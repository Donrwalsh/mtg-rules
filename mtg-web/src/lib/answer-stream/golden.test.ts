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
