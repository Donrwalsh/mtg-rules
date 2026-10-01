# Streaming answers from Gemini

Status: design agreed, awaiting implementation plan
Date: 2026-09-30
Branch: `feature/streaming-answers` (to be cut from `main`)

## Purpose

Cut the time a visitor spends looking at a spinner. Today `POST
/api/v1/query` retrieves, then waits on one blocking `generateContent`
call (often 5–20 s with thinking), then returns everything at once.

The main goal is **perceived latency**: show something useful within a
second or two. Most of that win comes from showing the **evidence**
(retrieved rules, cards, rulings) as soon as retrieval finishes, before
Gemini has written anything. Streaming the answer text itself is the
second, smaller win: visitors can start reading the first sentence while
the rest is still being written.

Core decisions:

- A **new SSE endpoint**, `POST /api/v1/query/stream`. The JSON endpoint
  stays, for evals and scripts, and both share one pipeline.
- **Evidence first**, then a "Thinking…" state, then answer text as it
  arrives, then an authoritative final event with validated citations.
- **Generation outlives the connection.** Once started, an answer is
  always read to the end on the server, then recorded, cached and saved
  to history, even if the visitor leaves.
- **Spend is reserved up front** and concurrent generations are capped,
  so an answer that keeps running after its visitor leaves can't be used
  to get around the quotas.

## Investigation findings

1. **One sync pipeline.** `query()` in `main.py` does validation →
   gate → cache lookup → retrieval → `answerer.generate()` → usage
   record → `cite_answer` → history → cache put, then returns one
   `QueryResponse`. One uvicorn worker; sync handlers run in the
   threadpool.
2. **Citation validation never renumbers.** `parse_citations` only
   removes invalid `[n]` numbers (and drops a marker left empty).
   Streamed `[n]` markers therefore mean the same source after
   validation, and the order of `results` is exactly the `[n]` numbering
   (`build_context` enumerates `all_results` from 1).
3. **Cost needs `usageMetadata`.** `cost_usd()` uses prompt, candidates
   and thoughts token counts. In a `streamGenerateContent` stream, the
   final counts arrive only with the last chunk. Earlier chunks usually
   carry partial counts, and `promptTokenCount` is normally present from
   the first chunk.
4. **nginx buffers `/api/`.** A stream would build up in nginx and arrive
   all at once at the end. `limit_conn api_conn 2` and
   `proxy_read_timeout 90s` (the time between reads, not the total) also
   apply.
5. **The gate counts only finished rows.** `check_gate` sums `cost_usd`
   and counts `generated`/`error` rows, and those are written only after
   generation. The concurrent-request race exists today but is limited
   by `limit_conn 2`, because a sync request holds its connection.
   Generation that outlives the connection (Q7) removes that limit.
6. **The frontend takes the whole response.** `submitQuery` awaits
   `resp.json()`. `AnswerBody` runs `segmentAnswer` → `assignSentences`
   → `layoutAnswer` on the complete string, and `EvidencePanel` takes a
   `citations` list. Production sets `max_tokens` 2048 and thinking
   `low` (`docker-compose.prod.yml`).
7. **History replay reuses `QueryResponse`.** `GET
   /api/v1/queries/{id}` returns a `ReplayResponse(QueryResponse)` built
   from a `query_history` row, and `/?replay=<id>` shows it on the desk.
   `ask()` ends a replay before it asks.
8. **Playwright runs against fixtures.** `mtg-web/e2e/` drives `vite dev`
   with `?mock=<fixture>` and routes every `/api/v1/**` call to a 503, so
   no backend is needed.

## Decisions

