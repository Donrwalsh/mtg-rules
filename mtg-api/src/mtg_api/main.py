from __future__ import annotations

import hashlib
import logging
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path
from typing import Any

from celery import Celery
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import TypeAdapter, ValidationError
from qdrant_client import QdrantClient
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from mtg_api.card_matcher import CardMatcher, load_card_matcher, ordinary_words
from mtg_api.celery_client import get_celery_client
from mtg_api.citations import cite_answer
from mtg_api.config import GENERATION_SETTINGS, OVERRIDABLE_SETTINGS, Settings, settings
from mtg_api.embedder import Embedder, load_sentence_transformer_embedder
from mtg_api.history import list_history, save_history
from mtg_api.keyword_matcher import KeywordMatcher
from mtg_api.llm import (
    PROMPT_VERSION,
    GroqAnswerer,
    OllamaAnswerer,
    build_context,
    load_groq_answerer,
)
from mtg_api.models import (
    CitationStats,
    EmbedRequest,
    QueryRequest,
    QueryResponse,
    QueryResult,
)
from mtg_api.qdrant_check import check_qdrant
from mtg_api.retrieval import fetch_card_rulings, hybrid_search
from mtg_api.rules_index import RulesIndex, load_rules_index
from mtg_api.sparse_embedder import SparseEmbedder, load_bm25_sparse_embedder

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
    return load_sentence_transformer_embedder(settings.dense_model_name, batch_size=1)


@lru_cache(maxsize=1)
def get_sparse_embedder() -> SparseEmbedder:
    return load_bm25_sparse_embedder(settings.sparse_model_name)


@lru_cache(maxsize=1)
def get_db_engine() -> Engine:
    return create_engine(settings.postgres_dsn)


def build_answerer(s: Settings) -> OllamaAnswerer:
    return OllamaAnswerer(
        s.ollama_url,
        s.ollama_model,
        temperature=s.generation_temperature,
        max_tokens=s.generation_max_tokens,
    )


@lru_cache(maxsize=1)
def get_groq_answerer() -> GroqAnswerer | OllamaAnswerer:
    # Local trial: route answers to Ollama instead of Groq.
    return build_answerer(settings)


def resolve_settings(overrides: dict[str, Any]) -> Settings:
    """The settings one request runs with: the global settings, or a copy
    with this request's validated overrides applied. Never mutates the
    global object, so an override cannot leak into the next request."""
    if not overrides:
        return settings
    if not settings.eval_mode:
        raise HTTPException(
            status_code=403, detail="overrides are only accepted when MTG_API_EVAL_MODE is on"
        )
    unknown = sorted(set(overrides) - set(OVERRIDABLE_SETTINGS))
    if unknown:
        raise HTTPException(status_code=422, detail=f"unknown override keys: {', '.join(unknown)}")
    # model_copy(update=) does no validation, so coerce each value against
    # its field's type first.
    validated = {}
    for key, value in overrides.items():
        try:
            validated[key] = TypeAdapter(Settings.model_fields[key].annotation).validate_python(
                value
            )
        except ValidationError as exc:
            raise HTTPException(
                status_code=422, detail=f"invalid override {key}: {exc.errors()[0]['msg']}"
            ) from exc
    return settings.model_copy(update=validated)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Warm the rules index, the card and keyword automatons, and both models
    # at container startup, not on the first request -- moves the ~20s cold-load cost
    # from the first query to `docker compose up` instead.
    get_rules_index()
    get_card_matcher()
    get_keyword_matcher()
    get_dense_embedder()
    get_sparse_embedder()
    get_groq_answerer()
    yield


