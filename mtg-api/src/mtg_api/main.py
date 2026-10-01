from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from celery import Celery
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import TypeAdapter, ValidationError
from qdrant_client import QdrantClient
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from mtg_api.admin_auth import has_admin_marker, is_admin, require_admin
from mtg_api.admin_auth import router as auth_router
from mtg_api.answer_cache import (
    cache_key,
    get_cached,
    normalize_query,
    put_cached,
    read_data_version,
)
from mtg_api.card_matcher import CardMatcher, load_card_matcher, ordinary_words
from mtg_api.celery_client import get_celery_client
from mtg_api.citations import citation_for, cite_answer
from mtg_api.config import (
    GENERATION_SETTINGS,
    OVERRIDABLE_SETTINGS,
    Settings,
    answer_model,
    generator_label,
    settings,
)
from mtg_api.embedder import Embedder, load_fastembed_embedder
from mtg_api.enrich import enrich_citations, enrich_results
from mtg_api.history import get_history, list_history, save_history
from mtg_api.keyword_matcher import KeywordMatcher
from mtg_api.llm import (
    PROMPT_VERSION,
    Answerer,
    Generation,
    StreamAccumulator,
    build_answerer,
    build_context,
    prompt_chars,
)
from mtg_api.models import (
    Citation,
    CitationStats,
    EmbedRequest,
    QueryRequest,
    QueryResponse,
    QueryResult,
    ReplayResponse,
    StreamDone,
    StreamHead,
)
from mtg_api.qdrant_check import check_qdrant
from mtg_api.retrieval import fetch_card_rulings, hybrid_search
from mtg_api.rules_index import RulesIndex, load_rules_index
from mtg_api.sparse_embedder import SparseEmbedder, load_bm25_sparse_embedder
from mtg_api.streaming import AnswerJob, Emit, GenerationSlots, sse_event
from mtg_api.usage import (
    Gate,
    check_gate,
    check_gating_config,
    cost_usd,
    estimate_generation,
    finalize_usage,
    ip_bucket,
    record_usage,
    reserve_usage,
    usage_summary,
    worst_case_cost,
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


_DEGRADED_OUTCOMES = {"ip_quota": "degraded_ip", "global_budget": "degraded_global"}
# Fields that describe one request, not the cached answer.
_PER_REQUEST_FIELDS = {
    "query",
    "cached_at",
    "degraded",
    "answers_remaining",
    "context_hash",
    "prompt_version",
    "generator",
    "usage",
    "generation_error",
}


def _gate(engine: Engine, s: Settings, bucket: str, now: datetime) -> Gate:
    try:
        return check_gate(engine, s, bucket, now)
    except Exception:
        # Fail closed: without the usage table there's no way to know the spend.
        logger.exception("Usage gate unavailable; answering retrieval-only")
        return Gate("global_budget", 0)


def _record(engine: Engine, **fields) -> None:
    try:
        record_usage(engine, **fields)
    except Exception:
        logger.exception("Failed to record LLM usage")


def _cache_get(engine: Engine, key: str) -> tuple[dict, datetime] | None:
    try:
        return get_cached(engine, key)
    except Exception:
        logger.exception("Answer cache lookup failed")
        return None


def _cache_put(engine: Engine, key: str, query: str, response: QueryResponse, now: datetime):
    try:
        put_cached(
            engine,
            key,
            normalized_query=normalize_query(query),
            response=response.model_dump(mode="json", exclude=_PER_REQUEST_FIELDS),
            now=now,
        )
    except Exception:
        logger.exception("Failed to store answer in cache")


def _save(engine: Engine, s: Settings, request: QueryRequest, **fields) -> None:
    # Eval runs are not user queries, but only eval mode may say so.
    if settings.eval_mode and request.source == "eval":
        return
    try:
        save_history(engine, query=request.query, model=answer_model(s), **fields)
    except Exception:
        logger.exception("Failed to persist query history")


generation_slots = GenerationSlots()


@dataclass
class QueryDeps:
    matcher: CardMatcher
    keyword_matcher: KeywordMatcher
    dense_embedder: Embedder
    sparse_embedder: SparseEmbedder
    client: QdrantClient
    answerer: Answerer
    engine: Engine
    rules_index: RulesIndex
    data_version: str


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


@dataclass
class _Started:
    """Phase 1's result: the `results` event, and the events after it
    (always ending with `done`)."""

    head: dict
    rest: Iterator[tuple[str, dict]]


def _head(response: QueryResponse, sources: list[Citation]) -> dict:
    return StreamHead.of(response, sources).model_dump(mode="json")


def _done(response: QueryResponse) -> dict:
    return StreamDone.of(response).model_dump(mode="json")


def _retrieve(query: str, s: Settings, d: QueryDeps) -> list[QueryResult]:
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


@dataclass
class _AnswerWork:
    """What the worker thread needs to write, record, cache and save one
    answer, whether or not anyone is still listening."""

    s: Settings
    request: QueryRequest
    answerer: Answerer
    d: QueryDeps
    context: str
    sources: dict[int, QueryResult]
    results: list[QueryResult]
    record: dict  # now / ip_bucket / is_admin / model for the usage row
    tracked: bool
    reservation: int | None
    key: str | None
    answers_remaining: int | None
    eval_fields: dict
    release: Callable[[], None] | None


def _finalize(work: _AnswerWork, outcome: str, generation: Generation) -> None:
    if not work.tracked:
        return
    cost = cost_usd(generation, work.s)
    if work.reservation is None:
        # The reservation insert failed; record the answer as before.
        _record(work.d.engine, outcome=outcome, generation=generation, cost=cost, **work.record)
        return
    try:
        finalize_usage(
            work.d.engine, work.reservation, outcome=outcome, generation=generation, cost=cost
        )
    except Exception:
        logger.exception("Failed to finalize LLM usage")


def _run_answer(work: _AnswerWork, emit: Emit) -> None:
    """Phase 2, on the job's thread: stream the answer out as deltas, then
    do everything the old endpoint did after generating."""
    s, d = work.s, work.d
    acc = StreamAccumulator()
    failure = None
    try:
        emit("thinking", {})
        try:
            for chunk in work.answerer.stream(work.request.query, work.context):
                acc.add(chunk)
                if chunk.text:
                    emit("delta", {"text": chunk.text})
        except Exception as exc:
            logger.exception("Answer generation failed (%s)", generator_label(s))
            failure = str(exc)

        generation = estimate_generation(acc, prompt_chars(work.request.query, work.context), s)
        _finalize(work, "error" if failure else "generated", generation)

        # Keep whatever was written, even when the stream failed after it.
        answer = acc.text if (acc.text or failure is None) else None
        complete = failure is None and acc.finish_reason == "STOP"
        error = failure
        if answer and not complete and error is None:
            error = f"answer cut off (finish reason: {acc.finish_reason or 'none'})"

        # Must run before the results are dumped: it sets each cited
        # result's `cited` flag.
        cited = cite_answer(answer, work.sources, d.rules_index) if answer is not None else None
        if cited is not None:
            answer = cited.answer
        citations = cited.citations if cited else []
        enrich_citations(citations, d.matcher, d.rules_index)
        rule_references = cited.rule_references if cited else []
        citation_stats = cited.stats if cited else CitationStats()

        _save(
            d.engine,
            s,
            work.request,
            answer=answer,
            results=[r.model_dump() for r in work.results],
            error=error,
            citations=[c.model_dump() for c in citations],
            citation_stats=citation_stats.model_dump(),
            rule_references=rule_references,
        )

        eval_fields = dict(work.eval_fields)
        if settings.eval_mode:
            eval_fields["usage"] = None if failure else generation.usage()
            eval_fields["generation_error"] = error
        response = QueryResponse(
            query=work.request.query,
            results=work.results,
            answer=answer,
            citations=citations,
            rule_references=rule_references,
            citation_stats=citation_stats,
            answer_complete=None if answer is None else complete,
            answers_remaining=work.answers_remaining,
            **eval_fields,
        )
        if work.key and answer and complete:
            _cache_put(d.engine, work.key, work.request.query, response, work.record["now"])
        if failure and answer is None:
            emit("error", {"message": failure})
        emit("done", _done(response))
    finally:
        if work.release:
            work.release()


def _reserve(engine: Engine, record: dict, cost: float) -> int | None:
    try:
        return reserve_usage(engine, cost=cost, **record)
    except Exception:
        logger.exception("Failed to reserve LLM usage")
        return None


def _start_query(request: QueryRequest, http_request: Request, d: QueryDeps) -> _Started:
    """Phase 1, in the request thread: everything that can still refuse the
    request with an HTTP status, then retrieval. Starts the answer job when
    there is an answer to write."""
    s = resolve_settings(request.overrides)
    answerer = d.answerer
    if GENERATION_SETTINGS & request.overrides.keys():
        answerer = build_answerer(s)

    if len(request.query) > s.max_query_chars:
        raise HTTPException(
            status_code=422, detail=f"query is longer than {s.max_query_chars} characters"
        )
    admin = is_admin(http_request)
    if request.fresh and not (admin and has_admin_marker(http_request)):
        raise HTTPException(status_code=403, detail="fresh answers are admin-only")

    engine = d.engine
    now = datetime.now(UTC)
    bucket = ip_bucket(http_request.client.host if http_request.client else "unknown")
    tracked = request.generate and not settings.eval_mode
    record = {"now": now, "ip_bucket": bucket, "is_admin": admin, "model": answer_model(s)}

    gate = Gate(None, 0)
    remaining = None
    if tracked and s.gating_enabled and not admin:
        gate = _gate(engine, s, bucket, now)
        remaining = gate.answers_remaining

    key = (
        cache_key(request.query, s, d.data_version) if tracked and s.answer_cache_enabled else None
    )
    hit = _cache_get(engine, key) if key and not request.fresh else None
    if hit is not None:
        stored, generated_at = hit
        # The global budget being exhausted applies to the next new
        # question too, so don't promise answers a cache hit didn't use.
        cache_remaining = 0 if gate.degraded == "global_budget" else remaining
        try:
            cached_response = QueryResponse(
                **stored,
                query=request.query,
                cached_at=generated_at,
                answers_remaining=cache_remaining,
            )
        except Exception:
            # A row from an older schema (or otherwise malformed): fall
            # through to a normal retrieval + generation below, which
            # overwrites this entry via _cache_put.
            logger.exception("Malformed answer-cache row for key %s; treating as a miss", key)
        else:
            # Only complete answers are cached; rows from before the field.
            if cached_response.answer_complete is None and cached_response.answer:
                cached_response.answer_complete = True
            # Rows cached before enrichment existed lack card/heading.
            enrich_results(cached_response.results, d.matcher, d.rules_index)
            enrich_citations(cached_response.citations, d.matcher, d.rules_index)
            _record(engine, outcome="cached", **record)
            _save(
                engine,
                s,
                request,
                answer=cached_response.answer,
                results=[r.model_dump() for r in cached_response.results],
                error=None,
                citations=[c.model_dump() for c in cached_response.citations],
                citation_stats=cached_response.citation_stats.model_dump(),
                rule_references=cached_response.rule_references,
                cached=True,
            )
            return _Started(_head(cached_response, []), iter([("done", _done(cached_response))]))

    generating = request.generate and gate.degraded is None
    release = None
    if generating and not settings.eval_mode:
        release = generation_slots.acquire(
            bucket,
            total_limit=s.max_concurrent_generations,
            ip_limit=None if admin else s.max_concurrent_generations_per_ip,
        )
        if release is None:
            raise HTTPException(
                status_code=429, detail="Too many answers in progress. Try again in a moment."
            )

    try:
        all_results = _retrieve(request.query, s, d)
        context, sources = build_context(all_results)
        # After build_context: display data must never reach the LLM.
        enrich_results(all_results, d.matcher, d.rules_index)

        eval_fields = {}
        if settings.eval_mode:
            eval_fields = {
                "context_hash": hashlib.sha256(context.encode("utf-8")).hexdigest(),
                "prompt_version": PROMPT_VERSION,
                "generator": generator_label(s),
            }

        if tracked and gate.degraded:
            _record(engine, outcome=_DEGRADED_OUTCOMES[gate.degraded], **record)
        if generating and tracked and remaining is not None:
            remaining = max(0, remaining - 1)

        head_response = QueryResponse(
            query=request.query,
            results=all_results,
            degraded=gate.degraded,
            answers_remaining=remaining,
            **eval_fields,
        )
        if not generating:
            _save(
                engine,
                s,
                request,
                answer=None,
                results=[r.model_dump() for r in all_results],
                error=None,
                citations=[],
                citation_stats=CitationStats().model_dump(),
                rule_references=[],
            )
            return _Started(_head(head_response, []), iter([("done", _done(head_response))]))

        live_sources = [citation_for(n, r) for n, r in sources.items()]
        enrich_citations(live_sources, d.matcher, d.rules_index)
        reservation = (
            _reserve(engine, record, worst_case_cost(prompt_chars(request.query, context), s))
            if tracked
            else None
        )
        work = _AnswerWork(
            s=s,
            request=request,
            answerer=answerer,
            d=d,
            context=context,
            sources=sources,
            results=all_results,
            record=record,
            tracked=tracked,
            reservation=reservation,
            key=key,
            answers_remaining=remaining,
            eval_fields=eval_fields,
            release=release,
        )
        job = AnswerJob(lambda emit: _run_answer(work, emit)).start()
    except BaseException:
        if release:
            release()
        raise
    return _Started(_head(head_response, live_sources), job.events())


@app.post("/api/v1/query", response_model=QueryResponse)
def query(
    request: QueryRequest, http_request: Request, d: QueryDeps = Depends(get_query_deps)
) -> QueryResponse:
    started = _start_query(request, http_request, d)
    fields = {k: v for k, v in started.head.items() if k != "sources"}
    for name, data in started.rest:
        if name == "done":
            fields.update(data)
    return QueryResponse(query=request.query, **fields)


def _sse(started: _Started) -> Iterator[str]:
    """Relays the answer job as SSE. Closing this (the client left) only
    stops the relay; the job finishes on its own thread."""
    yield sse_event("results", started.head)
    for name, data in started.rest:
        yield sse_event(name, data)


@app.post("/api/v1/query/stream")
def query_stream(
    request: QueryRequest, http_request: Request, d: QueryDeps = Depends(get_query_deps)
) -> StreamingResponse:
    # Phase 1 runs here, before any byte is sent, so a 422/403/429 is a
    # plain HTTP error.
    started = _start_query(request, http_request, d)
    return StreamingResponse(
        _sse(started),
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
