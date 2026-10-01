from __future__ import annotations

from dataclasses import dataclass

from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

from mtg_api.card_matcher import CardMatcher
from mtg_api.config import Settings
from mtg_api.embedder import Embedder
from mtg_api.keyword_matcher import KeywordMatcher
from mtg_api.models import QueryResult
from mtg_api.sparse_embedder import SparseEmbedder, SparseVector


@dataclass
class RetrievalDeps:
    """What retrieval reads: the matchers, the embedders and Qdrant."""

    matcher: CardMatcher
    keyword_matcher: KeywordMatcher
    dense_embedder: Embedder
    sparse_embedder: SparseEmbedder
    client: QdrantClient


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
    source_type: str | None = None,
) -> list[tuple[str, float, dict]]:
    query_filter = None
    if source_type is not None:
        query_filter = qmodels.Filter(
            must=[
                qmodels.FieldCondition(
                    key="source_type", match=qmodels.MatchValue(value=source_type)
                )
            ]
        )
    dense_hits = client.query_points(
        collection_name=collection_name,
        using="dense",
        query=dense_vector,
        limit=per_branch_limit,
        with_payload=True,
        query_filter=query_filter,
    ).points
    sparse_hits = client.query_points(
        collection_name=collection_name,
        using="sparse",
        query=qmodels.SparseVector(indices=sparse_vector.indices, values=sparse_vector.values),
        limit=per_branch_limit,
        with_payload=True,
        query_filter=query_filter,
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


def fetch_card_rulings(
    client: QdrantClient,
    collection_name: str,
    oracle_ids: list[str],
    limit: int,
) -> list[tuple[str, dict]]:
    """Directly fetch the rulings belonging to the given oracle_ids -- an
    exact filter, not a similarity search. Hybrid vector search alone has no
    way to guarantee a named card's own rulings surface: the query text's
    embedding can easily be closer to unrelated cards that share words with
    the matched card's name."""
    if not oracle_ids:
        return []

    scroll_filter = qmodels.Filter(
        must=[
            qmodels.FieldCondition(key="source_type", match=qmodels.MatchValue(value="ruling")),
            qmodels.FieldCondition(key="oracle_id", match=qmodels.MatchAny(any=oracle_ids)),
        ]
    )
    points, _ = client.scroll(
        collection_name=collection_name,
        scroll_filter=scroll_filter,
        limit=limit,
        with_payload=True,
    )
    return [(str(p.id), p.payload) for p in points]


def retrieve(query: str, s: Settings, d: RetrievalDeps) -> list[QueryResult]:
    """Card-name, card-ruling, keyword-rule and hybrid vector matches, in
    context order."""
    card_results = [
        QueryResult(
            source="card",
            title=card["name"],
            text=card.get("oracle_text", ""),
            score=1.0,
            match_type="card_name_match",
            oracle_id=card.get("oracle_id"),
            card_name=card["name"],
            scryfall_uri=card.get("scryfall_uri"),
        )
        for card in d.matcher.find_matches(query)
    ]
    matched_oracle_ids = {r.oracle_id for r in card_results if r.oracle_id}

    card_ruling_hits = fetch_card_rulings(
        d.client, s.collection_name, list(matched_oracle_ids), s.card_ruling_limit
    )
    card_ruling_results = [
        QueryResult(
            source=payload.get("source_type", "unknown"),
            title=payload.get("card_name", ""),
            text=payload.get("text", ""),
            score=1.0,
            match_type="card_ruling_match",
            oracle_id=payload.get("oracle_id"),
            card_name=payload.get("card_name"),
            published_at=payload.get("published_at"),
            scryfall_uri=payload.get("scryfall_uri"),
        )
        for _point_id, payload in card_ruling_hits
    ]

    keyword_results = [
        QueryResult(
            source="rule",
            title=rule["rule_id"],
            text=rule["text"],
            score=1.0,
            match_type="keyword_rule_match",
            rule_id=rule["rule_id"],
        )
        for keyword in d.keyword_matcher.find_matches(query)
        for rule in keyword["rules"]
    ]
    matched_rule_ids = {r.title for r in keyword_results}

    dense_vector = d.dense_embedder.encode([query])[0]
    sparse_vector = d.sparse_embedder.encode([query])[0]
    hits = hybrid_search(
        d.client,
        s.collection_name,
        dense_vector,
        sparse_vector,
        s.hybrid_per_branch_limit,
        s.hybrid_dense_weight,
        s.hybrid_sparse_weight,
        s.hybrid_score_threshold,
        s.hybrid_top_k,
    )

    vector_results = []
    for point_id, score, payload in hits:
        oracle_id = payload.get("oracle_id")
        if oracle_id and oracle_id in matched_oracle_ids:
            continue
        if payload.get("rule_id") in matched_rule_ids:
            continue
        vector_results.append(
            QueryResult(
                source=payload.get("source_type", "unknown"),
                title=payload.get("card_name") or payload.get("rule_id", ""),
                text=payload.get("text", ""),
                score=score,
                match_type="vector_hit",
                oracle_id=oracle_id,
                rule_id=payload.get("rule_id"),
                card_name=payload.get("card_name"),
                published_at=payload.get("published_at"),
                scryfall_uri=payload.get("scryfall_uri"),
            )
        )

    rule_search_results = []
    if s.rules_top_k > 0:
        seen_rule_ids = matched_rule_ids | {r.rule_id for r in vector_results if r.rule_id}
        rule_hits = hybrid_search(
            d.client,
            s.collection_name,
            dense_vector,
            sparse_vector,
            s.hybrid_per_branch_limit,
            s.hybrid_dense_weight,
            s.hybrid_sparse_weight,
            s.hybrid_score_threshold,
            s.rules_top_k,
            source_type="rule",
        )
        for _point_id, score, payload in rule_hits:
            if payload.get("rule_id") in seen_rule_ids:
                continue
            rule_search_results.append(
                QueryResult(
                    source="rule",
                    title=payload.get("rule_id", ""),
                    text=payload.get("text", ""),
                    score=score,
                    match_type="rule_vector_hit",
                    rule_id=payload.get("rule_id"),
                )
            )

    return (
        card_results + card_ruling_results + keyword_results + rule_search_results + vector_results
    )
