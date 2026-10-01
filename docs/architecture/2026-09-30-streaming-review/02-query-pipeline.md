# 2 · Pull the query pipeline out of `main.py`

> Architecture review, 2026-09-30, branch `feature/streaming-answers`.
> Strength: **Strong** · Dependency category: **in-process** (fakes behind `QueryDeps`)
> Companion docs: [01 answer allowance](01-answer-allowance.md) (lives inside this pipeline), [03 web answer stream](03-web-answer-stream.md) (consumes the event protocol defined here), [05 answerer seam](05-answerer-seam.md) (shared test fakes).

## Summary

Everything that turns a question into an answer lives in `main.py` (926 lines): resolving settings, refusals, the gate, the answer cache, slots, retrieval, context building, enrichment, history, the worker-thread hand-off, and the event protocol's head/done split. It is split between `_start_query` (154 lines, L585–738) and `_run_answer` (72 lines, L504–575). The only way into it is the HTTP route, or private names called with a hand-built Starlette `Request`.

This doc proposes a `query_pipeline` module with one entry point:

```python
start(request: QueryRequest, caller: Caller, deps: QueryDeps) -> AnswerStream
```

It returns a typed head and an iterator of typed events. The two route handlers become thin **adapters** over it: one frames the events as SSE, the other collects them into a `QueryResponse`. Retrieval moves into `retrieval.py`, next to the search functions it already calls.

## Why do this

**Right away**

- **Tests are already going around the interface.** `test_query_stream.py::test_closing_the_stream_early_still_finishes_the_answer` (L142–166) builds `main.QueryDeps(...)` field by field, then builds a raw ASGI scope dict to make a Starlette `Request`, and calls `main._sse(main._start_query(...))`. A test that has to fake HTTP to reach business logic is a sign the logic has no interface of its own.
- **There are three exits from `_start_query`.** Each builds a `_Started` by hand (L655, L708, L738), and each calls `_save` with seven keyword arguments copied by hand (L541, L643, L697). Doc 01 and doc 04 both need to edit those exits. Doing this first gives them one place to edit instead of three.
- **The branch is unmerged.** The two-phase split (`b64065a`) is two days old.

**Long term**

- **Leverage.** Any new entry point gets the whole pipeline through `start()`: an eval runner that skips HTTP, a CLI, a websocket, or a "replay with a new model" admin action.
- **Locality.** When the cache, history or event protocol changes, the edit lands in the pipeline module and never in route code. `main.py` goes back to what its name says: app wiring and routes.
- **AI-navigability.** "How does a question get answered?" becomes one file, read top to bottom, instead of a 926-line mix with the rules, config, admin and task endpoints.

## Current state

### What `_start_query` does, in order

| # | Job | Lines |
|---|---|---|
| 1 | Resolve settings and overrides; build an answerer per request when generation settings are overridden | 589–592 |
| 2 | Length check → 422 | 594–597 |
| 3 | Admin + `fresh` check → 403 (reads `http_request`) | 598–600 |
| 4 | Work out the IP bucket from `http_request.client.host`; `tracked`; the `record` dict | 602–606 |
| 5 | Gate | 608–612 |
| 6 | Cache key, cache lookup, validate the stored row, repair legacy rows, enrich, record `cached`, save history, return `_Started` | 614–655 |
| 7 | Slot acquire → 429 | 657–668 |
| 8 | Retrieval, `build_context`, enrich results | 670–674 |
| 9 | Eval fields (context hash, prompt version, generator label) | 676–682 |
| 10 | Record degraded outcome, decrement `remaining` | 684–687 |
| 11 | Build head `QueryResponse` | 689–695 |
| 12 | Not generating → save history, return `_Started` | 696–708 |
| 13 | Live sources + enrichment | 710–711 |
| 14 | Reserve | 712–716 |
| 15 | Fill a 14-field `_AnswerWork`, start the `AnswerJob`, return `_Started` | 717–738 |

### The three hand-written history saves

```python
# L541 (_run_answer, generated)
_save(d.engine, s, work.request,
      answer=answer,
      results=[r.model_dump() for r in work.results],
      error=error,
      citations=[c.model_dump() for c in citations],
      citation_stats=citation_stats.model_dump(),
      rule_references=rule_references)

# L643 (cache hit)
_save(engine, s, request,
      answer=cached_response.answer,
      results=[r.model_dump() for r in cached_response.results],
      error=None,
      citations=[c.model_dump() for c in cached_response.citations],
      citation_stats=cached_response.citation_stats.model_dump(),
      rule_references=cached_response.rule_references,
      cached=True)

# L697 (retrieval only)
_save(engine, s, request,
      answer=None,
      results=[r.model_dump() for r in all_results],
      error=None,
      citations=[],
      citation_stats=CitationStats().model_dump(),
      rule_references=[])
```

