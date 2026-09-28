from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol


@dataclass
class SparseVector:
    indices: list[int]
    values: list[float]


class SparseEncoderModel(Protocol):
    def embed(self, texts: Sequence[str]) -> Sequence[object]: ...


class SparseEmbedder:
    def __init__(self, model: SparseEncoderModel):
        self._model = model

    def encode(self, texts: list[str]) -> list[SparseVector]:
        if not texts:
            return []
        return [
            SparseVector(indices=list(e.indices), values=list(e.values))
            for e in self._model.embed(texts)
        ]


def load_bm25_sparse_embedder(model_name: str, threads: int | None = None) -> SparseEmbedder:
    """Real-model factory. Imports fastembed lazily so importing this
    module never requires that dependency unless this factory is called."""
    import fastembed

    return SparseEmbedder(fastembed.SparseTextEmbedding(model_name=model_name, threads=threads))
