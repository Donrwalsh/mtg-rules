from __future__ import annotations

from mtg_api.models import QueryResult

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


class GeminiAnswerer:
    """Answerer via the Gemini API's generateContent."""

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str = "https://generativelanguage.googleapis.com",
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout: float = 60.0,
    ):
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._timeout = timeout

    @property
    def model(self) -> str:
        return self._model

    def generate(self, query: str, context: str) -> str:
        import httpx

        body: dict = {
            "systemInstruction": {"parts": [{"text": _SYSTEM_PROMPT}]},
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": f"Context:\n{context}\n\nQuestion: {query}"}],
                }
            ],
        }
        config: dict = {}
        if self._temperature is not None:
            config["temperature"] = self._temperature
        if self._max_tokens is not None:
            # Gemini counts thinking tokens against this limit too.
            config["maxOutputTokens"] = self._max_tokens
        if config:
            body["generationConfig"] = config
        response = httpx.post(
            f"{self._base_url}/v1beta/models/{self._model}:generateContent",
            headers={"x-goog-api-key": self._api_key},
            json=body,
            timeout=self._timeout,
        )
        response.raise_for_status()
        data = response.json()
        candidates = data.get("candidates") or []
        if not candidates:
            # A blocked prompt comes back 200 with no candidates.
            reason = data.get("promptFeedback", {}).get("blockReason", "no candidates")
            raise RuntimeError(f"Gemini returned no answer: {reason}")
        parts = candidates[0].get("content", {}).get("parts", [])
        # Skip thought-summary parts; keep only the answer text.
        return "".join(p.get("text", "") for p in parts if not p.get("thought"))
