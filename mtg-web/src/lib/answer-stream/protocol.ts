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
