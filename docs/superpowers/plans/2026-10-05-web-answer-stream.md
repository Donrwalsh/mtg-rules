# Web Answer Stream Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the search page's answer-stream state machine into a tested `answer-stream` module (pure reducer, Svelte binding, event-source adapters), and add golden SSE transcripts that pin the protocol across the API/web seam.

**Architecture:** Event sources (`httpSource`, `fixtureSource`) are async generators of `StreamEvent`s behind one `AnswerSource` port. A pure `reduce(state, event, query)` turns them into an `AnswerState`, and `shown(state, draft)` computes what the page renders. `createAnswerStream(source)` in a `.svelte.ts` file binds that to runes, with abort, the rAF throttle and the busy rule. The page keeps layout, replay, selection and the source sheet.

**Tech Stack:** SvelteKit 2 / Svelte 5 runes, TypeScript, Vitest (node environment), Playwright (fixtures, no backend), FastAPI + pytest for the backend golden test.

**Spec:** [`docs/superpowers/specs/2026-10-05-web-answer-stream-design.md`](../specs/2026-10-05-web-answer-stream-design.md). Background: [review doc 03](../../architecture/2026-09-30-streaming-review/03-web-answer-stream.md).

## Global Constraints

- No visible behaviour change: every Playwright test in `mtg-web/e2e/` passes **unchanged**.
- No AI spend anywhere: backend tests use the conftest fake answerers, and the golden test asserts it.
- Never iterate a `ReadableStream` with `for await`: Safari has no async iterator on it. Use `getReader()`.
- `src/lib/fixtures` stays dynamically imported (`await import('$lib/fixtures')`), so it never enters the production bundle.
- The rAF throttle stays: at most one draft re-layout per animation frame (streaming spec decision 11).
- Replace, don't layer: by the end of the branch `stream.ts`, `stream.test.ts`, `api.test.ts`, `streamQuery`, `StreamHandlers` and `mockStream` are gone.
- Web commands run in `mtg-web/`: `npm run check`, `npm test`, `npx playwright test`. Backend commands run in `mtg-api/`: `pytest`, `ruff check .`, `ruff format --check .` (line length 100).
- Commit messages follow the repo's style (`refactor(web): …`, `test(api): …`) and end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## File map

```
mtg-web/src/lib/answer-stream/
  protocol.ts            StreamHead, StreamDone, StreamEvent, HEAD_KEYS, DONE_KEYS, decode, splitResponse
  protocol.test.ts
  live.ts                moved from src/lib/stream.ts unchanged (doc 04's seam)
  live.test.ts           moved from src/lib/stream.test.ts unchanged
  reduce.ts              AnswerState, reduce, shown, isBusy
  reduce.test.ts
  testing.ts             test builders: head(), done(), result(), citation()
  sources.ts             AnswerSource, httpSource, StreamEndedError
  sources.test.ts        replaces src/lib/api.test.ts
  answerStream.svelte.ts createAnswerStream
  answerStream.test.ts
  golden/{answered,cached,failed}.sse   written by the backend test
  golden.test.ts
mtg-web/src/lib/api.ts             + TOO_MANY_REQUESTS; − stream types, streamQuery
mtg-web/src/lib/fixtures/index.ts  mockStream → fixtureSource
mtg-web/src/lib/fixtures/fixtures.test.ts
mtg-web/src/routes/+page.svelte    uses createAnswerStream
mtg-api/tests/test_stream_golden.py
.gitattributes                     + *.sse text eol=lf
```

---

### Task 1: Protocol module, and move the live helpers

**Files:**
- Create: `mtg-web/src/lib/answer-stream/protocol.ts`, `mtg-web/src/lib/answer-stream/protocol.test.ts`
- Move: `mtg-web/src/lib/stream.ts` → `mtg-web/src/lib/answer-stream/live.ts`, `mtg-web/src/lib/stream.test.ts` → `mtg-web/src/lib/answer-stream/live.test.ts`
- Modify: `mtg-web/src/lib/api.ts` (stream types move out; temporary re-export), `mtg-web/src/routes/+page.svelte:36` and `mtg-web/src/lib/fixtures/index.ts:16` (import path of the live helpers)

**Interfaces:**
- Produces: `StreamHead`, `StreamDone`, `StreamEvent`, `HEAD_KEYS`, `DONE_KEYS`, `decode(name, data): StreamEvent | null`, `splitResponse(r, sources): { head; done }` from `$lib/answer-stream/protocol`. `visibleDraft`, `liveCitations`, `liveResults` from `$lib/answer-stream/live`.

- [ ] **Step 1: Move the live helpers with git**

```bash
cd mtg-web
mkdir -p src/lib/answer-stream
git mv src/lib/stream.ts src/lib/answer-stream/live.ts
git mv src/lib/stream.test.ts src/lib/answer-stream/live.test.ts
```

In `live.ts` change `import type { Citation, QueryResult } from './api';` to `from '../api'`. In `live.test.ts` change `from './api'` to `from '../api'` and `from './stream'` to `from './live'`. In `+page.svelte` change `from '$lib/stream'` to `from '$lib/answer-stream/live'`. In `fixtures/index.ts` change `from '../stream'` to `from '../answer-stream/live'`.

- [ ] **Step 2: Write the failing protocol test**

`src/lib/answer-stream/protocol.test.ts`:

```ts
import { describe, expect, it } from 'vitest';
import type { QueryResponse } from '../api';
import { decode, DONE_KEYS, HEAD_KEYS, splitResponse } from './protocol';

describe('decode', () => {
  it('turns each wire event into a StreamEvent', () => {
    expect(decode('results', '{"results":[],"sources":[]}')).toEqual({
      type: 'results',
      head: { results: [], sources: [] }
    });
    expect(decode('thinking', '{}')).toEqual({ type: 'thinking' });
    expect(decode('delta', '{"text":"Yes"}')).toEqual({ type: 'delta', text: 'Yes' });
    expect(decode('error', '{"message":"Gemini 500"}')).toEqual({
      type: 'error',
      message: 'Gemini 500'
    });
    expect(decode('done', '{"answer":"Yes"}')).toEqual({ type: 'done', done: { answer: 'Yes' } });
  });

  it('ignores events it does not know', () => {
    expect(decode('usage', '{}')).toBeNull();
  });
});

describe('splitResponse', () => {
  const r: QueryResponse = {
    query: 'q',
    results: [],
    answer: 'Yes',
    citations: [],
    rule_references: [],
    citation_stats: { cited_count: 0, invalid_count: 0, uncited_answer: false },
    answer_complete: true,
    cached_at: null,
    degraded: null,
    answers_remaining: 3
  };

  it('puts every field but query in exactly one of head and done, results in both', () => {
    const { head, done } = splitResponse(r, []);
    expect(Object.keys(head).sort()).toEqual([...HEAD_KEYS].sort());
    expect(Object.keys(done).sort()).toEqual([...DONE_KEYS].sort());
    const both = new Set([...HEAD_KEYS, ...DONE_KEYS, 'query']);
    both.delete('sources');
    expect([...both].sort()).toEqual(Object.keys(r).sort());
  });
});
```

- [ ] **Step 3: Run it to see it fail**

Run: `npx vitest run src/lib/answer-stream/protocol.test.ts`
Expected: FAIL, cannot resolve `./protocol`.

- [ ] **Step 4: Write `protocol.ts`**

