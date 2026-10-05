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
