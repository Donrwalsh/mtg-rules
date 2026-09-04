from __future__ import annotations

from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

from mtg_embed.models import EmbeddableChunk
from mtg_embed.sparse_embedder import SparseVector


class QdrantStore:
    def __init__(self, client: QdrantClient, collection_name: str):
        self._client = client
        self._collection_name = collection_name

    def ensure_collection(self, dense_size: int) -> None:
        existing = [c.name for c in self._client.get_collections().collections]
        if self._collection_name in existing:
            return
        self._client.create_collection(
            collection_name=self._collection_name,
            vectors_config={
                "dense": qmodels.VectorParams(size=dense_size, distance=qmodels.Distance.COSINE)
            },
            sparse_vectors_config={"sparse": qmodels.SparseVectorParams()},
        )

    def existing_hashes(self, point_ids: list[str]) -> dict[str, str]:
        if not point_ids:
            return {}
        points = self._client.retrieve(
            collection_name=self._collection_name,
            ids=point_ids,
            with_payload=["content_hash"],
        )
        return {
            str(p.id): p.payload["content_hash"]
            for p in points
            if p.payload and "content_hash" in p.payload
        }

    def upsert(
        self,
        chunks: list[EmbeddableChunk],
        dense_vectors: list[list[float]],
        sparse_vectors: list[SparseVector],
    ) -> None:
        if not chunks:
            return
        points = [
            qmodels.PointStruct(
                id=chunk.point_id,
                vector={
                    "dense": dense_vector,
                    "sparse": qmodels.SparseVector(
                        indices=sparse_vector.indices, values=sparse_vector.values
                    ),
                },
                payload=chunk.payload,
            )
            for chunk, dense_vector, sparse_vector in zip(chunks, dense_vectors, sparse_vectors)
        ]
        self._client.upsert(collection_name=self._collection_name, points=points)
