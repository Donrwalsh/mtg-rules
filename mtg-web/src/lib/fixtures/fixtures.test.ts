import { describe, expect, it } from 'vitest';
import { RateLimitedError } from '../api';
import { mockQuery } from './index';

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
