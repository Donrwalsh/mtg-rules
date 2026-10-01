import { describe, expect, it, vi } from 'vitest';
import { RateLimitedError, type StreamDone, type StreamHandlers } from '../api';
import { mockQuery, mockStream } from './index';

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

function record() {
  const events: string[] = [];
  let text = '';
  let done: StreamDone | null = null;
  const on: StreamHandlers = {
    results: () => events.push('results'),
    thinking: () => events.push('thinking'),
    delta: (t) => {
      if (events[events.length - 1] !== 'delta') events.push('delta');
      text += t;
    },
    error: () => events.push('error'),
    done: (d) => {
      events.push('done');
      done = d;
    }
  };
  return { events, on, text: () => text, done: (): StreamDone | null => done };
}

async function play(name: string) {
  vi.useFakeTimers();
  const r = record();
  const run = mockStream(name, r.on);
  await vi.runAllTimersAsync();
  await run;
  vi.useRealTimers();
  return r;
}

describe('mockStream', () => {
  it.each([
    ['streaming', ['results', 'thinking', 'delta', 'done'], true],
    ['cutoff', ['results', 'thinking', 'delta', 'done'], false],
    ['streamerror', ['results', 'thinking', 'error', 'done'], null],
    ['answered', ['results', 'done'], true],
    ['quota', ['results', 'done'], null]
  ])('%s sends %j', async (name, expected, complete) => {
    const r = await play(name);
    expect(r.events).toEqual(expected);
    expect(r.done()?.answer_complete).toBe(complete);
  });

  it('streaming deltas add up to the answer', async () => {
    const r = await play('streaming');
    expect(r.text()).toBe(r.done()?.answer);
  });

  it('stops when aborted', async () => {
    const controller = new AbortController();
    const r = record();
    const run = mockStream('streaming', r.on, controller.signal);
    controller.abort();
    await expect(run).rejects.toThrow();
    expect(r.events).not.toContain('done');
  });
});
