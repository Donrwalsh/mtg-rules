"""How a question becomes an answer: refusals, the answer cache, retrieval,
the answer written on a worker thread, and history. The answer allowance
(allowance.py) decides and pays for answers. The routes in main.py are thin
adapters over start()."""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic import TypeAdapter, ValidationError
from qdrant_client import QdrantClient
from sqlalchemy.engine import Engine

from mtg_api.allowance import Allowances, GenerationsBusy, Spend
from mtg_api.answer_cache import cache_key, get_cached, normalize_query, put_cached
from mtg_api.card_matcher import CardMatcher
from mtg_api.citations import citation_for, cite_answer
from mtg_api.config import (
    GENERATION_SETTINGS,
    OVERRIDABLE_SETTINGS,
    Settings,
    answer_model,
    generator_label,
    settings,
)
from mtg_api.embedder import Embedder
from mtg_api.enrich import enrich_citations, enrich_results
from mtg_api.history import save_history
from mtg_api.keyword_matcher import KeywordMatcher
from mtg_api.llm import (
    PROMPT_VERSION,
    Answerer,
    StreamAccumulator,
    build_answerer,
    build_context,
    prompt_chars,
)
from mtg_api.models import (
    CitationStats,
    QueryRequest,
    QueryResponse,
    QueryResult,
    StreamDone,
    StreamHead,
)
from mtg_api.retrieval import RetrievalDeps, retrieve
from mtg_api.rules_index import RulesIndex
from mtg_api.sparse_embedder import SparseEmbedder
from mtg_api.streaming import AnswerJob, Emit, sse_event
from mtg_api.usage import estimate_generation

logger = logging.getLogger(__name__)


