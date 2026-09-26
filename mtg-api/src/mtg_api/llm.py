from __future__ import annotations

from mtg_api.models import QueryResult

_SYSTEM_PROMPT = (
    "You are a Magic: The Gathering rules assistant. Answer the user's "
    "question using only the context below (card text, rulings, and "
    "Comprehensive Rules excerpts). If the context does not cover the "
    "question, say so plainly instead of guessing."
)


def build_context(results: list[QueryResult]) -> str:
    blocks = [f"[{r.source}] {r.title}\n{r.text}" for r in results]
    return "\n\n".join(blocks)


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
