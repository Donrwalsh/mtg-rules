from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol


class EncoderModel(Protocol):
    def encode(
        self, texts: Sequence[str], batch_size: int, show_progress_bar: bool
    ) -> list[list[float]]: ...

    def get_sentence_embedding_dimension(self) -> int: ...


class Embedder:
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


class FastEmbedModel:
    """Adapts a fastembed TextEmbedding (ONNX Runtime, no torch) to
    EncoderModel. Its bge-base vectors match sentence-transformers' ones
    (the worker indexes with those) to cosine 1.0000."""

    def __init__(self, model):
        self._model = model

    def encode(
        self, texts: Sequence[str], batch_size: int, show_progress_bar: bool = False
    ) -> list[list[float]]:
        return [vector.tolist() for vector in self._model.embed(list(texts), batch_size=batch_size)]

    def get_sentence_embedding_dimension(self) -> int:
        return self._model.embedding_size


def load_fastembed_embedder(
    model_name: str, batch_size: int = 1, threads: int | None = None
) -> Embedder:
    """Real-model factory. Imports fastembed lazily so importing this
    module never requires that dependency unless this factory is called.
    threads=None lets ONNX Runtime use every core."""
    import fastembed

    model = fastembed.TextEmbedding(model_name=model_name, threads=threads)
    return Embedder(FastEmbedModel(model), batch_size=batch_size)