class Refused(Exception):
    """The request is refused before any answer work starts. The routes turn
    it into an HTTP error with this status."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


BUSY = "Too many answers in progress. Try again in a moment."


@dataclass(frozen=True)
class Caller:
    """Who is asking, as far as quotas and admin-only options care."""

    ip_bucket: str
    is_admin: bool
    # An admin request that carries the X-Admin-Request marker: may ask for
    # a fresh answer.
    may_refresh: bool


@dataclass(frozen=True)
class Thinking:
    """The answer is being written; no text yet."""


@dataclass(frozen=True)
class Delta:
    text: str


@dataclass(frozen=True)
class Failed:
    """Generation failed before writing any text. Done still follows."""

    message: str


@dataclass(frozen=True)
class Done:
    body: StreamDone


Event = Thinking | Delta | Failed | Done


def resolve_settings(overrides: dict[str, Any]) -> Settings:
    """The settings one request runs with: the global settings, or a copy
    with this request's validated overrides applied. Never mutates the
    global object, so an override cannot leak into the next request."""
    if not overrides:
        return settings
    if not settings.eval_mode:
        raise Refused(403, "overrides are only accepted when MTG_API_EVAL_MODE is on")
    unknown = sorted(set(overrides) - set(OVERRIDABLE_SETTINGS))
    if unknown:
        raise Refused(422, f"unknown override keys: {', '.join(unknown)}")
    # model_copy(update=) does no validation, so coerce each value against
    # its field's type first.
    validated = {}
    for key, value in overrides.items():
        try:
            validated[key] = TypeAdapter(Settings.model_fields[key].annotation).validate_python(
                value
            )
        except ValidationError as exc:
            raise Refused(422, f"invalid override {key}: {exc.errors()[0]['msg']}") from exc
    return settings.model_copy(update=validated)


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


def _save_history(
    engine: Engine,
    s: Settings,
    request: QueryRequest,
    response: QueryResponse,
    *,
    error: str | None = None,
    cached: bool = False,
) -> None:
    # Eval runs are not user queries, but only eval mode may say so.
    if s.eval_mode and request.source == "eval":
        return
    try:
        save_history(
            engine,
            query=request.query,
            model=answer_model(s),
            answer=response.answer,
            error=error,
            cached=cached,
            results=[r.model_dump() for r in response.results],
            citations=[c.model_dump() for c in response.citations],
            citation_stats=response.citation_stats.model_dump(),
            rule_references=response.rule_references,
        )
    except Exception:
        logger.exception("Failed to persist query history")


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
    allowances: Allowances

    def retrieval(self) -> RetrievalDeps:
        return RetrievalDeps(
            self.matcher,
            self.keyword_matcher,
            self.dense_embedder,
            self.sparse_embedder,
            self.client,
        )


@dataclass
class AnswerStream:
    head: StreamHead  # always first
    events: Iterator[Event]  # always ends with exactly one Done


def start(request: QueryRequest, caller: Caller, deps: QueryDeps) -> AnswerStream:
    """Answer one question. Raises Refused (422 too long or a bad override,
    403 fresh or overrides not allowed, 429 too many answers in progress)
    before any work that costs money. Otherwise returns at once with the
    head; when an answer is being written, `events` relays it from a worker
    that finishes, records and caches it whether or not anyone keeps
    reading."""
    s, answerer = _resolve(request, caller, deps)
    now = datetime.now(UTC)
    admission = deps.allowances.admit(
        s,
        ip_bucket=caller.ip_bucket,
        is_admin=caller.is_admin,
        generate=request.generate,
        model=answer_model(s),
    )

    # The answer cache's own rule (only answers people asked for, never eval
    # runs), separate from the allowance's metering.
    key = (
        cache_key(request.query, s, deps.data_version)
        if request.generate and not s.eval_mode and s.answer_cache_enabled
        else None
    )
    if key and not request.fresh:
        hit = _cached(request, deps, key)
        if hit is not None:
            hit.answers_remaining = admission.served_from_cache()
            return _finished(deps, s, request, hit, cached=True)

    spend: Spend | None = None
    try:
        results = retrieve(request.query, s, deps.retrieval())
        context, sources = build_context(results)
        # After build_context: display data must never reach the LLM.
        enrich_results(results, deps.matcher, deps.rules_index)
        eval_fields = _eval_fields(s, context)

        if request.generate and admission.degraded is None:
            try:
                spend = admission.start_answer(prompt_chars(request.query, context))
            except GenerationsBusy:
                raise Refused(429, BUSY) from None
        remaining = spend.answers_remaining if spend else admission.retrieval_only()

        head = QueryResponse(
            query=request.query,
            results=results,
            degraded=admission.degraded,
            answers_remaining=remaining,
            **eval_fields,
        )
        if spend is None:
            return _finished(deps, s, request, head)

        live_sources = [citation_for(n, r) for n, r in sources.items()]
        enrich_citations(live_sources, deps.matcher, deps.rules_index)
        writer = _AnswerWriter(
            s=s,
            request=request,
            answerer=answerer,
            deps=deps,
            context=context,
            sources=sources,
            results=results,
            now=now,
            spend=spend,
            key=key,
            answers_remaining=remaining,
            eval_fields=eval_fields,
        )
        job = AnswerJob(writer.run).start()
    except BaseException:
        if spend:
            spend.cancel()
        raise
    return AnswerStream(StreamHead.of(head, live_sources), job.events())


def _resolve(request: QueryRequest, caller: Caller, deps: QueryDeps) -> tuple[Settings, Answerer]:
    """This request's settings and answerer, or Refused."""
    s = resolve_settings(request.overrides)
    answerer = deps.answerer
    if GENERATION_SETTINGS & request.overrides.keys():
        answerer = build_answerer(s)
    if len(request.query) > s.max_query_chars:
        raise Refused(422, f"query is longer than {s.max_query_chars} characters")
    if request.fresh and not caller.may_refresh:
        raise Refused(403, "fresh answers are admin-only")
    return s, answerer


def _cached(request: QueryRequest, deps: QueryDeps, key: str) -> QueryResponse | None:
    """The cached answer to this question, or None (a miss, or a row that no
    longer parses, which the new answer will overwrite)."""
    hit = _cache_get(deps.engine, key)
    if hit is None:
        return None
    stored, generated_at = hit
    try:
        response = QueryResponse(**stored, query=request.query, cached_at=generated_at)
    except Exception:
        logger.exception("Malformed answer-cache row for key %s; treating as a miss", key)
        return None
    # Only complete answers are cached; rows from before the field.
    if response.answer_complete is None and response.answer:
        response.answer_complete = True
    # Rows cached before enrichment existed lack card/heading.
    enrich_results(response.results, deps.matcher, deps.rules_index)
    enrich_citations(response.citations, deps.matcher, deps.rules_index)
    return response