```ts
// The answer stream's wire protocol (POST /api/v1/query/stream), as the
// client sees it: `results` first, then `thinking`, `delta`s, at most one
// `error`, and always `done` last. The server's StreamHead / StreamDone
// (mtg_api/models.py) are the other end; golden/*.sse pins the two together.
import type { Citation, QueryResponse } from '../api';

// The first event: everything but the answer.
export interface StreamHead {
  results: QueryResponse['results'];
  // A citation for every numbered source, so markers work while streaming.
  sources: Citation[];
  degraded: QueryResponse['degraded'];
  answers_remaining: number | null;
  cached_at: string | null;
}

// The last event: the validated answer. `results` again, with `cited` set.
export type StreamDone = Omit<
  QueryResponse,
  'query' | 'degraded' | 'answers_remaining' | 'cached_at'
>;

export type StreamEvent =
  | { type: 'results'; head: StreamHead }
  | { type: 'thinking' }
  | { type: 'delta'; text: string }
  // Generation failed; `done` still follows.
  | { type: 'error'; message: string }
  | { type: 'done'; done: StreamDone };

export const HEAD_KEYS = [
  'results',
  'degraded',
  'answers_remaining',
  'cached_at',
  'sources'
] as const satisfies readonly (keyof StreamHead)[];

export const DONE_KEYS = [
  'results',
  'answer',
  'citations',
  'rule_references',
  'citation_stats',
  'answer_complete'
] as const satisfies readonly (keyof StreamDone)[];

/** One SSE event as a StreamEvent; null for a name this client doesn't know. */
export function decode(name: string, data: string): StreamEvent | null {
  switch (name) {
    case 'results':
      return { type: 'results', head: JSON.parse(data) };
    case 'thinking':
      return { type: 'thinking' };
    case 'delta':
      return { type: 'delta', text: JSON.parse(data).text };
    case 'error':
      return { type: 'error', message: JSON.parse(data).message };
    case 'done':
      return { type: 'done', done: JSON.parse(data) };
    default:
      return null;
  }
}

/** A whole response as the stream's first and last events (for fixtures). */
export function splitResponse(
  r: QueryResponse,
  sources: Citation[]
): { head: StreamHead; done: StreamDone } {
  return {
    head: {
      results: r.results,
      degraded: r.degraded,
      answers_remaining: r.answers_remaining,
      cached_at: r.cached_at,
      sources
    },
    done: {
      results: r.results,
      answer: r.answer,
      citations: r.citations,
      rule_references: r.rule_references,
      citation_stats: r.citation_stats,
      answer_complete: r.answer_complete
    }
  };
}
```

- [ ] **Step 5: Point `api.ts` at the moved types**

In `src/lib/api.ts`, delete the `StreamHead` interface and the `StreamDone` type (with their comments, lines 106–120) and put this in their place. It's removed in Task 6, once nothing imports the types from `api.ts`.

```ts
// Moved to answer-stream/protocol; re-exported until the page switches over.
export type { StreamDone, StreamHead } from './answer-stream/protocol';
import type { StreamDone, StreamHead } from './answer-stream/protocol';
```

Also add, next to `RateLimitedError`:

```ts
export const TOO_MANY_REQUESTS = 'Too many requests. Wait a few seconds and try again.';
```

and use it in `submitQuery` and `streamQuery` in place of the two literal copies.

- [ ] **Step 6: Run the tests and the type check**

Run: `npm test && npm run check`
Expected: all pass, 0 errors.

- [ ] **Step 7: Commit**

```bash
git add -A src/lib
git commit -m "refactor(web): answer-stream protocol module, live helpers moved into it"
```

---

### Task 2: The reducer

**Files:**
- Create: `mtg-web/src/lib/answer-stream/testing.ts`, `mtg-web/src/lib/answer-stream/reduce.ts`, `mtg-web/src/lib/answer-stream/reduce.test.ts`

**Interfaces:**
- Consumes: `StreamEvent`, `StreamHead`, `StreamDone` (Task 1), `visibleDraft`, `liveCitations`, `liveResults` (Task 1).
- Produces:
  ```ts
  type AnswerState =
    | { phase: 'idle' }
    | { phase: 'loading' }
    | { phase: 'streaming'; query: string; head: StreamHead; draft: string }
    | { phase: 'result'; response: QueryResponse }
    | { phase: 'failed'; message: string };
  type Broken = { type: 'broken'; message: string };
  function reduce(state: AnswerState, event: StreamEvent | Broken, query: string): AnswerState;
  function shown(state: AnswerState, draft: string): QueryResponse | null;
  function isBusy(state: AnswerState): boolean;
  ```
  And test builders `head(over?)`, `done(over?)`, `result(title, cited?)`, `citation(n)` from `testing.ts`.

- [ ] **Step 1: Write the test builders**

`src/lib/answer-stream/testing.ts`:

```ts
// Builders for answer-stream tests.
import type { Citation, QueryResult } from '../api';
import type { StreamDone, StreamHead } from './protocol';

export const citation = (number: number): Citation => ({
  number,
  source_type: 'rule',
  title: `Rule ${number}`,
  rule_id: String(number),
  card_name: null,
  oracle_id: null,
  text: 't',
  url: null,
  published_at: null
});

export const result = (title: string, cited = false): QueryResult => ({
  source: 'rule',
  title,
  text: 't',
  score: 1,
  cited
});

export function head(over: Partial<StreamHead> = {}): StreamHead {
  return {
    results: [result('a'), result('b')],
    sources: [citation(1), citation(2)],
    degraded: null,
    answers_remaining: 7,
    cached_at: null,
    ...over
  };
}

export function done(over: Partial<StreamDone> = {}): StreamDone {
  return {
    results: [result('a', true), result('b')],
    answer: 'Yes [1].',
    citations: [citation(1)],
    rule_references: [],
    citation_stats: { cited_count: 1, invalid_count: 0, uncited_answer: false },
    answer_complete: true,
    ...over
  };
}
```

- [ ] **Step 2: Write the failing reducer tests**

`src/lib/answer-stream/reduce.test.ts`:

