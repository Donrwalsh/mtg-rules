from __future__ import annotations

from typing import Protocol, Sequence


class EncoderModel(Protocol):
    def encode(
        self, texts: Sequence[str], batch_size: int, show_progress_bar: bool
    ) -> list[list[float]]: ...

    def get_sentence_embedding_dimension(self) -> int: ...


class Embedder:
    """Thin wrapper around a sentence-transformers-shaped model.

    Takes the model as a constructor argument rather than loading it itself,
    so tests can inject a fake and never touch the real model or a network
    connection.
    """

    def __init__(self, model: EncoderModel, batch_size: int = 32):
        self._model = model
        self._batch_size = batch_size

    @property
    def vector_size(self) -> int:
        return self._model.get_sentence_embedding_dimension()

    def encode(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return self._model.encode(texts, batch_size=self._batch_size, show_progress_bar=False)


def load_sentence_transformer_embedder(model_name: str, batch_size: int) -> Embedder:
    """Real-model factory. Imports sentence_transformers lazily so importing
    this module (or anything that imports it) never requires that heavy
    dependency to be installed unless this factory is actually called."""
    from sentence_transformers import SentenceTransformer

    return Embedder(SentenceTransformer(model_name), batch_size=batch_size)
