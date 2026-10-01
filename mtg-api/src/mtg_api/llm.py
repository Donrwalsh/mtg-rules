from __future__ import annotations

import json
import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING

from mtg_api.models import QueryResult

if TYPE_CHECKING:
    import httpx

# Bump by hand whenever _SYSTEM_PROMPT or the user-message template changes:
# eval answer caches are keyed on it.
PROMPT_VERSION = 1

_SYSTEM_PROMPT = """You are a Magic: The Gathering rules assistant. Answer the user's \
question using only the numbered context sources below (Comprehensive Rules excerpts, \
official rulings, and card text).

Citation rules:
- Cite every factual claim with the bracketed number of the source that supports it, like [2].
- Use only numbers that appear in the context. Never cite a number that is not listed, \
and never quote rule numbers from memory.
- Several sources may support one claim: write [1][3] or [1, 3].
- When explaining why something works, prefer rules and rulings over card text.
- If the context does not cover the question, say so plainly, with no citations, \
instead of guessing.

Example (format only -- these sources are not part of your context):
Context:
[1] Rule 702.19b: The controller of an attacking creature with trample first assigns \
damage to the creature(s) blocking it. Once all those blocking creatures are assigned \
lethal damage, any excess damage is assigned as its controller chooses among those \
blocking creatures and the player, planeswalker, or battle the creature is attacking.
[2] Card — Colossal Dreadmaw: Trample
Question: My Colossal Dreadmaw is blocked by a 1/1. Does any damage get through?
Answer: Yes. Colossal Dreadmaw has trample [2], so you assign lethal damage to the \
blocker and the rest to the defending player [1]."""


def source_label(result: QueryResult) -> str:
    """Human-readable name for a source. Used for both the context block the
    LLM sees and the citation shown to the user, so the two always agree."""
    if result.source == "rule":
        return f"Rule {result.rule_id or result.title}"
    if result.source == "ruling":
        name = result.card_name or result.title
        if result.published_at:
            return f"Ruling — {name} ({result.published_at})"
        return f"Ruling — {name}"
    if result.source in ("card", "oracle"):
        return f"Card — {result.card_name or result.title}"
    return result.title


def build_context(results: list[QueryResult]) -> tuple[str, dict[int, QueryResult]]:
    """Number every result 1..N in order. The returned mapping is the only
    source of truth for what each [n] the LLM cites refers to."""
    sources = {number: result for number, result in enumerate(results, start=1)}
    blocks = [f"[{number}] {source_label(r)}: {r.text}" for number, r in sources.items()]
    return "\n\n".join(blocks), sources


def _user_message(query: str, context: str) -> str:
    return f"Context:\n{context}\n\nQuestion: {query}"


def prompt_chars(query: str, context: str) -> int:
    """Characters sent to Gemini: the basis for estimating input tokens."""
    return len(_SYSTEM_PROMPT) + len(_user_message(query, context))


@dataclass(frozen=True)
class Generation:
    """An answer and what it cost. Thinking tokens bill as output tokens."""

    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    thinking_tokens: int = 0
    # Gemini's candidates[0].finishReason: "STOP" for a complete answer,
    # "MAX_TOKENS" / "SAFETY" / "RECITATION" / ... for a truncated or
    # blocked one. None when the API didn't send one.
    finish_reason: str | None = None

    def usage(self) -> dict[str, int]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "thinking_tokens": self.thinking_tokens,
        }


@dataclass(frozen=True)
class StreamChunk:
    """One streamGenerateContent event: its answer text (thought parts left
    out) and whatever usage and finish reason it carried."""

    text: str = ""
    input_tokens: int | None = None
    output_tokens: int | None = None
    thinking_tokens: int | None = None
    finish_reason: str | None = None