```ts
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { QueryResponse } from '../api';
import { noticeFor } from '../status';
import type { StreamEvent } from './protocol';
import { isBusy, reduce, shown, type AnswerState, type Broken } from './reduce';
import { done, head } from './testing';

const Q = 'Does trample work?';
const results = (h = head()): StreamEvent => ({ type: 'results', head: h });
const thinking: StreamEvent = { type: 'thinking' };
const delta = (text: string): StreamEvent => ({ type: 'delta', text });
const finished = (d = done()): StreamEvent => ({ type: 'done', done: d });
const broken: Broken = { type: 'broken', message: 'The answer stopped arriving.' };

/** Reduces events starting from `loading`, as `ask()` does. */
function run(...events: (StreamEvent | Broken)[]): AnswerState {
  return events.reduce<AnswerState>((s, e) => reduce(s, e, Q), { phase: 'loading' });
}

function response(s: AnswerState): QueryResponse {
  if (s.phase !== 'result') throw new Error(`expected result, got ${s.phase}`);
  return s.response;
}

afterEach(() => vi.restoreAllMocks());

describe('reduce', () => {
  it('a full answer ends with the answer from done, not the draft', () => {
    const r = response(run(results(), thinking, delta('Dra'), delta('ft'), finished()));
    expect(r.query).toBe(Q);
    expect(r.answer).toBe('Yes [1].');
    expect(r.answer_complete).toBe(true);
    expect(r.answers_remaining).toBe(7);
    expect(r.results[0].cited).toBe(true);
  });

  it('a cache hit keeps cached_at from the head', () => {
    const r = response(run(results(head({ cached_at: '2026-01-01T00:00:00Z' })), finished()));
    expect(r.cached_at).toBe('2026-01-01T00:00:00Z');
  });

  it('error then done with no answer shows the no-answer notice', () => {
    const r = response(
      run(
        results(),
        thinking,
        { type: 'error', message: 'Gemini 500' },
        finished(done({ answer: null, citations: [], answer_complete: null }))
      )
    );
    expect(noticeFor(r)).toBe('no_answer');
  });

  it('a stream broken after text keeps the text as a cut-off answer', () => {
    const r = response(run(results(), delta('Yes [1]. And [2'), broken));
    expect(r.answer).toBe('Yes [1]. And');
    expect(r.answer_complete).toBe(false);
    expect(r.citations.map((c) => c.number)).toEqual([1]);
    expect(r.results.map((x) => x.cited)).toEqual([true, false]);
  });

  it('a stream broken before any text is a result with no answer', () => {
    const r = response(run(results(), thinking, broken));
    expect(r.answer).toBeNull();
    expect(r.answer_complete).toBeNull();
  });

  it('a stream broken before results fails with its message', () => {
    expect(run(broken)).toEqual({ phase: 'failed', message: 'The answer stopped arriving.' });
  });

  it.each([
    ['delta before results', [delta('Yes')]],
    ['done before results', [finished()]],
    ['thinking before results', [thinking]]
  ])('%s fails and logs once', (_name, events) => {
    const error = vi.spyOn(console, 'error').mockImplementation(() => {});
    expect(run(...events).phase).toBe('failed');
    expect(error).toHaveBeenCalledOnce();
  });

  it('results twice keeps what was written as a cut-off answer, and logs', () => {
    const error = vi.spyOn(console, 'error').mockImplementation(() => {});
    const r = response(run(results(), delta('Yes [1].'), results()));
    expect(r.answer).toBe('Yes [1].');
    expect(r.answer_complete).toBe(false);
    expect(error).toHaveBeenCalledOnce();
  });

  it('an event after the result leaves the result alone, and logs', () => {
    const error = vi.spyOn(console, 'error').mockImplementation(() => {});
    const r = response(run(results(), finished(), delta('late')));
    expect(r.answer).toBe('Yes [1].');
    expect(error).toHaveBeenCalledOnce();
  });
});

describe('shown', () => {
  it('while streaming: the draft with live citations, unfinished marker held back', () => {
    const s = run(results(), delta('A [1]. B [2'));
    const r = shown(s, 'A [1]. B [2')!;
    expect(r.answer).toBe('A [1]. B ');
    expect(r.citations.map((c) => c.number)).toEqual([1]);
    expect(r.results.map((x) => x.cited)).toEqual([true, false]);
    expect(r.query).toBe(Q);
  });

  it('uses the draft it is given (the throttled copy), not the state draft', () => {
    const s = run(results(), delta('Yes [1].'));
    expect(shown(s, '')!.answer).toBe('');
  });

  it('is the response once there is one, and null before', () => {
    const s = run(results(), finished());
    expect(shown(s, 'ignored')).toBe(response(s));
    expect(shown({ phase: 'idle' }, '')).toBeNull();
    expect(shown({ phase: 'loading' }, '')).toBeNull();
    expect(shown({ phase: 'failed', message: 'x' }, '')).toBeNull();
  });
});

describe('isBusy', () => {
  it('is true while loading or streaming', () => {
    expect(isBusy({ phase: 'idle' })).toBe(false);
    expect(isBusy({ phase: 'loading' })).toBe(true);
    expect(isBusy(run(results()))).toBe(true);
    expect(isBusy(run(results(), finished()))).toBe(false);
    expect(isBusy({ phase: 'failed', message: 'x' })).toBe(false);
  });
});
```

- [ ] **Step 3: Run them to see them fail**

Run: `npx vitest run src/lib/answer-stream/reduce.test.ts`
Expected: FAIL, cannot resolve `./reduce`.

- [ ] **Step 4: Write `reduce.ts`**

```ts
// The answer stream's states and how protocol events move between them.
// Pure: the Svelte binding (answerStream.svelte.ts) owns abort and timing.
import type { QueryResponse } from '../api';
import { liveCitations, liveResults, visibleDraft } from './live';
import type { StreamDone, StreamEvent, StreamHead } from './protocol';

export type AnswerState =
  | { phase: 'idle' }
  | { phase: 'loading' }
  | { phase: 'streaming'; query: string; head: StreamHead; draft: string }
  | { phase: 'result'; response: QueryResponse }
  | { phase: 'failed'; message: string };

type Streaming = Extract<AnswerState, { phase: 'streaming' }>;

/** The transport ended or failed without `done`. */
export type Broken = { type: 'broken'; message: string };

/** The response as far as the head knows it: no answer yet. */
function partial(query: string, head: StreamHead): QueryResponse {
  return {
    query,
    results: head.results,
    degraded: head.degraded,
    answers_remaining: head.answers_remaining,
    cached_at: head.cached_at,
    answer: null,
    citations: [],
    rule_references: [],
    citation_stats: { cited_count: 0, invalid_count: 0, uncited_answer: false },
    answer_complete: null
  };
}

/** `done` is authoritative; the head-only fields come from the head. */
function merge(query: string, head: StreamHead, done: StreamDone): QueryResponse {
  return { ...partial(query, head), ...done };
}

/** What was written before the stream broke, with its live citations. */
function cutOff(s: Streaming): QueryResponse {
  const base = partial(s.query, s.head);
  const answer = visibleDraft(s.draft).trimEnd() || null;
  const citations = answer ? liveCitations(answer, s.head.sources) : [];
  return {
    ...base,
    answer,
    citations,
    results: liveResults(base.results, citations),
    answer_complete: answer ? false : null
  };
}

/** An event the protocol doesn't allow here: a server or proxy bug. Handled
 * like a broken stream, so the page never sees an inconsistent state. */
function impossible(state: AnswerState, event: StreamEvent): AnswerState {
  console.error(`answer stream: unexpected '${event.type}' while ${state.phase}`);
  if (state.phase === 'streaming') return { phase: 'result', response: cutOff(state) };
  if (state.phase === 'result') return state;
  return { phase: 'failed', message: "The answer couldn't be read." };
}

export function reduce(
  state: AnswerState,
  event: StreamEvent | Broken,
  query: string
): AnswerState {
  if (event.type === 'broken') {
    if (state.phase === 'streaming') return { phase: 'result', response: cutOff(state) };
    if (state.phase === 'loading') return { phase: 'failed', message: event.message };
    return state;
  }
  if (event.type === 'results') {
    if (state.phase !== 'loading') return impossible(state, event);
    return { phase: 'streaming', query, head: event.head, draft: '' };
  }
  if (state.phase !== 'streaming') return impossible(state, event);
  switch (event.type) {
    case 'thinking':
    case 'error': // `done` follows with no answer
      return state;
    case 'delta':
      return { ...state, draft: state.draft + event.text };
    case 'done':
      return { phase: 'result', response: merge(state.query, state.head, event.done) };
  }
}

/** What the page renders. While streaming that is `draft` (the binding's
 * once-per-frame copy) with live citations in place of validated ones. */
export function shown(state: AnswerState, draft: string): QueryResponse | null {
  if (state.phase === 'result') return state.response;
  if (state.phase !== 'streaming') return null;
  const base = partial(state.query, state.head);
  const citations = liveCitations(draft, state.head.sources);
  return {
    ...base,
    answer: visibleDraft(draft),
    citations,
    results: liveResults(base.results, citations)
  };
}

/** Asking again must wait: the server holds this visitor's one generation
 * slot until an answer it started is finished, even an abandoned one. */
export function isBusy(state: AnswerState): boolean {
  return state.phase === 'loading' || state.phase === 'streaming';
}
```

- [ ] **Step 5: Run the tests**

Run: `npx vitest run src/lib/answer-stream/reduce.test.ts && npm run check`
Expected: PASS, 0 errors.

- [ ] **Step 6: Commit**

```bash
git add src/lib/answer-stream/testing.ts src/lib/answer-stream/reduce.ts src/lib/answer-stream/reduce.test.ts
git commit -m "feat(web): answer-stream reducer, with its case table"
```

---

### Task 3: The HTTP source

**Files:**
- Create: `mtg-web/src/lib/answer-stream/sources.ts`, `mtg-web/src/lib/answer-stream/sources.test.ts`

**Interfaces:**
- Consumes: `decode`, `StreamEvent` (Task 1); `ADMIN_HEADER`, `RateLimitedError`, `TOO_MANY_REQUESTS` from `api.ts`; `SseParser` from `sse.ts`.
- Produces:
  ```ts
  type AnswerSource = (query: string, opts: { fresh: boolean; signal: AbortSignal })
    => AsyncIterable<StreamEvent>;
  const httpSource: AnswerSource;
  class StreamEndedError extends Error {}
  ```

