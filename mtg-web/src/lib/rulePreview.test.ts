import { beforeEach, describe, expect, it, vi } from 'vitest';

const fetchRule = vi.fn();
vi.mock('./api', () => ({ fetchRule: (id: string) => fetchRule(id) }));

const { previewRule } = await import('./rulePreview');

describe('previewRule', () => {
  beforeEach(() => fetchRule.mockReset());

  it('titles a rule with its heading and caches it', async () => {
    fetchRule.mockResolvedValue({ rule_id: '702.2c', text: 'Lethal.', heading: 'Deathtouch' });
    expect(await previewRule('702.2c')).toEqual({ title: '702.2c · Deathtouch', text: 'Lethal.' });
    await previewRule('702.2c');
    expect(fetchRule).toHaveBeenCalledTimes(1);
  });

  it('returns null on failure and retries next time', async () => {
    fetchRule.mockRejectedValueOnce(new Error('boom'));
    expect(await previewRule('999.1')).toBeNull();
    fetchRule.mockResolvedValue({ rule_id: '999.1', text: 'T.', heading: null });
    expect(await previewRule('999.1')).toEqual({ title: 'Rule 999.1', text: 'T.' });
  });
});