All three already have a `QueryResponse`, or could build one. The only thing that differs is `error` and `cached`.

### Global and per-request settings, mixed

`settings.eval_mode` (the global) is read at L162, L272, L554, L605 and L659. The per-request `s` is read everywhere else. They can't differ today, because `eval_mode` isn't in `OVERRIDABLE_SETTINGS`. But a reader has to check `config.py` to find that out, and a future overridable flag would quietly split them.

### The head/done split is protocol knowledge that lives in `main.py`

```python
_HEAD_ONLY = ("degraded", "answers_remaining", "cached_at")          # L330

def _head(response, sources):  ...   # dict, not a type
def _done(response):            ...   # dict, not a type
```

The same split is written again in TypeScript (`StreamHead`, `StreamDone` in `api.ts`) and a third time in the fixtures (`headOf`, `doneOf`). See doc 03. Nothing ties these together. A field added to `QueryResponse` goes into `done` automatically, and into `head` only if someone remembers.

### The blocking endpoint is an adapter that rebuilds a response

```python
@app.post("/api/v1/query")                                           # L741
def query(...):
    started = _start_query(request, http_request, d)
    fields = {k: v for k, v in started.head.items() if k != "sources"}
    for name, data in started.rest:
        if name == "done":
            fields.update(data)
    return QueryResponse(query=request.query, **fields)
```

It is correct, and it shows that the real interface is "a head and an event stream". That interface just isn't named anywhere.

## Proposed design

### Module layout

```
mtg_api/
  query_pipeline.py   NEW  start(), AnswerStream, events, Caller, Refused
  retrieval.py        +    retrieve(query, s, deps) moved from main._retrieve (118 lines)
  allowance.py        NEW  (doc 01)
  main.py             −    routes and wiring only; ~450 lines
  models.py           +    StreamHead, StreamDone (pydantic): the protocol's schema
```

### Interface

```python
# mtg_api/query_pipeline.py  (sketch)

@dataclass(frozen=True)
class Caller:
    ip_bucket: str
    is_admin: bool
    may_refresh: bool          # admin with the X-Admin-Request marker


class Refused(Exception):
    """The request is refused before any answer work starts."""
    def __init__(self, status: Literal[403, 422, 429], detail: str): ...


# The event protocol, typed. One definition; SSE and JSON are adapters.
@dataclass(frozen=True)
class Thinking: ...
@dataclass(frozen=True)
class Delta:
    text: str
@dataclass(frozen=True)
class Failed:                  # generation failed with no text; Done still follows
    message: str
@dataclass(frozen=True)
class Done:
    body: StreamDone

Event = Thinking | Delta | Failed | Done


@dataclass
class AnswerStream:
    head: StreamHead           # always first
    events: Iterator[Event]    # always ends with exactly one Done


def start(request: QueryRequest, caller: Caller, deps: QueryDeps) -> AnswerStream:
    """Answer one question. Raises Refused (422 length, 403 fresh/overrides,
    429 busy) before doing any work that costs money. Otherwise returns at
    once with the head; when an answer is being written, `events` relays it
    from a worker that finishes, records and caches whether or not anyone
    keeps reading."""
```

`StreamHead` and `StreamDone` become pydantic models in `models.py`, so the split is written once, in the schema:

```python
class StreamHead(BaseModel):
    results: list[QueryResult]
    sources: list[Citation]
    degraded: str | None
    answers_remaining: int | None
    cached_at: datetime | None

class StreamDone(BaseModel):
    results: list[QueryResult]
    answer: str | None
    citations: list[Citation]
    rule_references: list[str]
    citation_stats: CitationStats
    answer_complete: bool | None
    context_hash: str | None = None
    prompt_version: int | None = None
    generator: str | None = None
    usage: dict[str, int] | None = None

    def response(self, query: str, head: StreamHead) -> QueryResponse: ...
```

A test pins the invariant that `QueryResponse`'s fields equal the union of `StreamHead`'s and `StreamDone`'s fields, plus `query`, minus `sources`. After that, adding a field to `QueryResponse` without deciding which event carries it fails CI.

### The route handlers afterwards