app = FastAPI(title="mtg-api", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/health")
def health(client: QdrantClient = Depends(get_qdrant_client)) -> dict:
    qdrant_status = "ok" if check_qdrant(client) else "unreachable"
    return {"status": "ok", "qdrant": qdrant_status}


@app.post("/api/v1/query", response_model=QueryResponse)
def query(
    request: QueryRequest,
    matcher: CardMatcher = Depends(get_card_matcher),
    keyword_matcher: KeywordMatcher = Depends(get_keyword_matcher),
    dense_embedder: Embedder = Depends(get_dense_embedder),
    sparse_embedder: SparseEmbedder = Depends(get_sparse_embedder),
    client: QdrantClient = Depends(get_qdrant_client),
    answerer: GroqAnswerer = Depends(get_groq_answerer),
    engine: Engine = Depends(get_db_engine),
    rules_index: RulesIndex = Depends(get_rules_index),
) -> QueryResponse:
    s = resolve_settings(request.overrides)
    if GENERATION_SETTINGS & request.overrides.keys():
        answerer = build_answerer(s)

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
        for card in matcher.find_matches(request.query)
    ]
    matched_oracle_ids = {r.oracle_id for r in card_results if r.oracle_id}

    card_ruling_hits = fetch_card_rulings(
        client, s.collection_name, list(matched_oracle_ids), s.card_ruling_limit
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
        for keyword in keyword_matcher.find_matches(request.query)
        for rule in keyword["rules"]
    ]
    matched_rule_ids = {r.title for r in keyword_results}

    dense_vector = dense_embedder.encode([request.query])[0]
    sparse_vector = sparse_embedder.encode([request.query])[0]
    hits = hybrid_search(
        client,
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

    all_results = card_results + card_ruling_results + keyword_results + vector_results
    context, sources = build_context(all_results)
    answer = None
    error = None
    if request.generate:
        try:
            answer = answerer.generate(request.query, context)
        except Exception as exc:
            logger.exception("Groq answer generation failed")
            error = str(exc)

    # Must run before the results are dumped for history: it sets each
    # cited result's `cited` flag.
    cited = cite_answer(answer, sources, rules_index) if answer is not None else None
    if cited is not None:
        answer = cited.answer
    citations = cited.citations if cited else []
    rule_references = cited.rule_references if cited else []
    citation_stats = cited.stats if cited else CitationStats()

    # Eval runs are not user queries: keep them out of history entirely.
    if request.source != "eval":
        try:
            save_history(
                engine,
                query=request.query,
                answer=answer,
                results=[r.model_dump() for r in all_results],
                model=s.ollama_model,
                error=error,
                citations=[c.model_dump() for c in citations],
                citation_stats=citation_stats.model_dump(),
                rule_references=rule_references,
            )
        except Exception:
            logger.exception("Failed to persist query history")

    eval_fields = {}
    if settings.eval_mode:
        eval_fields = {
            "context_hash": hashlib.sha256(context.encode("utf-8")).hexdigest(),
            "prompt_version": PROMPT_VERSION,
            "generator": f"ollama:{s.ollama_model}",
        }

    return QueryResponse(
        query=request.query,
        results=all_results,
        answer=answer,
        citations=citations,
        rule_references=rule_references,
        citation_stats=citation_stats,
        **eval_fields,
    )


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


@app.get("/api/v1/queries")
def get_query_history(
    limit: int = 50,
    offset: int = 0,
    engine: Engine = Depends(get_db_engine),
) -> list[dict]:
    return list_history(engine, limit=limit, offset=offset)


# What GET /api/v1/config may show. An explicit allowlist, never a dump of
# Settings, so a secret (groq_api_key, the password in postgres_dsn) can
# never leak through a newly added setting.
CONFIG_EXPOSED_SETTINGS = OVERRIDABLE_SETTINGS + (
    "dense_model_name",
    "sparse_model_name",
    "ollama_url",
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
    except Exception as exc:
        collection["points_count"] = None
        collection["error"] = str(exc)
    return {
        "settings": {key: getattr(settings, key) for key in CONFIG_EXPOSED_SETTINGS},
        "generator": f"ollama:{settings.ollama_model}",
        "prompt_version": PROMPT_VERSION,
        "collection": collection,
        "data_files": {
            kind: _latest_name(f"{kind}_*.jsonl") for kind in ("rules", "cards", "rulings")
        },
    }


def _rule_summary(rule: dict) -> dict:
    return {"rule_id": rule["rule_id"], "text": rule["text"]}


@app.get("/api/v1/rules/{rule_id}")
def get_rule(rule_id: str, rules_index: RulesIndex = Depends(get_rules_index)) -> dict:
    # Tolerate how people type rule numbers: "702.11B", "702.11b." in prose.
    normalized = rule_id.strip().rstrip(".").lower()
    rule = rules_index.get(normalized)
    if rule is None:
        raise HTTPException(status_code=404, detail="Rule not found")
    return {
        **_rule_summary(rule),
        "ancestors": [_rule_summary(r) for r in rules_index.ancestors(normalized)],
        "subrules": [_rule_summary(r) for r in rules_index.children(normalized)],
        "rules_ingested_at": rules_index.ingested_at,
    }
