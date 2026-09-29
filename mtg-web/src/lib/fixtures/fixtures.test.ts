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

  it('error and ratelimited reject', async () => {
    await expect(mockQuery('error')).rejects.toThrow('query failed: 502');
    await expect(mockQuery('ratelimited')).rejects.toBeInstanceOf(RateLimitedError);
  });
});