```python
def _caller(http_request: Request) -> Caller:
    admin = is_admin(http_request)
    host = http_request.client.host if http_request.client else "unknown"
    return Caller(ip_bucket(host), admin, admin and has_admin_marker(http_request))


def _start(request, http_request, d) -> AnswerStream:
    try:
        return query_pipeline.start(request, _caller(http_request), d)
    except Refused as r:
        raise HTTPException(status_code=r.status, detail=r.detail) from r


@app.post("/api/v1/query", response_model=QueryResponse)
def query(request: QueryRequest, http_request: Request, d=Depends(get_query_deps)):
    return query_pipeline.collect(request.query, _start(request, http_request, d))


@app.post("/api/v1/query/stream")
def query_stream(request: QueryRequest, http_request: Request, d=Depends(get_query_deps)):
    stream = _start(request, http_request, d)        # Refused → plain HTTP error
    return StreamingResponse(sse_frames(stream), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
```

`sse_frames` and `collect` are the two **adapters** of the event protocol. Two adapters make it a real **seam**.

```python
def sse_frames(stream: AnswerStream) -> Iterator[str]:
    yield sse_event("results", stream.head.model_dump(mode="json"))
    for e in stream.events:
        match e:
            case Thinking():      yield sse_event("thinking", {})
            case Delta(text):     yield sse_event("delta", {"text": text})
            case Failed(message): yield sse_event("error", {"message": message})
            case Done(body):      yield sse_event("done", body.model_dump(mode="json"))


def collect(query: str, stream: AnswerStream) -> QueryResponse:
    for e in stream.events:
        if isinstance(e, Done):
            return e.body.response(query, stream.head)
    raise AssertionError("answer stream ended without done")
```

### Inside `start()`: three strategies, one history save

```python
def start(request, caller, deps) -> AnswerStream:
    s, answerer = _resolve(request, caller, deps)         # 403 / 422 here
    admission = deps.allowances.admit(...)                # doc 01

    if (hit := _cached(request, s, deps)) is not None:
        return _finished(request, s, deps, hit, remaining=admission.served_from_cache(),
                         cached=True)

    results = retrieve(request.query, s, deps)
    numbered = build_context(results)                     # doc 04: NumberedSources
    enrich_results(results, deps.matcher, deps.rules_index)
    spend = admission.start_answer(...) if wants_answer else None

    if spend is None:
        return _finished(request, s, deps, _retrieval_only(results, admission),
                         remaining=admission.retrieval_only())

    return _generating(request, s, deps, answerer, numbered, results, spend)
```

```python
def _save_history(deps, s, request, response: QueryResponse, *, error=None, cached=False):
    if s.eval_mode and request.source == "eval":
        return
    try:
        save_history(deps.engine, query=request.query, model=s.gemini_model,
                     answer=response.answer, error=error, cached=cached,
                     **response.model_dump(include={"results", "citations",
                                                    "citation_stats", "rule_references"}))
    except Exception:
        logger.exception("Failed to persist query history")
```

The three `_save` sites become three calls of the form `_save_history(..., response, ...)`.

`_AnswerWork` goes away. The generate path is a small class, `_AnswerWriter`, whose constructor takes what it needs and whose `run(emit)` is today's `_run_answer`. With doc 01's `Spend` and doc 04's `NumberedSources`, its state drops from 14 fields to 8: request, s, answerer, deps, numbered sources, results, spend, cache key.

### Settings: one source per request

`_resolve` returns the per-request `s`, and from there on the pipeline reads only `s`. That includes `s.eval_mode`. `resolve_settings` still checks the global `settings.eval_mode` to decide whether overrides are allowed, since that is a property of the deployment rather than of the request.

## Implementation plan

