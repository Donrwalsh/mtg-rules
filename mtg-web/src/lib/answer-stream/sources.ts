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
