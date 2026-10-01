# 3 · Deepen the answer stream on the web side

> Architecture review, 2026-09-30, branch `feature/streaming-answers`.
> Strength: **Strong** · Dependency category: **ports & adapters** (fetch adapter + fixture adapter)
> Companion docs: [02 query pipeline](02-query-pipeline.md) (the server end of the same protocol), [04 citations](04-citations.md) (the live-citation helpers this module calls).

## Summary

The search page (`mtg-web/src/routes/+page.svelte`, 400 lines) contains the whole client side of the answer-stream protocol:

- the five event names, through the `StreamHandlers` it passes;
- the rule that `done` always follows `error`;
- how to build a partial `QueryResponse` from `results`;
- how to recover when the stream breaks after `results`;
- the draft buffer, its rAF throttle, abort handling, and what to show after a 429.

None of this is tested except through Playwright. Most of it can't be unit tested, because it lives inside a Svelte component's closures.

This doc proposes an **answer stream** module: a pure reducer from protocol events to an `AnswerView`, plus a thin Svelte binding. Event sources become **adapters** behind one port, an async iterable of events. The real fetch client is one adapter and the dev fixtures are the other. Two adapters make it a real **seam**.

The strongest reason is a bug this shape currently hides, described next.

## A bug the current shape hides

**Asking a second question while the first is still streaming shows "Couldn't write an answer this time" for the first question, plus a rate-limit message.** In production this is close to certain.

1. The visitor asks Q1. The server takes the visitor's one generation slot (`max_concurrent_generations_per_ip = 1`, `config.py:73`) and starts writing.
2. While Q1 streams, the visitor asks Q2. `ask()` aborts Q1's fetch (`+page.svelte:211`). Per decision 7 of the streaming spec, the server keeps writing Q1 on its worker thread and **keeps the slot** until Q1 finishes.
3. Q2 hits `generation_slots.acquire` → `None` → **HTTP 429**.
4. The page handles the 429:

   ```ts
   const previous: View = response ? 'result' : 'idle';   // L215: response is Q1's partial head
   ...
   if (e instanceof RateLimitedError) {
     rateLimited = e.message;
     view = previous;                                      // L225: 'result'
   }
   ```

5. `response` is Q1's head with `answer: null`. The draft lived in `draft`/`shownDraft`, not in `response`. With `view === 'result'`, `shown` is that raw response, and `noticeFor(shown)` returns `'no_answer'`. The page shows the **"Couldn't write an answer"** notice for Q1, while Q1's answer is being written successfully on the server and will appear in history.

The Playwright test `asking again mid-stream replaces the answer instead of mixing them` passes because the fixtures don't model slots, so no 429 happens. Q2 only succeeds if it's a cache hit or retrieval-only.

This is not a careless mistake. The page has to keep three pieces of state consistent (`view`, `response`, `draft`) across five handlers, a catch block and a replay path, and the 429 path forgot one of them. A reducer whose states are values (`streaming {head, draft}`) can't lose the draft by switching `view`. The case becomes a single row in a table test.