`streamQuery` stays in `api.ts` until Task 6 switches the page.

- [ ] **Step 1: Write the failing tests**

`src/lib/answer-stream/sources.test.ts`:

```ts
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ADMIN_HEADER, RateLimitedError } from '../api';
import type { StreamEvent } from './protocol';
import { httpSource, StreamEndedError } from './sources';

/** A response whose body sends `chunks`, then closes unless `open`. */
function sseResponse(chunks: string[], { status = 200, open = false, cancel = vi.fn() } = {}) {
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      const enc = new TextEncoder();
      for (const c of chunks) controller.enqueue(enc.encode(c));
      if (!open) controller.close();
    },
    cancel
  });
  return new Response(body, { status, headers: { 'content-type': 'text/event-stream' } });
}

async function collect(fresh = false): Promise<StreamEvent[]> {
  const out: StreamEvent[] = [];
  for await (const e of httpSource('q', { fresh, signal: new AbortController().signal })) {
    out.push(e);
  }
  return out;
}

const DONE = 'event: done\ndata: {"answer":"Yes"}\n\n';

afterEach(() => vi.unstubAllGlobals());

describe('httpSource', () => {
  it('yields each event, across chunk boundaries', async () => {
    const fetch = vi.fn(async () =>
      sseResponse([
        'event: results\ndata: {"results":[],"sources":[],"degraded":null,',
        '"answers_remaining":null,"cached_at":null}\n\nevent: thinking\ndata: {}\n\n',
        'event: delta\ndata: {"text":"Yes"}\n\n' + DONE
      ])
    );
    vi.stubGlobal('fetch', fetch);
    const events = await collect();
    expect(events.map((e) => e.type)).toEqual(['results', 'thinking', 'delta', 'done']);
    expect(events[2]).toEqual({ type: 'delta', text: 'Yes' });
    expect(fetch).toHaveBeenCalledWith(
      '/api/v1/query/stream',
      expect.objectContaining({ method: 'POST', body: JSON.stringify({ query: 'q' }) })
    );
  });

  it('asks for a fresh answer with the admin header', async () => {
    const fetch = vi.fn(async () => sseResponse([DONE]));
    vi.stubGlobal('fetch', fetch);
    await collect(true);
    expect(fetch).toHaveBeenCalledWith(
      '/api/v1/query/stream',
      expect.objectContaining({
        body: JSON.stringify({ query: 'q', fresh: true }),
        headers: expect.objectContaining({ [ADMIN_HEADER]: '1' })
      })
    );
  });

  it('maps 429 to RateLimitedError before any event', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('', { status: 429 })));
    await expect(collect()).rejects.toBeInstanceOf(RateLimitedError);
  });

  it('throws on any other HTTP error', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('', { status: 502 })));
    await expect(collect()).rejects.toThrow('query failed: 502');
  });

  it('throws StreamEndedError when the stream ends without done', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => sseResponse(['event: thinking\ndata: {}\n\n'])));
    await expect(collect()).rejects.toBeInstanceOf(StreamEndedError);
  });

  it('skips events it does not know', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => sseResponse(['event: usage\ndata: {}\n\n' + DONE])));
    expect((await collect()).map((e) => e.type)).toEqual(['done']);
  });

  it('stops at done and closes the connection', async () => {
    const cancel = vi.fn();
    vi.stubGlobal('fetch', vi.fn(async () => sseResponse([DONE], { open: true, cancel })));
    expect((await collect()).map((e) => e.type)).toEqual(['done']);
    expect(cancel).toHaveBeenCalled();
  });

  it('closes the connection when the caller stops reading', async () => {
    const cancel = vi.fn();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        sseResponse(['event: thinking\ndata: {}\n\n'], { open: true, cancel })
      )
    );
    for await (const _ of httpSource('q', { fresh: false, signal: new AbortController().signal })) {
      break;
    }
    expect(cancel).toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run them to see them fail**

Run: `npx vitest run src/lib/answer-stream/sources.test.ts`
Expected: FAIL, cannot resolve `./sources`.

- [ ] **Step 3: Write `sources.ts`**

```ts
// Where answer-stream events come from. Two adapters: httpSource (the API)
// and fixtureSource (src/lib/fixtures, dev and Playwright only).
import { ADMIN_HEADER, RateLimitedError, TOO_MANY_REQUESTS } from '../api';
import { SseParser } from '../sse';
import { decode, type StreamEvent } from './protocol';

export class StreamEndedError extends Error {}

/** Yields the protocol's events in order. Throws RateLimitedError or Error
 * before any event; throws StreamEndedError if the transport ends without
 * `done`; rejects with an AbortError when `signal` aborts. */
export type AnswerSource = (
  query: string,
  opts: { fresh: boolean; signal: AbortSignal }
) => AsyncIterable<StreamEvent>;

export const httpSource: AnswerSource = async function* (query, { fresh, signal }) {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (fresh) headers[ADMIN_HEADER] = '1';
  const resp = await fetch('/api/v1/query/stream', {
    method: 'POST',
    headers,
    body: JSON.stringify(fresh ? { query, fresh } : { query }),
    signal
  });
  if (resp.status === 429) throw new RateLimitedError(TOO_MANY_REQUESTS);
  if (!resp.ok || !resp.body) throw new Error(`query failed: ${resp.status}`);
  // An explicit reader, not `for await` over the body: Safari has no
  // ReadableStream async iterator.
  const reader = resp.body.pipeThrough(new TextDecoderStream()).getReader();
  const parser = new SseParser();
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      for (const e of parser.push(value)) {
        const event = decode(e.event, e.data);
        if (!event) continue;
        yield event;
        if (event.type === 'done') return;
      }
    }
  } finally {
    // Closes the connection on `done`, on abort, or when the caller stops.
    await reader.cancel().catch(() => {});
  }
  throw new StreamEndedError('The answer stopped arriving before it finished.');
};
```

- [ ] **Step 4: Run the tests**

Run: `npx vitest run src/lib/answer-stream/sources.test.ts && npm run check`
Expected: PASS, 0 errors.

- [ ] **Step 5: Commit**

```bash
git add src/lib/answer-stream/sources.ts src/lib/answer-stream/sources.test.ts
git commit -m "feat(web): httpSource, the answer stream as an async generator"
```

---

### Task 4: The fixture source

**Files:**
- Modify: `mtg-web/src/lib/fixtures/index.ts:1-16` (imports and header comment), `:283-392` (`wait` stays; `headOf`/`doneOf`/`mockStream` become `fixtureSource`)
- Modify: `mtg-web/src/lib/fixtures/fixtures.test.ts:50-106`

**Interfaces:**
- Consumes: `AnswerSource` (Task 3), `splitResponse`, `StreamEvent` (Task 1), `TOO_MANY_REQUESTS` (Task 1).
- Produces: `fixtureSource(name: string): AnswerSource` from `$lib/fixtures`. `mockQuery` stays (its tests use it).

`mockStream` stays until Task 6, because the page still calls it. It is rewritten here as a thin wrapper over `fixtureSource`, so there is one implementation.

- [ ] **Step 1: Rewrite the `mockStream` tests against `fixtureSource`**

In `fixtures.test.ts`, replace the imports and everything from `function record()` to the end with:

```ts
import { describe, expect, it, vi } from 'vitest';
import { RateLimitedError } from '../api';
import type { StreamDone, StreamEvent } from '../answer-stream/protocol';
import { fixtureSource, mockQuery } from './index';
```

```ts
async function play(name: string) {
  vi.useFakeTimers();
  const events: StreamEvent[] = [];
  const run = (async () => {
    const signal = new AbortController().signal;
    for await (const e of fixtureSource(name)('q', { fresh: false, signal })) events.push(e);
  })();
  await vi.runAllTimersAsync();
  await run;
  vi.useRealTimers();
  // Consecutive deltas collapse to one name, as the old recorder did.
  const names = events
    .map((e) => e.type)
    .filter((t, i, all) => t !== 'delta' || all[i - 1] !== 'delta');
  const text = events.map((e) => (e.type === 'delta' ? e.text : '')).join('');
  const last = events.at(-1);
  const done: StreamDone | null = last?.type === 'done' ? last.done : null;
  return { names, text, done };
}

