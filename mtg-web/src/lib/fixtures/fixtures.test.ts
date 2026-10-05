import { describe, expect, it, vi } from 'vitest';
import { RateLimitedError } from '../api';
import type { StreamDone, StreamEvent } from '../answer-stream/protocol';
import { fixtureSource, mockQuery } from './index';

describe('fixtures', () => {
  it('answered cites five sources and retrieves six more', async () => {
    const r = await mockQuery('answered');
    expect(r.citations.map((c) => c.number)).toEqual([1, 2, 3, 4, 5]);
    expect(r.results.filter((x) => !x.cited)).toHaveLength(6);
  });

  it.each([
    ['uncited', null],
    ['quota', 'ip_quota'],
    ['breather', 'ip_quota'],
    ['paused', 'global_budget']
  ])('%s degrades as %s', async (name, degraded) => {
    expect((await mockQuery(name)).degraded).toBe(degraded);
  });

  it('images gives art to every card but Basilisk Collar', async () => {
    const r = await mockQuery('images');
    const cards = [...r.citations.map((c) => c.card), ...r.results.map((x) => x.card)].filter(
      (c) => c != null
    );
    for (const c of cards) {
      if (c.name === 'Basilisk Collar') {
        expect(c.image_normal).toBeNull();
      } else {
        expect(c.image_normal).toMatch(/^https:\/\/cards\.scryfall\.io\/normal\//);
        expect(c.image_small).toBe(c.image_normal!.replace('/normal/', '/small/'));
        expect(c.image_large).toBe(c.image_normal!.replace('/normal/', '/large/'));
      }
    }
    expect(cards.some((c) => c.name === 'Basilisk Collar')).toBe(true);
    expect(cards.some((c) => c.name === 'Windswift Slice' && c.image_normal)).toBe(true);
  });

  it('answered keeps null art', async () => {
    const r = await mockQuery('answered');
    expect(r.citations.every((c) => !c.card?.image_normal)).toBe(true);
  });

  it('error and ratelimited reject', async () => {
    await expect(mockQuery('error')).rejects.toThrow('query failed: 502');
    await expect(mockQuery('ratelimited')).rejects.toBeInstanceOf(RateLimitedError);
  });
});

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
