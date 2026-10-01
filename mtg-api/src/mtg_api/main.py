from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

from celery import Celery
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from qdrant_client import QdrantClient
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from mtg_api import query_pipeline
from mtg_api.admin_auth import has_admin_marker, is_admin, require_admin
from mtg_api.admin_auth import router as auth_router
from mtg_api.answer_cache import (
    read_data_version,
)
from mtg_api.card_matcher import CardMatcher, load_card_matcher, ordinary_words
from mtg_api.celery_client import get_celery_client
from mtg_api.config import (
    OVERRIDABLE_SETTINGS,
    generator_label,
    settings,
)
from mtg_api.embedder import Embedder, load_fastembed_embedder
from mtg_api.history import get_history, list_history
from mtg_api.keyword_matcher import KeywordMatcher
from mtg_api.llm import (
    PROMPT_VERSION,
    Answerer,
    build_answerer,
)
from mtg_api.models import (
    EmbedRequest,
    QueryRequest,
    QueryResponse,
    ReplayResponse,
)
from mtg_api.qdrant_check import check_qdrant
from mtg_api.query_pipeline import AnswerStream, Caller, QueryDeps, Refused
from mtg_api.rules_index import RulesIndex, load_rules_index
from mtg_api.sparse_embedder import SparseEmbedder, load_bm25_sparse_embedder
from mtg_api.usage import (
    check_gating_config,
    ip_bucket,
    usage_summary,
)

logger = logging.getLogger(__name__)


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
    return load_card_matcher(cards_path, ordinary_words(get_rules_index().rules))


@lru_cache(maxsize=1)
def get_rules_index() -> RulesIndex:
    rules_path = _latest(settings.parsed_dir, "rules_*.jsonl")
    return load_rules_index(rules_path)


@lru_cache(maxsize=1)
def get_keyword_matcher() -> KeywordMatcher:
    return KeywordMatcher(get_rules_index().rules)


@lru_cache(maxsize=1)
def get_dense_embedder() -> Embedder:
    return load_fastembed_embedder(settings.dense_model_name, threads=settings.embed_threads)


@lru_cache(maxsize=1)
def get_sparse_embedder() -> SparseEmbedder:
    return load_bm25_sparse_embedder(settings.sparse_model_name, threads=settings.embed_threads)


@lru_cache(maxsize=1)
def get_db_engine() -> Engine:
    return create_engine(settings.postgres_dsn)


@lru_cache(maxsize=1)
def get_data_version() -> str:
    return read_data_version(settings.parsed_dir)


@lru_cache(maxsize=1)
def get_answerer() -> Answerer:
    return build_answerer(settings)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Refuse to serve with gating on but no prices: every answer would look free.
    check_gating_config(settings)
    # Warm the rules index, the card and keyword automatons, and both models
    # at container startup, not on the first request -- moves the ~20s cold-load cost
    # from the first query to `docker compose up` instead.
    get_rules_index()
    get_card_matcher()
    get_keyword_matcher()
    get_dense_embedder()
    get_sparse_embedder()
    get_answerer()
    get_data_version()
    yield