describe('fixtureSource', () => {
  it.each([
    ['streaming', ['results', 'thinking', 'delta', 'done'], true],
    ['cutoff', ['results', 'thinking', 'delta', 'done'], false],
    ['streamerror', ['results', 'thinking', 'error', 'done'], null],
    ['answered', ['results', 'done'], true],
    ['quota', ['results', 'done'], null]
  ])('%s sends %j', async (name, expected, complete) => {
    const r = await play(name);
    expect(r.names).toEqual(expected);
    expect(r.done?.answer_complete).toBe(complete);
  });

  it('streaming deltas add up to the answer', async () => {
    const r = await play('streaming');
    expect(r.text).toBe(r.done?.answer);
  });

  it('error and ratelimited throw before any event', async () => {
    const signal = new AbortController().signal;
    const first = (name: string) => fixtureSource(name)('q', { fresh: false, signal })
      [Symbol.asyncIterator]()
      .next();
    await expect(first('error')).rejects.toThrow('query failed: 502');
    await expect(first('ratelimited')).rejects.toBeInstanceOf(RateLimitedError);
  });

  it('stops when aborted', async () => {
    const controller = new AbortController();
    const events: StreamEvent[] = [];
    const run = (async () => {
      for await (const e of fixtureSource('streaming')('q', {
        fresh: false,
        signal: controller.signal
      })) {
        events.push(e);
      }
    })();
    controller.abort();
    await expect(run).rejects.toThrow();
    expect(events.map((e) => e.type)).not.toContain('done');
  });
});
```

- [ ] **Step 2: Run them to see them fail**

Run: `npx vitest run src/lib/fixtures`
Expected: FAIL, `fixtureSource` is not exported.

- [ ] **Step 3: Write `fixtureSource`**

In `fixtures/index.ts`, change the import block at the top to:

```ts
import {
  RateLimitedError,
  TOO_MANY_REQUESTS,
  type CardDetails,
  type Citation,
  type QueryResponse,
  type QueryResult,
  type StreamHandlers
} from '../api';
import { splitResponse, type StreamEvent } from '../answer-stream/protocol';
import { liveCitations, visibleDraft } from '../answer-stream/live';
import type { AnswerSource } from '../answer-stream/sources';
```

In `mockQuery`, replace the literal message with `TOO_MANY_REQUESTS`. Delete `headOf` and `doneOf`. Keep `wait`, `pieces` and `STREAMED` as they are. Replace `mockStream` with the following (the comment block above `wait` stays):

```ts
/** Never yields; rejects when `signal` aborts (the `slow` and `stalled` hangs). */
function never(signal: AbortSignal): Promise<never> {
  return new Promise((_, reject) =>
    signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), {
      once: true
    })
  );
}

export function fixtureSource(name: string): AnswerSource {
  return async function* (_query, { signal }): AsyncGenerator<StreamEvent> {
    if (name === 'slow') await never(signal);
    if (name === 'error') throw new Error('query failed: 502');
    if (name === 'ratelimited') throw new RateLimitedError(TOO_MANY_REQUESTS);
    const plan = STREAMED[name];
    if (!plan) {
      const { head, done } = splitResponse(
        structuredClone((FIXTURES[name] ?? FIXTURES.answered)()),
        []
      );
      await wait(400, signal);
      yield { type: 'results', head };
      yield { type: 'done', done };
      return;
    }

    const base = structuredClone({ ...BASE, cached_at: null });
    await wait(300, signal);
    yield {
      type: 'results',
      head: splitResponse({ ...base, results: uncitedResults }, base.citations).head
    };
    yield { type: 'thinking' };
    await wait(plan.thinkMs, signal);

    const full = base.answer!;
    const text = plan.stopAt === null ? full : full.slice(0, Math.floor(full.length * plan.stopAt));
    for (const piece of pieces(text)) {
      yield { type: 'delta', text: piece };
      await wait(40, signal);
    }
    if (plan.stall) await never(signal);

    if (plan.stopAt === 0) {
      yield { type: 'error', message: 'Gemini returned no answer: SAFETY' };
      yield {
        type: 'done',
        done: splitResponse({ ...retrievalOnly, answer_complete: null }, []).done
      };
      return;
    }
    if (plan.stopAt === null) {
      yield { type: 'done', done: splitResponse(base, []).done };
      return;
    }
    const answer = visibleDraft(text).trimEnd();
    const citations = liveCitations(answer, base.citations);
    const cited = new Set(citations.map((c) => c.number));
    yield {
      type: 'done',
      done: splitResponse(
        {
          ...base,
          answer,
          citations,
          rule_references: base.rule_references.filter((id) => answer.includes(id)),
          results: base.results.map((r, i) => ({ ...r, cited: cited.has(i + 1) })),
          citation_stats: { cited_count: citations.length, invalid_count: 0, uncited_answer: false },
          answer_complete: false
        },
        []
      ).done
    };
  };
}

/** The old handler interface over fixtureSource, until the page switches (Task 6). */
export async function mockStream(
  name: string,
  on: StreamHandlers,
  signal: AbortSignal = new AbortController().signal
): Promise<void> {
  for await (const e of fixtureSource(name)('', { fresh: false, signal })) {
    if (signal.aborted) return;
    if (e.type === 'results') on.results(e.head);
    else if (e.type === 'thinking') on.thinking();
    else if (e.type === 'delta') on.delta(e.text);
    else if (e.type === 'error') on.error(e.message);
    else on.done(e.done);
  }
}
```

- [ ] **Step 4: Run the tests and the type check**

Run: `npm test && npm run check`
Expected: PASS, 0 errors.

- [ ] **Step 5: Run the streaming e2e tests (they still go through `mockStream`)**

Run: `npx playwright test e2e/streaming.desktop.spec.ts`
Expected: 5 passed.

- [ ] **Step 6: Commit**

```bash
git add src/lib/fixtures
git commit -m "refactor(web): fixtures yield stream events through fixtureSource"
```

---

### Task 5: The Svelte binding

**Files:**
- Create: `mtg-web/src/lib/answer-stream/answerStream.svelte.ts`, `mtg-web/src/lib/answer-stream/answerStream.test.ts`

**Interfaces:**
- Consumes: `reduce`, `shown`, `isBusy`, `AnswerState` (Task 2); `AnswerSource`, `StreamEndedError` (Task 3); `RateLimitedError` from `api.ts`.
- Produces:
  ```ts
  function createAnswerStream(source: AnswerSource): {
    readonly state: AnswerState;
    readonly shown: QueryResponse | null;
    readonly busy: boolean;
    ask(query: string, fresh?: boolean): Promise<'ok' | 'busy' | 'rate-limited'>;
    load<T extends QueryResponse>(
      fetch: (signal: AbortSignal) => Promise<T>
    ): Promise<T | 'aborted' | Error>;
    reset(): void;
  };
  ```

Vitest runs in node, which has no `requestAnimationFrame`. The test stubs it with a queue that only runs when the test calls `paint()`, so the once-per-frame behaviour is tested deterministically.

- [ ] **Step 1: Write the failing tests**

`src/lib/answer-stream/answerStream.test.ts`:

```ts
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { RateLimitedError, type QueryResponse } from '../api';
import { createAnswerStream } from './answerStream.svelte';
import type { StreamEvent } from './protocol';
import { StreamEndedError, type AnswerSource } from './sources';
import { done, head } from './testing';

/** A source that yields `events`, then throws `end` if given, or hangs until
 * aborted when `end` is 'hang'. */
function scripted(events: StreamEvent[], end?: Error | 'hang'): AnswerSource {
  return async function* (_q, { signal }) {
    for (const e of events) yield e;
    if (end === 'hang') {
      await new Promise((_, reject) =>
        signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')))
      );
    }
    if (end) throw end;
  };
}

