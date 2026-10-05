# Web answer stream

Status: design agreed, awaiting implementation plan
Date: 2026-10-05
Branch: `refactor/web-answer-stream` (cut from `main` at `a7940fe`, after doc 01 merged)

## Purpose

Architecture review
[doc 03](../../architecture/2026-09-30-streaming-review/03-web-answer-stream.md)
proposes an **answer stream** module on the web side: a pure reducer from
protocol events to what the page shows, a thin Svelte binding, and event
sources as adapters behind one port. This spec doesn't repeat that design.
It records the decisions taken on doc 03's open questions, the ones that
followed from them, and what changed since doc 03 was written.

This PR **doesn't change behaviour** a visitor can see. Every existing
Playwright test passes unchanged. It is a reshaping that makes the page's
state transitions unit-testable, plus a contract test across the API/web
seam.

## Since doc 03 was written

Doc 03's headline argument was a bug: asking again mid-stream got a 429
(the abandoned answer keeps the visitor's one generation slot), and the page
then showed "Couldn't write an answer" for the first question. **PR #21
(`e144b79`, 2026-10-01) fixed it** by not letting a visitor ask while an
answer is loading or streaming: the button reads "Searching…" /
"Answering…" and is disabled, which also blocks Enter, and `ask()` returns
early while `live`. The e2e test `asking again is blocked until the answer
finishes` pins it.

So doc 03's 429-specific parts (`settleInterrupted`, the `busy` Playwright
fixture, plan step 7) shrink to one rule, "can't ask while busy", plus one
reducer row for the 429 that can still happen: the client's stream broke,
the server is still writing, and the visitor asks again.

Doc 02 gave the server `StreamHead` / `StreamDone` pydantic models
(`models.py`), with a backend test pinning that together they carry every
`QueryResponse` field but `query`. Doc 01 moved the slots into
`allowance.py`; `start_answer` takes the slot just before the head is sent.

## Decisions

| # | Question | Decision |
|---|---|---|
| 1 | The 429 mid-stream (doc 03 Q1) | **(b), as shipped in PR #21:** asking is disabled while an answer is loading or streaming. (a) is worse than what shipped, (c) still 429s for a generating second question, and (d) means two generations per IP or reversing streaming decision 7. |
| 2 | Scope | **Doc 03, trimmed.** Reducer, binding and event-yielding adapters. No `settleInterrupted`, no `busy` fixture. The structural case stands without the bug: the page's state machine lives in closures and isn't unit-tested. |
| 3 | Where `shown` lives (doc 03 Q2) | **A pure `shown(state, visibleDraft)` in `reduce.ts`.** The binding owns the rAF throttle and passes its throttled copy of the draft. |
| 4 | Guarding the head/done split (doc 03 Q3) | **Golden SSE transcripts**, written by a backend test and read by a web test. No TypeScript codegen. |
| 5 | Who owns "can't ask while busy" | **The module.** `isBusy(state)` in `reduce.ts` (loading or streaming). The binding's `ask()` returns `'busy'` and does nothing; the page passes the phase to `SearchForm`. The page's own `live` guard goes. |
| 6 | `stream.ts`'s helpers | **Moved as they are to `answer-stream/live.ts`**, tests to `live.test.ts`. The reducer calls them. Doc 04 later moves or replaces that one file. |
| 7 | Keeping the transcripts in sync | **Committed; the backend test compares and fails on any difference.** `UPDATE_GOLDEN=1 pytest tests/test_stream_golden.py` rewrites them. |
| 8 | Which transcripts | **Three:** `answered` (results, thinking, delta×N, done), `cached` (results, done with `cached_at`), `failed` (results, thinking, error, done with no answer). `cached_at` is normalised to a fixed value. |
| 9 | Impossible sequences | The reducer turns them into `failed` (before `results`) or a cut-off answer (after), and **logs a `console.error`** naming the event and phase. No client error reporting. |
| 10 | Process | This spec and a plan, on a new branch. The review README and doc 03 are updated to say the bug was fixed by PR #21 and point here. |
| 11 | No AI spend | The golden test runs the pipeline with the conftest fake answerers and **asserts** the answerer is a test fake, so it can never reach Gemini, whatever the environment. |
| 12 | Replay | *(Taken while writing the spec.)* The binding gets `load(fetch)`: it aborts any stream, goes to `loading`, then `result`, or `failed` with the message. It returns the response, `'aborted'` (a later `ask`, `load` or `reset` took over) or the `Error`. The page keeps `replayFailed` for the title and hint, and its stale-load counter goes. |
| 13 | Line endings | *(Taken while writing the spec.)* The `.sse` files are compared byte for byte and parsed on blank lines, and this repo is checked out with `core.autocrlf=true` on Windows, so `.gitattributes` gets `*.sse text eol=lf`. |
| 14 | The 429 message | *(Taken while writing the spec.)* `api.ts` exports `TOO_MANY_REQUESTS`, the text written three times today. `httpSource`, the fixtures and the page use it, so `ask()` can return `'rate-limited'` without carrying the message. |

## Interface