1. **Pin current behaviour.** Run the full suite and save the passing list. No new tests are needed yet: `test_query.py` (22 tests), `test_gating_flow.py`, `test_eval_mode.py` and `test_query_stream.py` already cover the HTTP behaviour end to end.
2. **Add the protocol models.** Add `StreamHead` and `StreamDone` to `models.py`, plus the field-partition test (`tests/test_models.py`). Switch `_head` and `_done` to build them, still as dicts at the edge. Green.
3. **Move `_retrieve`** to `retrieval.py` as `retrieve(query, s, deps)`. It needs only `matcher`, `keyword_matcher`, the embedders and `client`. Pass a small `RetrievalDeps` or the whole `QueryDeps`; prefer the narrower one. Green.
4. **Create `query_pipeline.py`.** Move `_Started` (renamed `AnswerStream`), `_AnswerWork`/`_run_answer` (as `_AnswerWriter`), `_start_query` (as `start`), `_cache_get`, `_cache_put`, `_save` and `_PER_REQUEST_FIELDS`. At first, keep them behaving exactly as they do now: same order, same globals. Replace `HTTPException` with `Refused`, and `http_request` with `Caller`. `main.py` keeps the routes and `_caller`. Green.
5. **Typed events.** Make the worker `emit` `Thinking`/`Delta`/`Failed`/`Done` instead of `(name, dict)` tuples. Add `sse_frames` and `collect`. `streaming.AnswerJob` stays generic: it relays whatever objects it is given. Green.
6. **Collapse the history saves** into `_save_history(response, …)`. Green.
7. **Split `start` into the three strategies** (`_cached`, `_retrieval_only` / `_finished`, `_generating`). Read `s.eval_mode` everywhere. Green.
8. **Add direct pipeline tests** (`tests/test_query_pipeline.py`, see below), and rewrite `test_closing_the_stream_early_still_finishes_the_answer` against `start()`.
9. **Fold in doc 01 and doc 04** if they're being done in the same PR. Otherwise leave clearly marked seams (`admission`, `numbered`) where they plug in.
10. **Verify.** `uv run pytest`, `ruff`, the eval harness against a local stack (`evals/` posts to `/api/v1/query`, so its responses must be byte-for-byte the same), and a manual `curl -N` through nginx.

Rough size: `main.py` 926 → ~450 lines. `query_pipeline.py` ~330 lines. `retrieval.py` +120 lines. Net change about −20 lines. The point is placement, not line count.

## Test plan

**New: `tests/test_query_pipeline.py`**. No `TestClient`, no `dependency_overrides`, no ASGI scope:

```python
def test_cache_hit_is_head_then_done(deps):
    first = start(QueryRequest(query="trample"), CALLER, deps)
    list(first.events)
    second = start(QueryRequest(query="trample"), CALLER, deps)
    assert second.head.cached_at is not None
    assert [type(e) for e in second.events] == [Done]

def test_closing_the_relay_still_finishes_the_answer(deps):
    stream = start(QueryRequest(query="trample"), CALLER, deps)
    stream.events.close()                       # nobody is reading
    join_all()
    assert outcomes(deps.engine) == ["generated"]
```

Cases to cover: each strategy's event sequence; `Refused` statuses (422 length, 403 fresh without the marker, 429 busy); a per-request answerer built only for generation overrides; eval-mode fields present and history skipped for `source="eval"`; `collect` equals `StreamDone.response`.

**New: the adapter tests.** `sse_frames` produces the exact frames for each event type. `collect` on a stream with no `Done` raises.

**Keep:** the HTTP tests in `test_query.py`, `test_gating_flow.py`, `test_eval_mode.py` and `test_query_stream.py`. They are now integration tests for the route adapters and should pass unchanged. That is the refactor's safety net. Over time, move the behaviour-only cases (cache, history, eval fields) down to `test_query_pipeline.py`, leaving the HTTP files to check status codes, headers and framing.

**Shared deps factory.** Move `_override`'s fakes (`_FakeDenseModel`, `_FakeSparseModel`, `_FakeQdrantClient`, `_FakeHit`) from `test_query.py` into `conftest.py` and add a `deps()` fixture that builds `QueryDeps` directly. This removes the cross-file test imports (`from test_query import ...` in three files). Doc 05 does the same for answerers.

## Risks

- **A large diff in a hot file.** Steps 2–7 are each mechanical and green on their own. Land them as separate commits.
- **The JSON contract.** `collect` must give the same `QueryResponse` the current `query` builds. The eval harness's cached answers are keyed on response content, so run the eval suite before and after and diff the outputs.
- **Typed events in a generic job.** `AnswerJob` relays any object; only `sse_frames` serializes. Make sure nothing else (logging, tests) depends on the tuple shape. `test_streaming.py` uses tuples; keep those as they are, since that's the job's own interface.

## Open questions (for grilling)

1. Should the blocking `/api/v1/query` keep running the answer on a worker thread, or should `start()` take a `detached: bool` so `collect` runs it inline? Inline is simpler for evals and skips a thread per request. Detached keeps one code path.
2. Should `QueryDeps` be split into `RetrievalDeps` and `AnswerDeps`? The cache-hit path needs neither embedders nor Qdrant, and a narrower dependency set makes the pipeline tests' fixtures smaller.
3. Should the pipeline's events also be the eval harness's interface, letting `evals/` call `start()` in-process instead of over HTTP? That's speculative, and only worth it if eval runs are slow because of HTTP.