/** A source that plays each script in turn, one per ask. */
function sequence(...sources: AnswerSource[]): AnswerSource {
  let i = 0;
  return (q, opts) => sources[i++](q, opts);
}

/** Lets the source and the binding run until they wait on something. */
const settle = () => new Promise((resolve) => setTimeout(resolve, 5));
const results: StreamEvent = { type: 'results', head: head() };

// Animation frames run only when a test calls paint().
let frames: FrameRequestCallback[] = [];
function paint() {
  const due = frames;
  frames = [];
  for (const cb of due) cb(0);
}

beforeEach(() => {
  frames = [];
  vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => frames.push(cb));
  vi.stubGlobal('cancelAnimationFrame', () => {});
});
afterEach(() => vi.unstubAllGlobals());

describe('createAnswerStream', () => {
  it('runs a stream to its result', async () => {
    const answer = createAnswerStream(
      scripted([results, { type: 'delta', text: 'Yes' }, { type: 'done', done: done() }])
    );
    expect(await answer.ask('q')).toBe('ok');
    expect(answer.state.phase).toBe('result');
    expect(answer.shown?.answer).toBe('Yes [1].');
    expect(answer.busy).toBe(false);
  });

  it('refuses to ask while an answer streams', async () => {
    const answer = createAnswerStream(scripted([results], 'hang'));
    const first = answer.ask('q');
    await settle();
    expect(answer.state.phase).toBe('streaming');
    expect(answer.busy).toBe(true);
    expect(await answer.ask('again')).toBe('busy');
    expect(answer.state.phase).toBe('streaming');
    answer.reset();
    expect(await first).toBe('ok');
    expect(answer.state.phase).toBe('idle');
  });

  it('shows the draft once per frame while streaming', async () => {
    const answer = createAnswerStream(
      scripted([results, { type: 'delta', text: 'Yes [1].' }], 'hang')
    );
    void answer.ask('q');
    await settle();
    expect(answer.state.phase).toBe('streaming');
    expect(answer.shown?.answer).toBe('');
    paint();
    expect(answer.shown?.answer).toBe('Yes [1].');
    answer.reset();
  });

  it('a broken stream, then a 429, keeps the cut-off answer', async () => {
    const answer = createAnswerStream(
      sequence(
        scripted([results, { type: 'delta', text: 'Yes [1].' }], new StreamEndedError('gone')),
        scripted([], new RateLimitedError('busy'))
      )
    );
    await answer.ask('q');
    const cutOff = answer.shown;
    expect(cutOff?.answer_complete).toBe(false);
    expect(await answer.ask('again')).toBe('rate-limited');
    expect(answer.state.phase).toBe('result');
    expect(answer.shown).toEqual(cutOff);
  });

  it('a failure before results shows the error', async () => {
    const answer = createAnswerStream(scripted([], new Error('query failed: 502')));
    await answer.ask('q');
    expect(answer.state).toEqual({ phase: 'failed', message: 'query failed: 502' });
  });

  it('load shows a saved response, or fails with its error', async () => {
    const answer = createAnswerStream(scripted([]));
    const saved = { query: 'q', answer: 'Saved' } as QueryResponse;
    expect(await answer.load(async () => saved)).toEqual(saved);
    expect(answer.shown?.answer).toBe('Saved');
    const err = await answer.load(async () => {
      throw new Error('replay failed: 500');
    });
    expect(err).toBeInstanceOf(Error);
    expect(answer.state).toEqual({ phase: 'failed', message: 'replay failed: 500' });
  });

  it('a later load wins over an earlier one', async () => {
    const answer = createAnswerStream(scripted([]));
    let finish!: (r: QueryResponse) => void;
    const first = answer.load(() => new Promise<QueryResponse>((resolve) => (finish = resolve)));
    const second = answer.load(async () => ({ query: 'b', answer: 'B' }) as QueryResponse);
    finish({ query: 'a', answer: 'A' } as QueryResponse);
    expect(await first).toBe('aborted');
    await second;
    expect(answer.shown?.answer).toBe('B');
  });
});
```

- [ ] **Step 2: Run them to see them fail**

Run: `npx vitest run src/lib/answer-stream/answerStream.test.ts`
Expected: FAIL, cannot resolve `./answerStream.svelte`.

- [ ] **Step 3: Write `answerStream.svelte.ts`**

```ts
// The answer stream as Svelte state: runs a source through the reducer,
// aborts the previous run, and refreshes the shown draft at most once per
// frame (streaming spec decision 11).
import { RateLimitedError, type QueryResponse } from '../api';
import { isBusy, reduce, shown, type AnswerState } from './reduce';
import type { AnswerSource } from './sources';