app = FastAPI(title="mtg-api", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(auth_router)


@app.get("/health")
def health(client: QdrantClient = Depends(get_qdrant_client)) -> dict:
    qdrant_status = "ok" if check_qdrant(client) else "unreachable"
    return {"status": "ok", "qdrant": qdrant_status}


def get_query_deps(
    matcher: CardMatcher = Depends(get_card_matcher),
    keyword_matcher: KeywordMatcher = Depends(get_keyword_matcher),
    dense_embedder: Embedder = Depends(get_dense_embedder),
    sparse_embedder: SparseEmbedder = Depends(get_sparse_embedder),
    client: QdrantClient = Depends(get_qdrant_client),
    answerer: Answerer = Depends(get_answerer),
    engine: Engine = Depends(get_db_engine),
    rules_index: RulesIndex = Depends(get_rules_index),
    data_version: str = Depends(get_data_version),
) -> QueryDeps:
    return QueryDeps(
        matcher,
        keyword_matcher,
        dense_embedder,
        sparse_embedder,
        client,
        answerer,
        engine,
        rules_index,
        data_version,
    )


def _caller(http_request: Request) -> Caller:
    admin = is_admin(http_request)
    host = http_request.client.host if http_request.client else "unknown"
    return Caller(
        ip_bucket=ip_bucket(host),
        is_admin=admin,
        may_refresh=admin and has_admin_marker(http_request),
    )


def _start(request: QueryRequest, http_request: Request, d: QueryDeps) -> AnswerStream:
    try:
        return query_pipeline.start(request, _caller(http_request), d)
    except Refused as refused:
        raise HTTPException(status_code=refused.status, detail=refused.detail) from refused


@app.post("/api/v1/query", response_model=QueryResponse)
def query(
    request: QueryRequest, http_request: Request, d: QueryDeps = Depends(get_query_deps)
) -> QueryResponse:
    return query_pipeline.collect(request.query, _start(request, http_request, d))


@app.post("/api/v1/query/stream")
def query_stream(
    request: QueryRequest, http_request: Request, d: QueryDeps = Depends(get_query_deps)
) -> StreamingResponse:
    # start() runs here, before any byte is sent, so a 422/403/429 is a
    # plain HTTP error.
    stream = _start(request, http_request, d)
    return StreamingResponse(
        query_pipeline.sse_frames(stream),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def require_task_endpoints() -> None:
    if not settings.task_endpoints:
        raise HTTPException(status_code=404, detail="Not Found")


@app.post("/api/v1/ingest", dependencies=[Depends(require_task_endpoints)])
def trigger_ingest(client: Celery = Depends(get_celery_client)) -> dict:
    result = client.send_task("mtg_worker.ingest")
    return {"task_id": result.id}


@app.post("/api/v1/embed", dependencies=[Depends(require_task_endpoints)])
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


@app.get("/api/v1/tasks/{task_id}", dependencies=[Depends(require_task_endpoints)])
def get_task_status(task_id: str, client: Celery = Depends(get_celery_client)) -> dict:
    result = client.AsyncResult(task_id)
    return {
        "task_id": task_id,
        "status": result.status,
        "result": result.result if result.ready() else None,
    }


@app.get("/api/v1/queries", dependencies=[Depends(require_admin)])
def get_query_history(
    limit: int = 50,
    offset: int = 0,
    engine: Engine = Depends(get_db_engine),
) -> list[dict]:
    return list_history(engine, limit=limit, offset=offset)


# Admin only: re-renders a past answer on the desk without asking again, so
# nothing here touches gating, usage, the answer cache or history.
@app.get(
    "/api/v1/queries/{history_id}",
    response_model=ReplayResponse,
    dependencies=[Depends(require_admin)],
)
def get_query_replay(history_id: int, engine: Engine = Depends(get_db_engine)) -> ReplayResponse:
    row = get_history(engine, history_id)
    if row is None or not row["answer"]:
        raise HTTPException(status_code=404, detail="No answer to replay")
    return ReplayResponse(
        id=row["id"],
        created_at=row["created_at"],
        query=row["query"],
        answer=row["answer"],
        results=row["results"],
        citations=row["citations"] or [],
        rule_references=row["rule_references"] or [],
        citation_stats=row["citation_stats"] or {},
        # No stored finish reason; a cut-off answer is the only row with
        # both an answer and an error.
        answer_complete=not row["error"],
    )


@app.get("/api/v1/admin/usage", dependencies=[Depends(require_admin)])
def get_usage(engine: Engine = Depends(get_db_engine)) -> dict:
    return usage_summary(engine, settings, datetime.now(UTC))


# What GET /api/v1/config may show. An explicit allowlist, never a dump of
# Settings, so a secret (the API keys, the password in postgres_dsn) can
# never leak through a newly added setting.
CONFIG_EXPOSED_SETTINGS = OVERRIDABLE_SETTINGS + (
    "dense_model_name",
    "sparse_model_name",
    # Which model wrote the answers, and how. Not ollama_url: it describes
    # the machine, not the result.
    "answer_provider",
    "ollama_model",
    "ollama_num_ctx",
    "ollama_seed",
    "ollama_num_predict_default",
    "ollama_timeout_seconds",
    "ollama_stream_chunk_timeout_seconds",
)


def _latest_name(pattern: str) -> str | None:
    try:
        return _latest(settings.parsed_dir, pattern).name
    except FileNotFoundError:
        return None


@app.get("/api/v1/config")
def get_config(client: QdrantClient = Depends(get_qdrant_client)) -> dict:
    if not settings.eval_mode:
        raise HTTPException(status_code=404, detail="Not Found")
    collection: dict = {"name": settings.collection_name}
    try:
        collection["points_count"] = client.count(
            collection_name=settings.collection_name, exact=True
        ).count
    except Exception as exc:  # noqa: BLE001 -- report any failure instead of failing the endpoint
        collection["points_count"] = None
        collection["error"] = str(exc)
    return {
        "settings": {key: getattr(settings, key) for key in CONFIG_EXPOSED_SETTINGS},
        "generator": generator_label(settings),
        "prompt_version": PROMPT_VERSION,
        "collection": collection,
        "data_files": {
            kind: _latest_name(f"{kind}_*.jsonl") for kind in ("rules", "cards", "rulings")
        },
    }


def _rule_summary(rule: dict) -> dict:
    return {"rule_id": rule["rule_id"], "text": rule["text"]}


@app.get("/api/v1/meta")
def get_meta(rules_index: RulesIndex = Depends(get_rules_index)) -> dict:
    """Public facts the UI states: the daily answer limit (null when not
    gated) and how current the rules are."""
    return {
        "answers_per_day": settings.ip_daily_llm_limit if settings.gating_enabled else None,
        "max_query_chars": settings.max_query_chars,
        "rules_as_of": rules_index.ingested_at,
    }


@app.get("/api/v1/rules")
def list_rules(rules_index: RulesIndex = Depends(get_rules_index)) -> dict:
    return {
        "sections": rules_index.table_of_contents(),
        "rules_as_of": rules_index.ingested_at,
    }


@app.get("/api/v1/rules/{rule_id}")
def get_rule(rule_id: str, rules_index: RulesIndex = Depends(get_rules_index)) -> dict:
    # Tolerate how people type rule numbers: "702.11B", "702.11b." in prose.
    normalized = rule_id.strip().rstrip(".").lower()
    rule = rules_index.get(normalized)
    if rule is None:
        raise HTTPException(status_code=404, detail="Rule not found")
    return {
        **_rule_summary(rule),
        "heading": rules_index.heading(normalized),
        "ancestors": [_rule_summary(r) for r in rules_index.ancestors(normalized)],
        "subrules": [_rule_summary(r) for r in rules_index.children(normalized)],
        "rules_ingested_at": rules_index.ingested_at,
    }
