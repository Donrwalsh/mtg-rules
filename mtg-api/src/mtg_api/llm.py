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
