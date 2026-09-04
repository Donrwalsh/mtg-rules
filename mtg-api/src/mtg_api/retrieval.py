from __future__ import annotations

from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

from mtg_api.sparse_embedder import SparseVector


def _normalize(scores: dict[str, float]) -> dict[str, float]:
    if not scores:
        return {}
    values = list(scores.values())
    lo, hi = min(values), max(values)
    if hi == lo:
        return {k: 1.0 for k in scores}
    return {k: (v - lo) / (hi - lo) for k, v in scores.items()}


def hybrid_search(
    client: QdrantClient,
    collection_name: str,
    dense_vector: list[float],
    sparse_vector: SparseVector,
    per_branch_limit: int,
    dense_weight: float,
    sparse_weight: float,
    score_threshold: float,
    top_k: int,
) -> list[tuple[str, float, dict]]:
    dense_hits = client.query_points(
        collection_name=collection_name,
        using="dense",
        query=dense_vector,
        limit=per_branch_limit,
        with_payload=True,
    ).points
    sparse_hits = client.query_points(
        collection_name=collection_name,
        using="sparse",
        query=qmodels.SparseVector(indices=sparse_vector.indices, values=sparse_vector.values),
        limit=per_branch_limit,
        with_payload=True,
    ).points

    dense_scores = {str(h.id): h.score for h in dense_hits}
    sparse_scores = {str(h.id): h.score for h in sparse_hits}
    payloads: dict[str, dict] = {}
    for h in dense_hits:
        payloads[str(h.id)] = h.payload
    for h in sparse_hits:
        payloads.setdefault(str(h.id), h.payload)

    dense_norm = _normalize(dense_scores)
    sparse_norm = _normalize(sparse_scores)

    combined = []
    for point_id in set(dense_scores) | set(sparse_scores):
        score = dense_weight * dense_norm.get(point_id, 0.0) + sparse_weight * sparse_norm.get(
            point_id, 0.0
        )
        if score >= score_threshold:
            combined.append((point_id, score, payloads[point_id]))

    combined.sort(key=lambda item: item[1], reverse=True)
    return combined[:top_k]