```ts
// answer-stream/protocol.ts
export type StreamEvent =
  | { type: 'results'; head: StreamHead }
  | { type: 'thinking' }
  | { type: 'delta'; text: string }
  | { type: 'error'; message: string }
  | { type: 'done'; done: StreamDone };

export const HEAD_KEYS = ['results', 'degraded', 'answers_remaining', 'cached_at', 'sources'] as const;
export const DONE_KEYS = ['results', 'answer', 'citations', 'rule_references',
                          'citation_stats', 'answer_complete'] as const;
export function decode(name: string, data: string): StreamEvent | null;   // null: unknown event
export function splitResponse(r: QueryResponse, sources: Citation[]): { head: StreamHead; done: StreamDone };

// answer-stream/sources.ts
/** Yields the protocol's events in order. Throws RateLimitedError or Error
 * before any event; throws StreamEndedError if the transport ends without
 * `done`; rejects with an AbortError when `signal` aborts. */
export type AnswerSource = (query: string, opts: { fresh: boolean; signal: AbortSignal })
  => AsyncIterable<StreamEvent>;
export const httpSource: AnswerSource;

// answer-stream/reduce.ts
export type AnswerState =
  | { phase: 'idle' }
  | { phase: 'loading' }
  | { phase: 'streaming'; query: string; head: StreamHead; draft: string }  // query: from reduce's argument
  | { phase: 'result'; response: QueryResponse }
  | { phase: 'failed'; message: string };
export type Broken = { type: 'broken'; message: string };
export function reduce(state: AnswerState, event: StreamEvent | Broken, query: string): AnswerState;
export function shown(state: AnswerState, visibleDraft: string): QueryResponse | null;
export function isBusy(state: AnswerState): boolean;

// answer-stream/answerStream.svelte.ts
export function createAnswerStream(source: AnswerSource): {
  readonly state: AnswerState;
  readonly shown: QueryResponse | null;   // uses the rAF-throttled draft while streaming
  readonly busy: boolean;
  ask(query: string, fresh?: boolean): Promise<'ok' | 'busy' | 'rate-limited'>;
  load<T extends QueryResponse>(fetch: (signal: AbortSignal) => Promise<T>): Promise<T | 'aborted' | Error>;
  reset(): void;
};

// fixtures/index.ts
export function fixtureSource(name: string): AnswerSource;
```

`AnswerSource` is the seam: `httpSource` and `fixtureSource` are its two
adapters. The page picks one at load (`?mock=` in dev imports the fixtures
lazily, as today, so they stay out of the production bundle).

What a caller can rely on:

- **`ask` while busy** returns `'busy'` and changes nothing.
- **429** (`RateLimitedError` before any event) restores the state from
  before the ask and returns `'rate-limited'`. Since asking is blocked while
  busy, that state is `idle`, `result` or `failed`, never a half-built one.
- **Transport ends or fails after `results`:** a cut-off `result` built from
  the draft (`answer_complete: false`, or `null` with no text), as the
  page's catch block does today.
- **Transport fails before `results`:** `failed` with the error's message.
- **Abort** (a new `ask`, `load` or `reset`): the old run stops writing
  state. Breaking out of the generator runs its `finally`, which cancels
  the reader and closes the connection.
- **`done` is authoritative:** `merge(query, head, done)` replaces the draft.
  Head-only fields (`degraded`, `answers_remaining`, `cached_at`) come from
  the head.

## Tests

- **`reduce.test.ts`** (new): doc 03's case table, minus the "streaming,
  then 429" row, plus:
  - a cut-off result, then a 429 on the next ask, keeps the cut-off answer
    (this is a binding test with a scripted source, because 429 is not an
    event);
  - `ask` while loading or streaming returns `'busy'`;
  - each impossible sequence logs once.
- **`live.test.ts`**: `stream.test.ts` moved over unchanged.
- **`sources.test.ts`**: `api.test.ts`'s `streamQuery` cases ported to
  `httpSource` (split frames, 429, ended without `done`, unknown event
  ignored, abort cancels the reader).
- **`fixtures.test.ts`**: `play()` collects yielded events. Same
  expectations.
- **Golden contract test:**
  - Backend `tests/test_stream_golden.py` posts the three scenarios through
    `TestClient` with fake answerers, normalises `cached_at`, and compares
    with `mtg-web/src/lib/answer-stream/golden/{answered,cached,failed}.sse`.
    Backend CI checks out the whole repo, so the path resolves.
  - Web `golden.test.ts` feeds each file through `httpSource` (a stubbed
    `fetch`) and `reduce`. It asserts the head's keys are `HEAD_KEYS`, that
    `done` has every `DONE_KEYS` key, and the final `QueryResponse` for each
    scenario.
- **Replaced, not layered:** `stream.test.ts`, the `streamQuery` block in
  `api.test.ts`, the handler recorder in `fixtures.test.ts`.
- **Kept unchanged, as the behaviour check:** every Playwright test.

## Out of scope

- Doc 04's citation grammar (`live.ts` is its seam).
- Slot takeover (doc 01 decision 5, doc 06).
- `submitQuery` and `/api/v1/query`, which nothing in the web app calls any
  more. Worth a separate cleanup.
- Client error reporting (decision 9).
