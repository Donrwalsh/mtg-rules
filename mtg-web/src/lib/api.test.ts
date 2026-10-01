import { afterEach, describe, expect, it, vi } from 'vitest';
import { RateLimitedError, StreamEndedError, streamQuery, type StreamHandlers } from './api';

function sseResponse(chunks: string[], status = 200): Response {
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      const enc = new TextEncoder();
      for (const c of chunks) controller.enqueue(enc.encode(c));
      controller.close();
    }
  });
  return new Response(body, { status, headers: { 'content-type': 'text/event-stream' } });
}

function recorder() {
  const calls: [string, unknown][] = [];
  const on: StreamHandlers = {
    results: (h) => calls.push(['results', h]),
    thinking: () => calls.push(['thinking', null]),
    delta: (t) => calls.push(['delta', t]),
    error: (m) => calls.push(['error', m]),
    done: (d) => calls.push(['done', d])
  };
  return { calls, on };
}

afterEach(() => vi.unstubAllGlobals());

describe('streamQuery', () => {
  it('dispatches each event, across chunk boundaries', async () => {
    const fetch = vi.fn(async () =>
      sseResponse([
        'event: results\ndata: {"results":[],"sources":[],"degraded":null,',
        '"answers_remaining":null,"cached_at":null}\n\nevent: thinking\ndata: {}\n\n',
        'event: delta\ndata: {"text":"Yes"}\n\nevent: done\ndata: {"answer":"Yes"}\n\n'
      ])
    );
    vi.stubGlobal('fetch', fetch);
    const { calls, on } = recorder();
    await streamQuery('q', {}, on);
    expect(calls.map((c) => c[0])).toEqual(['results', 'thinking', 'delta', 'done']);
    expect(calls[2][1]).toBe('Yes');
    expect(fetch).toHaveBeenCalledWith(
      '/api/v1/query/stream',
      expect.objectContaining({ method: 'POST', body: JSON.stringify({ query: 'q' }) })
    );
  });

  it('maps 429 to RateLimitedError before reading', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('', { status: 429 })));
    await expect(streamQuery('q', {}, recorder().on)).rejects.toBeInstanceOf(RateLimitedError);
  });

  it('rejects when the stream ends without done', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => sseResponse(['event: thinking\ndata: {}\n\n'])));
    await expect(streamQuery('q', {}, recorder().on)).rejects.toBeInstanceOf(StreamEndedError);
  });
});