def _finished(
    deps: QueryDeps,
    s: Settings,
    request: QueryRequest,
    response: QueryResponse,
    *,
    cached: bool = False,
) -> AnswerStream:
    """A request answered without a worker: a cache hit, or retrieval only."""
    _save_history(deps.engine, s, request, response, cached=cached)
    return AnswerStream(StreamHead.of(response, []), iter([Done(StreamDone.of(response))]))


def _eval_fields(s: Settings, context: str) -> dict:
    if not s.eval_mode:
        return {}
    return {
        "context_hash": hashlib.sha256(context.encode("utf-8")).hexdigest(),
        "prompt_version": PROMPT_VERSION,
        "generator": generator_label(s),
    }


@dataclass
class _AnswerWriter:
    """Writes, records, caches and saves one answer on the job's thread,
    whether or not anyone is still listening."""

    s: Settings
    request: QueryRequest
    answerer: Answerer
    deps: QueryDeps
    context: str
    sources: dict[int, QueryResult]
    results: list[QueryResult]
    now: datetime
    spend: Spend
    key: str | None
    answers_remaining: int | None
    eval_fields: dict

    def run(self, emit: Emit) -> None:
        s, d = self.s, self.deps
        acc = StreamAccumulator()
        failure = None
        try:
            emit(Thinking())
            try:
                for chunk in self.answerer.stream(self.request.query, self.context):
                    acc.add(chunk)
                    if chunk.text:
                        emit(Delta(chunk.text))
            except Exception as exc:
                logger.exception("Answer generation failed (%s)", generator_label(s))
                failure = str(exc)

            generation = estimate_generation(acc, prompt_chars(self.request.query, self.context), s)
            self.spend.settle(generation, failed=failure is not None)

            # Keep whatever was written, even when the stream failed after it.
            answer = acc.text if (acc.text or failure is None) else None
            complete = failure is None and acc.finish_reason == "STOP"
            error = failure
            if answer and not complete and error is None:
                error = f"answer cut off (finish reason: {acc.finish_reason or 'none'})"

            # Must run before the results are dumped: it sets each cited
            # result's `cited` flag.
            cited = cite_answer(answer, self.sources, d.rules_index) if answer is not None else None
            if cited is not None:
                answer = cited.answer
            citations = cited.citations if cited else []
            enrich_citations(citations, d.matcher, d.rules_index)

            eval_fields = dict(self.eval_fields)
            if s.eval_mode:
                eval_fields["usage"] = None if failure else generation.usage()
                eval_fields["generation_error"] = error
            response = QueryResponse(
                query=self.request.query,
                results=self.results,
                answer=answer,
                citations=citations,
                rule_references=cited.rule_references if cited else [],
                citation_stats=cited.stats if cited else CitationStats(),
                answer_complete=None if answer is None else complete,
                answers_remaining=self.answers_remaining,
                **eval_fields,
            )
            _save_history(d.engine, s, self.request, response, error=error)
            if self.key and answer and complete:
                _cache_put(d.engine, self.key, self.request.query, response, self.now)
            if failure and answer is None:
                emit(Failed(failure))
            emit(Done(StreamDone.of(response)))
        finally:
            self.spend.close()


def sse_frames(stream: AnswerStream) -> Iterator[str]:
    """The stream as SSE frames. Closing this (the client left) only stops
    the relay; the answer finishes on its own thread."""
    yield sse_event("results", stream.head.model_dump(mode="json"))
    for event in stream.events:
        match event:
            case Thinking():
                yield sse_event("thinking", {})
            case Delta(text):
                yield sse_event("delta", {"text": text})
            case Failed(message):
                yield sse_event("error", {"message": message})
            case Done(body):
                yield sse_event("done", body.model_dump(mode="json"))


def collect(query: str, stream: AnswerStream) -> QueryResponse:
    """The stream as one response, for the blocking endpoint. Reads to the
    end, not just to Done: the worker frees its generation slot after
    sending Done, and the caller's next request must find it free, as it
    always has."""
    done = None
    for event in stream.events:
        if isinstance(event, Done):
            done = event
    if done is None:
        raise AssertionError("answer stream ended without done")
    return done.body.response(query, stream.head)