class StreamAccumulator:
    """Collects a stream as it arrives. Counts keep the last value Gemini
    sent; None means Gemini never sent one."""

    def __init__(self) -> None:
        self._parts: list[str] = []
        self.received = False
        self.input_tokens: int | None = None
        self.output_tokens: int | None = None
        self.thinking_tokens: int | None = None
        self.finish_reason: str | None = None

    def add(self, chunk: StreamChunk) -> None:
        self.received = True
        self._parts.append(chunk.text)
        for field in ("input_tokens", "output_tokens", "thinking_tokens", "finish_reason"):
            value = getattr(chunk, field)
            if value is not None:
                setattr(self, field, value)

    @property
    def text(self) -> str:
        return "".join(self._parts)

    def generation(self) -> Generation:
        return Generation(
            text=self.text,
            input_tokens=self.input_tokens or 0,
            output_tokens=self.output_tokens or 0,
            thinking_tokens=self.thinking_tokens or 0,
            finish_reason=self.finish_reason,
        )


def _parse_chunk(data: dict) -> StreamChunk:
    candidates = data.get("candidates") or []
    feedback = data.get("promptFeedback") or {}
    if not candidates and feedback.get("blockReason"):
        # A blocked prompt comes back 200 with no candidates.
        raise RuntimeError(f"Gemini returned no answer: {feedback['blockReason']}")
    usage = data.get("usageMetadata") or {}
    candidate = candidates[0] if candidates else {}
    parts = candidate.get("content", {}).get("parts", [])
    return StreamChunk(
        # Skip thought-summary parts; keep only the answer text.
        text="".join(p.get("text", "") for p in parts if not p.get("thought")),
        input_tokens=usage.get("promptTokenCount"),
        output_tokens=usage.get("candidatesTokenCount"),
        thinking_tokens=usage.get("thoughtsTokenCount"),
        finish_reason=candidate.get("finishReason"),
    )


class GeminiAnswerer:
    """Answerer via the Gemini API's streamGenerateContent."""

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str = "https://generativelanguage.googleapis.com",
        temperature: float | None = None,
        max_tokens: int | None = None,
        thinking_level: str | None = None,
        timeout: float = 60.0,
        chunk_timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ):
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._thinking_level = thinking_level
        # Total time for one answer. chunk_timeout bounds each wait between
        # chunks, which includes the thinking before the first one.
        self._timeout = timeout
        self._chunk_timeout = chunk_timeout
        self._transport = transport

    @property
    def model(self) -> str:
        return self._model

    def _body(self, query: str, context: str) -> dict:
        body: dict = {
            "systemInstruction": {"parts": [{"text": _SYSTEM_PROMPT}]},
            "contents": [{"role": "user", "parts": [{"text": _user_message(query, context)}]}],
        }
        config: dict = {}
        if self._temperature is not None:
            config["temperature"] = self._temperature
        if self._max_tokens is not None:
            # Gemini counts thinking tokens against this limit too.
            config["maxOutputTokens"] = self._max_tokens
        if self._thinking_level is not None:
            config["thinkingConfig"] = {"thinkingLevel": self._thinking_level}
        if config:
            body["generationConfig"] = config
        return body

    def _client(self) -> httpx.Client:
        import httpx

        return httpx.Client(
            transport=self._transport, timeout=httpx.Timeout(self._chunk_timeout, connect=10.0)
        )

    def stream(self, query: str, context: str) -> Iterator[StreamChunk]:
        deadline = time.monotonic() + self._timeout
        with (
            self._client() as client,
            client.stream(
                "POST",
                f"{self._base_url}/v1beta/models/{self._model}:streamGenerateContent",
                params={"alt": "sse"},
                headers={"x-goog-api-key": self._api_key},
                json=self._body(query, context),
            ) as response,
        ):
            if response.is_error:
                response.read()  # so the raised error carries Gemini's message
            response.raise_for_status()
            for line in response.iter_lines():
                if time.monotonic() > deadline:
                    raise TimeoutError(f"Gemini answer took longer than {self._timeout:g}s")
                if line.startswith("data:"):
                    yield _parse_chunk(json.loads(line[5:]))

    def generate(self, query: str, context: str) -> Generation:
        acc = StreamAccumulator()
        for chunk in self.stream(query, context):
            acc.add(chunk)
        return acc.generation()
