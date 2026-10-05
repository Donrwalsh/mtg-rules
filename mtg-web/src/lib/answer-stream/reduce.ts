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
