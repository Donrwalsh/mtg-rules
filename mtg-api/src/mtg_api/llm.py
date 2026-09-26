from __future__ import annotations

from mtg_api.models import QueryResult

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


class GroqAnswerer:
    def __init__(self, client, model: str):
        self._client = client
        self._model = model

    def generate(self, query: str, context: str) -> str:
        response = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": f"Context:\n{context}\n\nQuestion: {query}"},
            ],
        )
        return response.choices[0].message.content


def load_groq_answerer(api_key: str, model: str) -> GroqAnswerer:
    """Real-client factory. Imports groq lazily so importing this module
    never requires that dependency unless this factory is actually called."""
    from groq import Groq

    return GroqAnswerer(Groq(api_key=api_key), model)


class OllamaAnswerer:
    """Local-model answerer via Ollama's /api/chat. Same generate()
    interface as GroqAnswerer so it drops into the query endpoint."""

    def __init__(self, base_url: str, model: str, num_ctx: int = 16384):
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._num_ctx = num_ctx

    def generate(self, query: str, context: str) -> str:
        import httpx

        response = httpx.post(
            f"{self._base_url}/api/chat",
            json={
                "model": self._model,
                "stream": False,
                # Ollama's default context window is small enough to
                # silently truncate our retrieved context.
                "options": {"num_ctx": self._num_ctx},
                "messages": [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": f"Context:\n{context}\n\nQuestion: {query}"},
                ],
            },
            timeout=300.0,
        )
        response.raise_for_status()
        return response.json()["message"]["content"]