export function createAnswerStream(source: AnswerSource) {
  let state = $state<AnswerState>({ phase: 'idle' });
  // The draft as last painted: a copy of the state's, refreshed once a frame.
  let shownDraft = $state('');
  let frame = 0;
  let controller: AbortController | null = null;

  function paintNextFrame() {
    if (frame) return;
    frame = requestAnimationFrame(() => {
      frame = 0;
      shownDraft = state.phase === 'streaming' ? state.draft : '';
    });
  }

  /** Ends whatever is running and returns the new run's controller. */
  function restart(): AbortController {
    controller?.abort();
    cancelAnimationFrame(frame);
    frame = 0;
    shownDraft = '';
    return (controller = new AbortController());
  }

  async function ask(query: string, fresh = false): Promise<'ok' | 'busy' | 'rate-limited'> {
    if (isBusy(state)) return 'busy';
    const before = state;
    const ctrl = restart();
    state = { phase: 'loading' };
    try {
      for await (const event of source(query, { fresh, signal: ctrl.signal })) {
        if (ctrl.signal.aborted) return 'ok';
        state = reduce(state, event, query);
        if (event.type === 'delta') paintNextFrame();
      }
      if (isBusy(state)) {
        // A source that ended quietly without `done`.
        state = reduce(state, { type: 'broken', message: 'The answer stopped arriving.' }, query);
      }
    } catch (e) {
      if (ctrl.signal.aborted) return 'ok';
      if (e instanceof RateLimitedError) {
        state = before;
        return 'rate-limited';
      }
      const message = e instanceof Error ? e.message : String(e);
      state = reduce(state, { type: 'broken', message }, query);
    } finally {
      if (controller === ctrl) {
        cancelAnimationFrame(frame);
        frame = 0;
      }
    }
    return 'ok';
  }

  async function load<T extends QueryResponse>(
    fetch: (signal: AbortSignal) => Promise<T>
  ): Promise<T | 'aborted' | Error> {
    const ctrl = restart();
    state = { phase: 'loading' };
    try {
      const response = await fetch(ctrl.signal);
      if (ctrl.signal.aborted) return 'aborted';
      state = { phase: 'result', response };
      return response;
    } catch (e) {
      if (ctrl.signal.aborted) return 'aborted';
      const error = e instanceof Error ? e : new Error(String(e));
      state = { phase: 'failed', message: error.message };
      return error;
    }
  }

  function reset() {
    restart();
    controller = null;
    state = { phase: 'idle' };
  }

  return {
    get state() {
      return state;
    },
    get shown() {
      return shown(state, shownDraft);
    },
    get busy() {
      return isBusy(state);
    },
    ask,
    load,
    reset
  };
}
```

- [ ] **Step 4: Run the tests**

Run: `npx vitest run src/lib/answer-stream && npm run check`
Expected: PASS, 0 errors.

If `answerStream.test.ts` fails because `$state` is not defined, the Svelte plugin isn't compiling `.svelte.ts` under vitest. `vite.config.ts` already loads `sveltekit()`, which does compile it. Check the import path ends in `answerStream.svelte` (no `.ts`), the same way `admin.svelte` is imported.

- [ ] **Step 5: Commit**

```bash
git add src/lib/answer-stream/answerStream.svelte.ts src/lib/answer-stream/answerStream.test.ts
git commit -m "feat(web): createAnswerStream, the Svelte binding for the answer stream"
```

---

### Task 6: Switch the page, and delete the old path

**Files:**
- Modify: `mtg-web/src/routes/+page.svelte` (script and markup)
- Modify: `mtg-web/src/lib/api.ts` (delete `StreamHandlers`, `StreamEndedError`, `streamQuery`, the re-export)
- Modify: `mtg-web/src/lib/fixtures/index.ts` (delete `mockStream`, the `StreamHandlers` import; update the header comment)
- Delete: `mtg-web/src/lib/api.test.ts`

**Interfaces:**
- Consumes: `createAnswerStream` (Task 5), `httpSource`, `AnswerSource` (Task 3), `fixtureSource` (Task 4), `TOO_MANY_REQUESTS` (Task 1).

- [ ] **Step 1: Replace the page's script state and stream code**

In `+page.svelte`:

1. Replace the `$lib/api` import with:
   ```ts
   import {
     AdminRequiredError,
     fetchReplay,
     NotFoundError,
     TOO_MANY_REQUESTS,
     type ReplayResponse
   } from '$lib/api';
   import { createAnswerStream } from '$lib/answer-stream/answerStream.svelte';
   import { httpSource, type AnswerSource } from '$lib/answer-stream/sources';
   ```
   and delete the `$lib/answer-stream/live` import.
2. Delete `type View`, and the `view`, `response`, `failure`, `sources`, `draft`, `shownDraft`, `frame`, `controller` and `replayLoads` declarations, along with their comments.
3. Replace the block from `const live = $derived(view === 'streaming');` through the end of the `shown` `$derived.by` with:
   ```ts
   // Dev only: ?mock=<fixture> answers from src/lib/fixtures (see the spec),
   // imported on first use so the fixtures stay out of the production bundle.
   const mock = import.meta.env.DEV ? page.url.searchParams.get('mock') : null;
   const source: AnswerSource = mock
     ? async function* (q, opts) {
         yield* (await import('$lib/fixtures')).fixtureSource(mock)(q, opts);
       }
     : httpSource;
   const answer = createAnswerStream(source);

   const phase = $derived(answer.state.phase);
   const live = $derived(phase === 'streaming');
   // What the page shows: the response, or while streaming, the draft with
   // live citations in place of the validated ones.
   const shown = $derived(answer.shown);
   const failure = $derived.by(() => {
     const s = answer.state;
     return s.phase === 'failed' ? s.message : '';
   });
   ```
   Then delete the old `const mock = …` line further down, and `showDraftNextFrame`, `stopDraftFrames` and `run`.
4. Replace `reset`, `loadReplay` and `ask` with:
   ```ts
   function reset() {
     answer.reset();
     replay = null;
     replayFailed = null;
     selection = null;
     query = '';
   }

   async function loadReplay(id: string) {
     rateLimited = '';
     selection = null;
     replayFailed = null;
     const r = await answer.load(() => fetchReplay(id));
     if (r === 'aborted') return;
     if (r instanceof Error) {
       replay = null;
       replayFailed = {
         id,
         kind:
           r instanceof AdminRequiredError
             ? 'admin'
             : r instanceof NotFoundError
               ? 'missing'
               : 'other'
       };
       return;
     }
     replay = r;
     query = r.query;
   }
   ```
   ```ts
   async function ask(fresh = false) {
     // A fresh request must regenerate the question whose cached answer is
     // on screen, not whatever is currently sitting in the input box.
     const q = fresh && shown ? shown.query : query.trim();
     // While an answer loads or streams the server holds this visitor's one
     // generation slot, so asking waits (the answer stream's busy rule).
     if (!q || answer.busy) return;
     if (replayParam) {
       // Asking for real ends the replay.
       replay = null;
       replayFailed = null;
       goto('/', { keepFocus: true, noScroll: true });
     }
     rateLimited = '';
     selection = null;
     if ((await answer.ask(q, fresh)) === 'rate-limited') rateLimited = TOO_MANY_REQUESTS;
   }
   ```

- [ ] **Step 2: Update the markup**

- `loading={view === 'loading'}` → `loading={phase === 'loading'}`
- `retrievalOnly={!!response?.degraded}` → `retrievalOnly={!!shown?.degraded}`
- `center={view === 'idle' ? undefined : headerForm}` → `center={phase === 'idle' ? undefined : headerForm}`
- `{#if view === 'idle'}` → `{#if phase === 'idle'}`
- `{:else if view === 'loading'}` → `{:else if phase === 'loading'}`
- `{:else if view === 'failed'}` → `{:else if phase === 'failed'}`

`answering={live}`, `shown`, `notice`, `citedItems` and everything else stay as they are.

- [ ] **Step 3: Delete the old stream path**

- `api.ts`: delete the re-export lines added in Task 1, `StreamHandlers`, `StreamEndedError` and `streamQuery`.
- `fixtures/index.ts`: delete `mockStream`, remove `type StreamHandlers` from the `../api` import, and replace the header comment's first two lines with:
  ```ts
  // Dev-only stand-ins for the API, so every state of the search page can be
  // checked without spending quota: /?mock=<name> in `npm run dev`.
  ```
  In the comment above `wait`, change "Streamed versions of the fixtures, for the stream the page really reads." to "fixtureSource: the fixtures as an AnswerSource, the stream the page really reads."
- `git rm src/lib/api.test.ts`. Its cases live in `answer-stream/sources.test.ts`.

Then confirm nothing still refers to the old names:

Run: `git grep -n -E "streamQuery|StreamHandlers|mockStream|lib/stream'|from './stream'" -- src e2e`
Expected: no output.

- [ ] **Step 4: Run every check**

Run: `npm run check && npm test && npx playwright test`
Expected: 0 errors; all unit tests pass; every Playwright test passes, **with no test file changed**.

- [ ] **Step 5: Commit**

```bash
git add -A src
git commit -m "refactor(web): the search page runs on the answer stream"
```

---

### Task 7: Golden transcripts across the seam

**Files:**
- Modify: `.gitattributes`
- Create: `mtg-api/tests/test_stream_golden.py`
- Create (generated): `mtg-web/src/lib/answer-stream/golden/answered.sse`, `cached.sse`, `failed.sse`
- Create: `mtg-web/src/lib/answer-stream/golden.test.ts`

**Interfaces:**
- Consumes: conftest's `setup_trample`, `ChunksAnswerer`, `CountingAnswerer`; `mtg_api.llm.StreamChunk`. Web: `httpSource`, `reduce`, `HEAD_KEYS`, `DONE_KEYS`, `noticeFor`.

- [ ] **Step 1: Keep `.sse` files LF on every checkout**

Append to `.gitattributes`:

```
# Golden answer-stream transcripts: compared byte for byte, parsed on "\n\n".
*.sse text eol=lf
```

- [ ] **Step 2: Write the backend test**

`mtg-api/tests/test_stream_golden.py`:

```python
"""Golden answer-stream transcripts, the contract the web client is tested
against (mtg-web/src/lib/answer-stream/golden.test.ts). A difference fails
here, on the side that changed. After a deliberate protocol change, rewrite
them with UPDATE_GOLDEN=1 pytest tests/test_stream_golden.py and update the
web client in the same PR."""

import os
import re
from pathlib import Path

import pytest
from conftest import ChunksAnswerer, CountingAnswerer, setup_trample
from fastapi.testclient import TestClient

from mtg_api.llm import StreamChunk
from mtg_api.main import app, get_answerer

GOLDEN = Path(__file__).resolve().parents[2] / "mtg-web/src/lib/answer-stream/golden"
CACHED_AT = re.compile(r'"cached_at": "[^"]*"')
FIXED_CACHED_AT = '"cached_at": "2026-01-01T00:00:00Z"'


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def _post() -> str:
    # Transcripts must never spend: only the conftest fakes may answer.
    answerer = app.dependency_overrides[get_answerer]()
    assert isinstance(answerer, (ChunksAnswerer, CountingAnswerer)), answerer
    resp = TestClient(app).post("/api/v1/query/stream", json={"query": "trample"})
    assert resp.status_code == 200
    return resp.text


def _answered() -> str:
    chunks = [StreamChunk(text="Yes "), StreamChunk(text="[1].", finish_reason="STOP")]
    setup_trample(answerer=ChunksAnswerer(chunks))
    return _post()


def _cached() -> str:
    setup_trample()
    _post()
    return _post()


def _failed() -> str:
    setup_trample(answerer=ChunksAnswerer([], RuntimeError("Gemini 500")))
    return _post()


@pytest.mark.parametrize(
    ("name", "transcript"), [("answered", _answered), ("cached", _cached), ("failed", _failed)]
)
def test_golden_transcript(name, transcript):
    text = CACHED_AT.sub(FIXED_CACHED_AT, transcript())
    path = GOLDEN / f"{name}.sse"
    if os.environ.get("UPDATE_GOLDEN"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode())
    assert path.exists(), f"{path} is missing: run with UPDATE_GOLDEN=1"
    assert path.read_bytes().decode() == text, (
        f"{path.name} drifted from the server: rerun with UPDATE_GOLDEN=1 "
        "and update the web client to match"
    )
```

- [ ] **Step 3: Run it to see it fail, then generate the transcripts**

Run (in `mtg-api/`): `pytest tests/test_stream_golden.py -v`
Expected: 3 FAIL, "is missing: run with UPDATE_GOLDEN=1".

Run: `UPDATE_GOLDEN=1 pytest tests/test_stream_golden.py -v` (PowerShell: `$env:UPDATE_GOLDEN=1; pytest tests/test_stream_golden.py -v; Remove-Item Env:UPDATE_GOLDEN`)
Expected: 3 passed.

Run: `pytest tests/test_stream_golden.py -v`
Expected: 3 passed. Then read the three files. `answered.sse` has `results`, `thinking`, two `delta`s and `done`. `cached.sse` has `results` (with `"cached_at": "2026-01-01T00:00:00Z"`) and `done`. `failed.sse` has `results`, `thinking`, `error` (`"Gemini 500"`) and `done`. If any of them differs between two runs, find the nondeterministic field and normalise it like `cached_at`.

- [ ] **Step 4: Write the web test**

`mtg-web/src/lib/answer-stream/golden.test.ts`:

```ts
// The golden transcripts are written by mtg-api/tests/test_stream_golden.py
// from the real pipeline. This side checks the client still reads them.
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { QueryResponse } from '../api';
import { noticeFor } from '../status';
import answered from './golden/answered.sse?raw';
import cached from './golden/cached.sse?raw';
import failed from './golden/failed.sse?raw';
import { DONE_KEYS, HEAD_KEYS, type StreamEvent } from './protocol';
import { reduce, type AnswerState } from './reduce';
import { httpSource } from './sources';

async function play(transcript: string) {
  vi.stubGlobal('fetch', async () => new Response(transcript, { status: 200 }));
  const events: StreamEvent[] = [];
  let state: AnswerState = { phase: 'loading' };
  for await (const e of httpSource('trample', {
    fresh: false,
    signal: new AbortController().signal
  })) {
    events.push(e);
    state = reduce(state, e, 'trample');
  }
  if (state.phase !== 'result') throw new Error(`ended in ${state.phase}`);
  return { events, response: state.response as QueryResponse };
}

const HEAD_ONLY = ['degraded', 'answers_remaining', 'cached_at'];

afterEach(() => vi.unstubAllGlobals());

describe.each([
  ['answered', answered, ['results', 'thinking', 'delta', 'delta', 'done']],
  ['cached', cached, ['results', 'done']],
  ['failed', failed, ['results', 'thinking', 'error', 'done']]
])('golden %s', (_name, transcript, order) => {
  it('sends the events in the order the client expects', async () => {
    const { events } = await play(transcript);
    expect(events.map((e) => e.type)).toEqual(order);
  });

  it('splits the response between head and done as the client expects', async () => {
    const { events } = await play(transcript);
    const first = events[0];
    const last = events.at(-1)!;
    if (first.type !== 'results' || last.type !== 'done') throw new Error('bad ends');
    expect(Object.keys(first.head).sort()).toEqual([...HEAD_KEYS].sort());
    expect(Object.keys(last.done)).toEqual(expect.arrayContaining([...DONE_KEYS]));
    for (const key of HEAD_ONLY) expect(last.done).not.toHaveProperty(key);
  });
});

describe('golden results', () => {
  it('answered: the validated answer, with its citation', async () => {
    const { response } = await play(answered);
    expect(response.query).toBe('trample');
    expect(response.answer).toBe('Yes [1].');
    expect(response.answer_complete).toBe(true);
    expect(response.citations.map((c) => c.number)).toEqual([1]);
    expect(response.results[0].cited).toBe(true);
    expect(response.cached_at).toBeNull();
  });

  it('cached: cached_at comes through from the head', async () => {
    const { response } = await play(cached);
    expect(response.cached_at).toBe('2026-01-01T00:00:00Z');
    expect(response.answer).toBe('Yes [1].');
  });

  it('failed: no answer, and the no-answer notice', async () => {
    const { response } = await play(failed);
    expect(response.answer).toBeNull();
    expect(noticeFor(response)).toBe('no_answer');
  });
});
```

- [ ] **Step 5: Run the web test**

Run (in `mtg-web/`): `npx vitest run src/lib/answer-stream/golden.test.ts && npm run check`
Expected: PASS, 0 errors. If `svelte-check` reports no type for `*.sse?raw`, add `declare module '*.sse?raw' { const text: string; export default text; }` to `src/app.d.ts`. Vite's client types normally cover `?raw` already.

- [ ] **Step 6: Lint the backend and run its whole suite**

Run (in `mtg-api/`): `ruff check . && ruff format --check . && pytest`
Expected: clean; all pass.

- [ ] **Step 7: Commit**

```bash
git add .gitattributes mtg-api/tests/test_stream_golden.py mtg-web/src/lib/answer-stream/golden mtg-web/src/lib/answer-stream/golden.test.ts
git commit -m "test: golden answer-stream transcripts across the API/web seam"
```

---

### Task 8: Final verification and docs

**Files:**
- Modify: `docs/architecture/2026-09-30-streaming-review/README.md` (row 3 → done)
- Modify: `docs/superpowers/specs/2026-10-05-web-answer-stream-design.md` (status line)

- [ ] **Step 1: Run everything**

Run (in `mtg-web/`): `npm run check && npm test && npx playwright test && npm run build`
Run (in `mtg-api/`): `ruff check . && ruff format --check . && pytest`
Expected: all green. `npm run build` must still keep the fixtures in their own chunk:

Run: `grep -l "Basilisk Collar" build/_app/immutable/nodes/*.js build/_app/immutable/entry/*.js`
Expected: no output (the fixture data is only in a lazily loaded chunk).

- [ ] **Step 2: Confirm the line count moved out of the page**

Run: `wc -l mtg-web/src/routes/+page.svelte`
Expected: roughly 280 lines (from 403).

- [ ] **Step 3: Manual check against the real backend: ask the user first**

This one costs real Gemini answers (two or three), so **don't run it without the user's go-ahead**. With `docker compose up` (or the API on :8000) and `npm run dev`:
1. Ask a question and watch it stream: evidence first, then "Thinking…", then the text, and finally the cited sources.
2. While it streams, confirm the button reads "Answering…" and Enter does nothing.
3. Ask the same question again: it's a cache hit, with no typing.

- [ ] **Step 4: Mark the work done in the docs**

In the review README, change row 3's strength cell to `Strong · **done** (YYYY-MM-DD)` with the merge date. In the spec, change `Status:` to `implemented`.

- [ ] **Step 5: Commit**

```bash
git add docs
git commit -m "docs: web answer stream done (review doc 03)"
```
