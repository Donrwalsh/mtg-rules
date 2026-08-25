from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence


@dataclass
class SparseVector:
    indices: list[int]
    values: list[float]


class SparseEncoderModel(Protocol):
    def embed(self, texts: Sequence[str]) -> Sequence[object]: ...


class SparseEmbedder:
    """Thin wrapper around a fastembed-shaped sparse model.

    Takes the model as a constructor argument rather than loading it
    itself, so tests can inject a fake and never touch the real model.
    """

    def __init__(self, model: SparseEncoderModel):
        self._model = model

    def encode(self, texts: list[str]) -> list[SparseVector]:
        if not texts:
            return []
        return [
            SparseVector(indices=list(e.indices), values=list(e.values))
            for e in self._model.embed(texts)
        ]


def load_bm25_sparse_embedder(model_name: str) -> SparseEmbedder:
    """Real-model factory. Imports fastembed lazily so importing this
    module never requires that dependency unless this factory is called."""
    from fastembed import SparseTextEmbedding

    return SparseEmbedder(SparseTextEmbedding(model_name=model_name))