(The fix for the 429 itself, whether to keep showing Q1's draft or to block asking again while streaming, is a product decision. See the open questions. This module makes either choice a few lines in one place.)

## Why do this

**Right away**

- It fixes the bug above, with a test that would have caught it.
- The page drops by about 120 lines of protocol and state handling and keeps layout and interaction, which is what a route component is for.
- The fixtures stop calling handlers and yield events instead. `headOf`/`doneOf` move to one protocol helper.

**Long term**

- **Locality.** Any protocol change lands in one module plus its table test: a new event (e.g. `usage`, or `retrying` for Gemini 503 retries), a new terminal state, or reconnecting a broken stream.
- **Leverage.** Other pages that want a streamed answer get it for free: history replay animating an answer, or an admin "regenerate" button.
- **Testability.** Every state is a value, so vitest table tests cover sequences that Playwright can only cover one fixture at a time.

## Current state

### Who calls what

```
ask(fresh)                                       +page.svelte:198
├─ abort previous controller; stopDraftFrames()
├─ previous = response ? 'result' : 'idle'       (snapshot for 429)
├─ view = 'loading'
└─ run(q, fresh, signal, progress)               +page.svelte:99
   ├─ builds StreamHandlers closing over page state:
   │    results → progress.results = true; response = {…head, answer:null, citations:[],
   │              rule_references:[], citation_stats:{0,0,false}, answer_complete:null};
   │              sources = s; draft = ''; view = 'streaming'
   │    thinking → (nothing)
   │    delta    → draft += text; showDraftNextFrame()
   │    error    → (nothing; relies on done following)
   │    done     → stopDraftFrames(); response = {…response, …d}; view = 'result'
   └─ mock ? fixtures.mockStream(mock, on, signal)
           : streamQuery(q, {fresh, signal}, on)  api.ts:132
                └─ SseParser.push → if/else on e.event → on.*()

catch in ask():
   aborted        → return
   RateLimited    → view = previous                ← the bug
   progress.results && response → rebuild response from draft (cut-off)  L226–238
   else           → view = 'failed'

$derived shown                                   +page.svelte:68
   live ? {…response, answer: visibleDraft(shownDraft),
           citations: liveCitations(shownDraft, sources),
           results: liveResults(response.results, citations)}
        : response
```

### Protocol rules the page holds

| Rule | Where it lives |
|---|---|
| `results` is first and carries `sources` | `results` handler destructures it |
| A partial response needs default `citation_stats` | L114, hard-coded |
| `error` is always followed by `done` | comment at L127; the handler does nothing |
| `done` overrides head fields except `degraded`, `answers_remaining`, `cached_at` | `{...response, ...d}`; correct only because the server leaves those out of `done` |
| A stream that ends without `done` after `results` is a cut-off answer | `progress.results` flag plus the catch block |
| A stream that ends before `results` is a failure | catch block |
| A delta arriving before `results` is impossible | not enforced: the delta is added to `draft`, then **wiped** by the `results` handler |

### The head/done split, written three times

```python
# mtg-api main.py:330
_HEAD_ONLY = ("degraded", "answers_remaining", "cached_at")
```
```ts
// mtg-web api.ts:116
export type StreamDone = Omit<QueryResponse, 'query' | 'degraded' | 'answers_remaining' | 'cached_at'>;
```
```ts
// mtg-web fixtures/index.ts:298
function headOf(r, sources): StreamHead { … }   function doneOf(r): StreamDone { … }
```

### What's tested

- `sse.test.ts`: frame parsing. Good, and it stays.
- `api.test.ts`: `streamQuery` dispatches to handlers and maps 429. Good, and it becomes an adapter test.
- `stream.test.ts`: `visibleDraft`, `liveCitations`, `liveResults` as pure functions. This is the "extracted for testability, but the bugs hide in how they're called" pattern: the 429 bug and the delta-before-results case are both in the calling code.
- `fixtures.test.ts`: the event order of each fixture.
- Playwright (`e2e/streaming.desktop.spec.ts`): 5 scenarios against fixtures.
- **Not tested:** the page's state transitions.

### The deletion test

Delete `stream.ts` (35 lines) and its three helpers move into the page. Nothing gets harder: they are shallow. Delete the page's `run`/`ask`/`shown` logic, and there is nowhere for it to go. The missing piece is the module that owns the transitions.

## Proposed design

### Files

```
src/lib/answer-stream/
  protocol.ts        StreamHead, StreamDone, StreamEvent union, splitResponse()
  reduce.ts          pure: initial, reduce(state, event), view(state, query)
  reduce.test.ts     table tests
  sources.ts         AnswerSource port; httpSource (from streamQuery)
  answerStream.svelte.ts   createAnswerStream(source): runes binding, abort, rAF
src/lib/fixtures/index.ts   fixtureSource(name): AnswerSource (yields events)
```

`stream.ts` merges into `reduce.ts`, or into doc 04's `draftCitations` module.

### The port

```ts
// protocol.ts
export type StreamEvent =
  | { type: 'results'; head: StreamHead }
  | { type: 'thinking' }
  | { type: 'delta'; text: string }
  | { type: 'error'; message: string }
  | { type: 'done'; done: StreamDone };

// sources.ts
/** Yields the protocol's events in order. Throws RateLimitedError / Error for
 * a refusal before any event; throws StreamEndedError if the transport ends
 * without `done`. Stops quietly when `signal` aborts. */
export type AnswerSource = (
  query: string,
  opts: { fresh: boolean; signal: AbortSignal }
) => AsyncIterable<StreamEvent>;

export const httpSource: AnswerSource = async function* (query, { fresh, signal }) {
  const resp = await post('/api/v1/query/stream', query, fresh, signal);   // 429 → RateLimitedError
  // An explicit reader, not `for await` over the stream: Safari has no
  // ReadableStream async iterator.
  const reader = resp.body!.pipeThrough(new TextDecoderStream()).getReader();
  const parser = new SseParser();
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      for (const e of parser.push(value)) {
        const event = decode(e);        // name + JSON → StreamEvent; unknown names ignored
        yield event;
        if (event.type === 'done') return;
      }
    }
  } finally {
    reader.cancel().catch(() => {});    // closes the connection on abort or early return
  }
  throw new StreamEndedError('The answer stopped arriving before it finished.');
};
```

The fixture adapter becomes the same kind of generator:

```ts
export function fixtureSource(name: string): AnswerSource {
  return async function* (_query, { signal }) {
    ...
    const { head, done } = splitResponse(base, base.citations);
    await wait(300, signal);
    yield { type: 'results', head: { ...head, results: uncitedResults } };
    yield { type: 'thinking' };
    for (const piece of pieces(text)) { yield { type: 'delta', text: piece }; await wait(40, signal); }
    yield { type: 'done', done };
  };
}
```

### The reducer: the deep part

```ts
// reduce.ts
export type AnswerState =
  | { phase: 'idle' }
  | { phase: 'loading' }
  | { phase: 'streaming'; head: StreamHead; draft: string; thinking: boolean }
  | { phase: 'result'; response: QueryResponse }
  | { phase: 'failed'; message: string };

export function reduce(state: AnswerState, event: StreamEvent | Broken, query: string): AnswerState {
  switch (event.type) {
    case 'results':
      if (state.phase !== 'loading') return protocolError(state, 'results twice');
      return { phase: 'streaming', head: event.head, draft: '', thinking: false };
    case 'thinking':
      return state.phase === 'streaming' ? { ...state, thinking: true } : protocolError(state, …);
    case 'delta':
      if (state.phase !== 'streaming') return protocolError(state, 'delta before results');
      return { ...state, draft: state.draft + event.text, thinking: false };
    case 'error':
      return state;                                   // `done` follows; nothing to show yet
    case 'done':
      if (state.phase !== 'streaming') return protocolError(state, 'done before results');
      return { phase: 'result', response: merge(query, state.head, event.done) };
    case 'broken':                                    // transport ended without done
      return state.phase === 'streaming'
        ? { phase: 'result', response: cutOff(query, state) }   // today's L226–238
        : { phase: 'failed', message: event.message };
  }
}

/** What the page renders: in `streaming`, the draft with live citations. */
export function shown(state: AnswerState, query: string): QueryResponse | null { … }
```

`merge`, `cutOff`, the default `citation_stats` and the live view are written once each, here. `protocolError` turns an impossible sequence into the same `failed` or cut-off handling as a broken stream, and logs it. The page never sees an inconsistent state.

### The Svelte binding

```ts
// answerStream.svelte.ts
export function createAnswerStream(source: AnswerSource) {
  let state = $state<AnswerState>({ phase: 'idle' });
  let shownDraft = $state('');           // rAF-throttled copy, as today
  let controller: AbortController | null = null;

  async function ask(query: string, fresh = false): Promise<'ok' | 'rate-limited'> {
    controller?.abort();
    const ctrl = (controller = new AbortController());
    const before = state;
    state = { phase: 'loading' };
    try {
      for await (const e of source(query, { fresh, signal: ctrl.signal })) {
        state = reduce(state, e, query);
        scheduleFrame();
      }
    } catch (e) {
      if (ctrl.signal.aborted) return 'ok';
      if (e instanceof RateLimitedError) { state = settleInterrupted(before); return 'rate-limited'; }
      state = reduce(state, { type: 'broken', message: messageOf(e) }, query);
    }
    return 'ok';
  }

  return {
    get state() { return state; },
    get shown() { return shown(state, …) },   // uses shownDraft while streaming
    ask, show: (r: QueryResponse) => (state = { phase: 'result', response: r }),
    reset: () => { controller?.abort(); state = { phase: 'idle' }; }
  };
}
```

`settleInterrupted(before)` is where the 429 decision lives. If the previous state was `streaming`, it becomes a cut-off `result` built from its draft, rather than a `result` with `answer: null`. It is one pure function, so it can have a table test.

### The page afterwards

```ts
const answer = createAnswerStream(
  import.meta.env.DEV && mock ? fixtureSource(mock) : httpSource
);
const live = $derived(answer.state.phase === 'streaming');
const shown = $derived(answer.shown);

async function ask(fresh = false) {
  const q = fresh && shown ? shown.query : query.trim();
  if (!q) return;
  endReplay();
  rateLimited = '';
  selection = null;
  if ((await answer.ask(q, fresh)) === 'rate-limited') rateLimited = RATE_LIMITED_MESSAGE;
}
```

The replay loader calls `answer.show(r)`. The `{#if}` chain in the markup switches on `answer.state.phase` instead of `view`.

### Fixture loading

`fixtures/index.ts` is 392 lines of data, and it is imported dynamically today (`await import('$lib/fixtures')`) so it stays out of the production bundle. Keep that. `fixtureSource` is resolved lazily by a small `lazyFixtureSource(name)` adapter that imports the module on its first call.

## Implementation plan

1. **Protocol module.** Create `answer-stream/protocol.ts` with the `StreamEvent` union, `StreamHead` and `StreamDone` (moved from `api.ts`; re-export from `api.ts` for now), and `splitResponse(r, sources) → {head, done}`, which replaces `headOf`/`doneOf`.
2. **Reducer, tests first.** Write `reduce.test.ts` from the case table below, then implement `reduce`, `shown`, `merge`, `cutOff` and `settleInterrupted`. Move `visibleDraft`/`liveCitations`/`liveResults` in, or call doc 04's module if that has landed.
3. **Turn `streamQuery` into `httpSource`.** Change it from a handlers callback to an async generator. Adjust `api.test.ts` to collect yielded events instead of recording handler calls; the assertions stay the same. Delete `StreamHandlers`.
4. **Turn the fixtures into `fixtureSource`.** Port `mockStream` to a generator over `splitResponse`. Port `fixtures.test.ts`'s `play()` to collect events; the expectations stay the same. `mockQuery` is only used by tests (`grep` shows no page imports). Keep it only if the tests need it, otherwise inline it into those tests.
5. **Svelte binding.** `createAnswerStream` in `answerStream.svelte.ts`, with abort and the rAF throttle moved from the page.
6. **Switch the page.** Replace `run`, the stream half of `ask`, `shown`, `sources`, `draft`, `shownDraft`, `frame`, `controller` and `stopDraftFrames`. Keep replay, selection and the sheet in the page.
7. **Decide and implement the 429 behaviour** (open question 1) in `settleInterrupted`, with its table rows.
8. **Playwright.** Add a `busy` fixture: the source yields `results` and a few deltas, and the next call throws `RateLimitedError`. Add an e2e test for asking again mid-stream under the per-IP cap. Existing e2e tests should pass unchanged.
9. **Verify.** `npm run check`, `npm test`, `npx playwright test`, then a manual run against the real backend: ask, ask again mid-stream, and confirm the chosen 429 behaviour.

Rough size: +260 lines (reducer ~120, binding ~60, tests ~150), −130 lines from the page, −40 from the fixtures.

## Test plan

**`reduce.test.ts`**, a table of event sequences and expected final states and `shown` values:

| Sequence | Expect |
|---|---|
| results, thinking, delta×3, done | `result`; answer from `done`, not the draft |
| results, done (cache hit) | `result`; `cached_at` from the head survives the merge |
| results, thinking, error, done(answer null) | `result`; `noticeFor` → `no_answer` |
| results, delta "Yes [1", broken | `result`; answer "Yes", `answer_complete: false`, citation 1 live |
| results, broken (no text) | `result`; answer null, `answer_complete: null` |
| broken before results | `failed` |
| delta before results | `failed` (protocol error, logged), never a wiped draft |
| done before results | `failed` |
| results, results | protocol error |
| streaming(draft "Yes [1]") then 429 on the next ask | Q1 kept as a cut-off `result`, not `no_answer` (pins the bug) |
| `shown` while streaming with "A [1]. B [2" | answer "A [1]. B ", citations [1], results[0].cited |

**Adapter tests:** `api.test.ts` ports to `httpSource`: split frames, 429, ended without `done`, unknown event ignored. `fixtures.test.ts` ports to `fixtureSource`.

**Contract test across the seam (optional, see open question 3):** a golden SSE transcript written by a backend test (`mtg-api/tests/test_query_stream.py`) to `mtg-web/src/lib/answer-stream/golden.sse`, then parsed by `httpSource` and reduced in a web test. If the server's head/done split drifts, one side fails.

**Delete:** the handler-recording helpers in `api.test.ts` and `fixtures.test.ts`, and `stream.test.ts`, whose cases move into `reduce.test.ts`. Replace, don't layer.

## Risks

- **Svelte 5 runes outside components.** `createAnswerStream` uses `$state` in a `.svelte.ts` module. That is the supported pattern; `meta.svelte.ts` and `admin.svelte.ts` already do it.
- **rAF throttle behaviour.** The throttle must still cap re-layouts at one per frame (decision 11). Keep the same scheduling, and have Playwright check that the draft appears progressively.
- **Async-iterator abort.** Breaking out of `for await` over the *generator* calls its `return()`, which runs the `finally` that cancels the reader, so the connection closes promptly. Never iterate the `ReadableStream` itself with `for await`, because Safari lacks it.

## Open questions (for grilling)

1. **The 429-mid-stream behaviour.** Options:
   - (a) Keep Q1's draft on screen as a cut-off answer and show the rate-limit message.
   - (b) Disable "ask" while an answer streams, showing the remaining time instead.
   - (c) Don't abort Q1's relay until Q2 has received its `results`.
   - (d) On the server, let a new question from the same IP take over the slot of that IP's abandoned generation.

   (a) is the smallest change. (c) or (d) are better UX. (d) touches doc 01 and contradicts decision 7, so it would need that decision revisited.
2. Should `shown` live in the reducer (pure, testable) or stay a page `$derived`? This doc recommends the reducer.
3. Is a golden-transcript contract test worth it, or is a shared TypeScript type generated from the pydantic `StreamHead`/`StreamDone` (doc 02) a better guard against the head/done split drifting?