| # | Topic | Decision |
|---|---|---|
| 1 | Goal | Perceived latency first; reading while it's written second. Evidence is shown before generation starts. |
| 2 | Endpoint | New `POST /api/v1/query/stream`. `/api/v1/query` keeps its JSON contract; both run one shared pipeline that yields events. |
| 3 | Transport | SSE-formatted events on a `POST` response, read with `fetch` + `ReadableStream`. No `EventSource` (it can't `POST`), no WebSocket. |
| 4 | Events | `results` → `thinking` → `delta`×N → `done` \| `error`. `done` is authoritative, and the client replaces the streamed text with it. |
| 5 | Cache hits | `results` then `done` immediately. No simulated typing. |
| 6 | Early stop (`MAX_TOKENS`, `SAFETY`, …) | Keep the partial text, validate its citations, flag it as cut off, don't cache it. |
| 7 | Client disconnect | The server reads Gemini to the end and records, caches and saves history as usual. |
| 8 | Thinking | Show a plain "Thinking…" state until the first answer text. Thought summaries are never requested or shown. |
| 9 | Transport error or HTTP error mid-stream | Same as 6: keep the partial text with the cut-off notice. Record an estimated cost (see 17) so budget gating isn't undercounted. |
| 10 | Live citations | Show `[n]` as real markers during the stream, using the sources sent in `results`. Hold back an unfinished `[1` at the end of the buffer. |
| 11 | Live layout | Rerun the normal lead/body layout at most once per animation frame. |
| 12 | Detached generation | A worker thread reads Gemini into a queue and does all the bookkeeping. The response only takes events from the queue. |
| 13 | Gemini client | `GeminiAnswerer.stream()` on `streamGenerateContent?alt=sse`, and `generate()` collects from it. One parser, and evals see the same path users do. |
| 14 | Errors before the stream | 422 / 403 / 429 stay plain HTTP errors, sent before any event. Only generation failures become `error` events. |
| 15 | nginx | `location = /api/v1/query/stream` with `proxy_buffering off` and the same limits. The backend also sends `X-Accel-Buffering: no`. |
| 16 | Quota safety | Both: reserve a `pending` usage row at the gate, **and** in-memory caps on concurrent generations. |
| 17 | Missing usage | Use the last `usageMetadata` seen. Otherwise estimate chars ÷ 4 (rounded up). Thinking with no count seen = `max_tokens`. |
| 18 | Timeouts | 30 s limit between chunks (it covers the thinking gap) plus a total generation cap (`gemini_timeout_seconds`, 60 s). Hitting either → decision 9. |
| 19 | Duplicate questions in flight | Accepted. No joining of identical generations that are both in progress. |
| 20 | Rollout | The frontend switches to the stream endpoint completely, with no flag and no fallback. The nginx change ships in the same release. |
| 21 | Testing | Mocked Gemini SSE on the backend; a streaming fixture and parser tests on the frontend; a manual `curl -N` through nginx. |
| 22 | Cap exemptions | Eval mode skips both caps. Admins skip the per-IP cap but count toward the global cap. A cap 429 reuses `RateLimitedError`. |

## Design

### Event protocol (`POST /api/v1/query/stream`)

Request body: the same `QueryRequest` as `/api/v1/query`. Response:
`200`, `Content-Type: text/event-stream`, `Cache-Control: no-cache`,
`X-Accel-Buffering: no`. Each event is `event: <name>\ndata: <json>\n\n`.

| Event | When | `data` |
|---|---|---|
| `results` | Always first | `results` (enriched `QueryResult`s, as today), `sources` (a list of `Citation`s, one for every numbered result: see below), `degraded`, `answers_remaining`, `cached_at` |
| `thinking` | Generation started, no answer text yet | `{}` |
| `delta` | Each chunk that has answer text | `{"text": "<raw text since the last delta>"}` |
| `error` | Generation failed with **no** answer text at all; `done` still follows | `{"message": "..."}` |
| `done` | **Always last** | Every `QueryResponse` field except `query`, `degraded`, `answers_remaining` and `cached_at`: `results` again (now with `cited` flags), `answer`, `citations`, `rule_references`, `citation_stats`, `answer_complete`, and in eval mode the eval fields |

`sources` lets the client show a live `CitationMarker` for `[n]` before
validation has run. Result `i` in `results` is source `i + 1`, so the
client can also mark live-cited results as `cited`. Each `Citation` is built with the same helper as
`build_citations` (the `_citation` function in `citations.py`, made
public) and then enriched with `enrich_citations`. The `done` event's
`citations` list replaces the live one, and in practice only removes the
occasional invalid number.

`answer_complete` is a new `QueryResponse` field (also on the JSON
endpoint): `true` when `finish_reason == "STOP"`, `false` when there is
partial text from any early stop or failure, `null` when there's no
answer. If generation fails after some text has streamed, there is no
`error` event: the stream just ends with `done` (with `answer_complete:
false`). The JSON endpoint reads only `results` and `done`.

A degraded request (quota or budget), a `generate: false` request, and a
cache hit all send `results` followed right away by `done`.

History replay has no stored `finish_reason`. It sets `answer_complete`
to `false` when the row has both an `answer` and an `error` (that pair
only exists for a cut-off answer), and to `true` otherwise.

### Backend pipeline

The work in `query()` splits into two phases so both endpoints can share
it.

**Phase 1: before the response (request thread).** Everything that can
refuse the request with an HTTP status runs here, so decision 14 holds:

1. Resolve settings/overrides, validate length, check `fresh` →
   422/403 as today.
2. Cache lookup → on a hit, the pipeline is just `results` + `done`
   (record `cached`, save history, as today).
3. Gate (as today). If not degraded and an answer is wanted:
4. **Acquire a generation slot** (see *Concurrency caps*). No slot →
   `HTTPException(429)`.
5. Retrieval, `build_context`, enrichment, `sources` (exactly as today).
6. **Reserve** a `pending` usage row (see *Spend reservation*). This
   comes after retrieval because the worst-case estimate needs the
   context's length. The gap is one retrieval (well under a second), and
   the slot caps limit how many requests can sit in it.

**Phase 2: generation (worker thread).** A `GenerationJob` owns the
slot, the reservation id, `context`, `sources` and a `queue.Queue` of
events. Its thread:

1. Pushes `thinking`, then calls `answerer.stream(query, context)` and
   pushes a `delta` for each chunk, while collecting the text, the
   latest `usageMetadata` and `finishReason`.
2. On a normal end, an early stop, a timeout or a transport error: runs
   `cite_answer` on whatever text there is, finalizes the usage row
   (real or estimated cost), saves history, puts the answer in the cache
   only if `finish_reason == "STOP"`, and pushes `done` (preceded by
   `error` if there was no text at all). A cut-off answer is saved to
   history with `error = "answer cut off (finish reason: …)"`.
3. In `finally`: releases the slot and pushes an end-of-stream marker.
   If anything above raised unexpectedly, the reservation keeps its
   worst-case cost (see below), so the budget stays safe.

Pushing to the queue never blocks: it's an unbounded queue of small
events, and no one reads it after a disconnect. Nothing in the worker
depends on the client.

**Endpoints.**

- `/api/v1/query/stream` returns a `StreamingResponse` over a sync
  generator: it sends `results`, then relays queue events until the
  end marker. When the client disconnects, Starlette closes the
  generator. That only stops the relay, and the worker carries on.
- `/api/v1/query` runs the same phases and then waits for the job to
  finish (it reads the queue to the end) and builds the `QueryResponse`
  from `results` + `done` as today. Its JSON contract is unchanged
  except for the new `answer_complete` field.

Thread-safety: the `Engine` is already shared across threadpool
threads. `sources` / `QueryResult.cited` are changed only by the worker
after phase 1 hands them over.

### Gemini client (`llm.py`)

- `GeminiAnswerer.stream(query, context) -> Iterator[StreamChunk]` posts
  the same body to
  `/v1beta/models/{model}:streamGenerateContent?alt=sse` with
  `httpx.stream`, parses `data:` lines, and yields per-chunk answer text
  (non-thought parts only), plus the latest `usageMetadata` and
  `finishReason` it has seen.
- `includeThoughts` is not sent. Thought parts are skipped if they appear
  anyway, as today.
- A blocked prompt (no candidates, `promptFeedback.blockReason`) raises,
  as today.
- **Timeouts:** `httpx.Timeout(connect=10, read=stream_chunk_timeout,
  ...)` gives the 30 s limit between chunks. The total cap is
  `gemini_timeout_seconds`, checked between chunks against a monotonic
  deadline. New setting: `gemini_stream_chunk_timeout_seconds: float =
  30.0`.
- `generate()` collects `stream()` into the same `Generation` as today, so
  evals and `generator_label()` are unaffected. `PROMPT_VERSION` does not
  change.

### Spend reservation (`usage.py`)

- New outcome **`pending`**, added to `ANSWER_OUTCOMES` so it counts
  against per-IP quotas straight away. The `outcome` column is already
  `Text`, so no migration is needed. Update the column comment.
- `reserve_usage(...) -> int` inserts a `pending` row whose `cost_usd` is
  the **worst case**: estimated input tokens (context chars ÷ 4, rounded
  up) at the input price, plus `max_tokens` (or 2048 if unset) at the
  output price. `spend_since` then counts it straight away.
- `finalize_usage(row_id, outcome, generation, cost)` updates that row
  in place to `generated` / `error` with real or estimated token counts
  and cost.
- A row left `pending` (process crash, or a bug in the worker) keeps its
  worst-case cost for good, which is the conservative failure. No sweeper
  is needed. The admin usage page shows `pending` as its own outcome.
- `record_usage` stays for the `cached` / `degraded_*` outcomes.

**Estimated cost when `usageMetadata` is missing or incomplete**
(decision 17): input = last `promptTokenCount` seen, otherwise ⌈prompt
chars ÷ 4⌉. Output = last `candidatesTokenCount` seen, otherwise
⌈received answer chars ÷ 4⌉. Thinking = last `thoughtsTokenCount` seen,
otherwise `max_tokens` (or 2048). Round the result up.

This applies only to a stream that ended **without** a `finishReason`.
Once the final chunk has arrived, a missing count means zero (Gemini
leaves out `thoughtsTokenCount` when there was no thinking), as today. A
stream that never got a single chunk (for example, Gemini answered 429)
costs zero, as a failed call does today.

### Concurrency caps

An in-process `GenerationSlots` (a lock and counters; one uvicorn worker,
so memory in one process is enough):

- global: `max_concurrent_generations = 4`
- per IP bucket: `max_concurrent_generations_per_ip = 1`

Acquired in phase 1 step 4 and released in the worker's `finally`. Eval
mode skips both caps. Admins skip the per-IP cap but count toward the
global one. If no slot is free, the request gets a 429 with `detail`
matching the message the frontend already shows for `RateLimitedError`.
Both limits are new `Settings` fields.

### nginx (`mtg-web/nginx.conf`)

```nginx
location = /api/v1/query/stream {
    limit_req zone=api burst=5 nodelay;
    limit_conn api_conn 2;
    proxy_pass http://backend:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $remote_addr;
    proxy_set_header X-Forwarded-Proto $forwarded_proto;
    proxy_buffering off;
    proxy_cache off;
    proxy_read_timeout 90s;
}
```

Traefik (Coolify) passes streams through without buffering by default.
The `limit_conn 2` is still enough for one tab, because a new question
aborts the previous stream. The Vite dev proxy doesn't buffer SSE and
needs no change.

### Frontend

**`api.ts`:** `streamQuery(query, {fresh, signal, on})` posts to
`/api/v1/query/stream`. It maps 429 → `RateLimitedError` and other
non-2xx → `Error` before reading anything, then parses SSE frames from
`resp.body` and calls `on.results`, `on.thinking`, `on.delta`,
`on.done`, `on.error`. The frame parser is a pure function (`sse.ts`)
that handles a frame split across network chunks. `submitQuery` stays
for anything that still wants JSON. `QueryResponse` gains
`answer_complete`.

**Page state (`routes/+page.svelte`):** `View` gains `'streaming'`. `ask()`
still ends a replay first, as it does today. It then aborts the previous
request's `AbortController`, and a replay load aborts it too. Then:

- before `results`: `LoadingState` as today;
- `results`: build a partial response (results, degraded,
  answers_remaining, cached_at, `sources`). Show `EvidencePanel` /
  `MatchingSources` at once. The answer column shows **"Thinking…"**;
- `delta`: add to a `draft` string. A `requestAnimationFrame`-throttled
  derived value feeds `AnswerBody`;
- `done`: replace the draft and the live citations with the validated
  fields. `view = 'result'`;
- `error`: nothing to do. The `done` that follows has no answer, so the
  page shows today's "Couldn't write an answer this time" notice above
  the matching sources.
- A network failure after `results` (the stream ends without `done`):
  keep the evidence and whatever text has streamed, marked
  `answer_complete: false`. With no text, show the same "Couldn't write
  an answer" notice. A failure before `results` → `ErrorState` as today.

**Live citations:** during streaming, `AnswerBody` gets `citations` =
the `sources` whose number appears in the draft so far, and
`EvidencePanel` gets the same list, so it fills in as the answer cites
sources. The draft passed to segmentation leaves out an unfinished
bracket at the end (`/\[[\d,\s]*$/`) until it closes. Invalid numbers
stay plain text until `done` removes them.

**Live layout:** `layoutAnswer` / `assignSentences` must handle a final
sentence that isn't finished yet (no closing punctuation). Add a unit
test and fix it if it doesn't.

**Cut-off notice:** when `answer_complete === false`, show a caution note
under the answer ("This answer was cut off before it finished…"), styled
like the existing uncited-answer note. It also shows on the history page
for rows whose `error` is set and whose `answer` isn't null.

**Fixtures:** `?mock=<fixture>` gains streaming fixtures that replay
events on a timer: normal, thinking-long, cut-off, error-after-results,
cached. This way every state can be tried in dev without Gemini.

## Testing

Backend (`mtg-api/tests/`, `httpx.MockTransport` serving recorded
`streamGenerateContent` SSE bodies):

- `stream()` parsing: text across chunks, thought parts skipped, usage
  and `finishReason` taken from the last chunk, a blocked prompt, the
  timeout between chunks, the total cap.
- `generate()` still returns the same `Generation` as before, and the
  existing `test_llm.py` cases pass unchanged.
- `/query/stream` event order for a normal answer, a cache hit, a
  degraded request, `generate: false`, `MAX_TOKENS` (`done` with
  `answer_complete: false`, not cached), a failure after partial text
  (`done` with an estimated cost), and a failure with no text (`error`).
- Client disconnect after `results`: the worker still finalizes usage,
  caches, and saves history.
- Reservation: `pending` counts in `check_gate` straight away; it's
  finalized to `generated`/`error`; a stranded `pending` keeps its
  worst-case cost.
- Caps: a second concurrent request from one IP → 429, the global cap →
  429, eval mode exempt, admin exempt from the per-IP cap only.
- `/api/v1/query` JSON responses match what they were before (plus
  `answer_complete`).

Frontend (vitest): the SSE frame parser (split frames, several frames in
one chunk, trailing partial), holding back unfinished brackets, and
`layoutAnswer` on an unfinished last sentence.

Frontend (Playwright, `e2e/`, on the streaming fixtures): evidence is
visible and "Thinking…" shows before any answer text; a citation marker
from the stream can be clicked before `done`; the cut-off fixture shows
the notice; asking again partway through a stream replaces it without
mixing the two answers.

Manual: `docker compose up`, then `curl -N -X POST
localhost:<web-port>/api/v1/query/stream -H 'Content-Type:
application/json' -d '{"query":"how does trample work"}'` through the
nginx container. Events should arrive one at a time, not in a single
burst at the end. Then click through the dev fixtures and one real
answer in the browser.

## Out of scope

- Joining identical in-progress generations (decision 19).
- Showing thought summaries (decision 8).
- Any change to the eval harness beyond `answer_complete` appearing in
  responses.
- Streaming on the history page (it renders stored answers).
