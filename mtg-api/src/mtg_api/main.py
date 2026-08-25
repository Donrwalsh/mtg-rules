from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from celery import Celery
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from qdrant_client import QdrantClient

from mtg_api.card_matcher import CardMatcher, load_card_matcher
from mtg_api.celery_client import get_celery_client
from mtg_api.config import settings
from mtg_api.embedder import Embedder, load_sentence_transformer_embedder
from mtg_api.models import EmbedRequest, QueryRequest, QueryResponse, QueryResult
from mtg_api.qdrant_check import check_qdrant
from mtg_api.retrieval import hybrid_search
from mtg_api.sparse_embedder import SparseEmbedder, load_bm25_sparse_embedder

app = FastAPI(title="mtg-api")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


def get_qdrant_client() -> QdrantClient:
    return QdrantClient(host=settings.qdrant_host, port=settings.qdrant_port)


def _latest(directory: Path, pattern: str) -> Path:
    matches = sorted(directory.glob(pattern))
    if not matches:
        raise FileNotFoundError(f"No files matching {pattern!r} in {directory}")
    return matches[-1]


@lru_cache(maxsize=1)
def get_card_matcher() -> CardMatcher:
    cards_path = _latest(settings.parsed_dir, "cards_*.jsonl")
    return load_card_matcher(cards_path)


@lru_cache(maxsize=1)
def get_dense_embedder() -> Embedder:
    return load_sentence_transformer_embedder(settings.dense_model_name, batch_size=1)


@lru_cache(maxsize=1)
def get_sparse_embedder() -> SparseEmbedder:
    return load_bm25_sparse_embedder(settings.sparse_model_name)


@app.get("/health")
def health(client: QdrantClient = Depends(get_qdrant_client)) -> dict:
    qdrant_status = "ok" if check_qdrant(client) else "unreachable"
    return {"status": "ok", "qdrant": qdrant_status}


@app.post("/api/v1/query", response_model=QueryResponse)
def query(
    request: QueryRequest,
    matcher: CardMatcher = Depends(get_card_matcher),
    dense_embedder: Embedder = Depends(get_dense_embedder),
    sparse_embedder: SparseEmbedder = Depends(get_sparse_embedder),
    client: QdrantClient = Depends(get_qdrant_client),
) -> QueryResponse:
    card_results = [
        QueryResult(
            source="card",
            title=card["name"],
            text=card.get("oracle_text", ""),
            score=1.0,
            match_type="card_name_match",
            oracle_id=card.get("oracle_id"),
        )
        for card in matcher.find_matches(request.query)
    ]
    matched_oracle_ids = {r.oracle_id for r in card_results if r.oracle_id}

    dense_vector = dense_embedder.encode([request.query])[0]
    sparse_vector = sparse_embedder.encode([request.query])[0]
    hits = hybrid_search(
        client,
        settings.collection_name,
        dense_vector,
        sparse_vector,
        settings.hybrid_per_branch_limit,
        settings.hybrid_dense_weight,
        settings.hybrid_sparse_weight,
        settings.hybrid_score_threshold,
        settings.hybrid_top_k,
    )

    vector_results = []
    for point_id, score, payload in hits:
        oracle_id = payload.get("oracle_id")
        if oracle_id and oracle_id in matched_oracle_ids:
            continue
        vector_results.append(
            QueryResult(
                source=payload.get("source_type", "unknown"),
                title=payload.get("card_name") or payload.get("rule_id", ""),
                text=payload.get("text", ""),
                score=score,
                match_type="vector_hit",
                oracle_id=oracle_id,
            )
        )

    return QueryResponse(query=request.query, results=card_results + vector_results)


@app.post("/api/v1/ingest")
def trigger_ingest(client: Celery = Depends(get_celery_client)) -> dict:
    result = client.send_task("mtg_worker.ingest")
    return {"task_id": result.id}


@app.post("/api/v1/embed")
def trigger_embed(request: EmbedRequest, client: Celery = Depends(get_celery_client)) -> dict:
    if request.limit == "all":
        limit = None
    else:
        try:
            limit = int(request.limit)
        except ValueError:
            raise HTTPException(status_code=400, detail='limit must be "all" or a positive integer')
        if limit <= 0:
            raise HTTPException(status_code=400, detail='limit must be "all" or a positive integer')
    result = client.send_task("mtg_worker.embed", kwargs={"limit": limit})
    return {"task_id": result.id}


@app.get("/api/v1/tasks/{task_id}")
def get_task_status(task_id: str, client: Celery = Depends(get_celery_client)) -> dict:
    result = client.AsyncResult(task_id)
    return {
        "task_id": task_id,
        "status": result.status,
        "result": result.result if result.ready() else None,
    }
