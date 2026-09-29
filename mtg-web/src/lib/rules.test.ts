import { describe, expect, it } from 'vitest';
import {
  CR_SECTIONS,
  entryIdOf,
  isTopLevel,
  neighbours,
  normalizeRuleId,
  sectionOf,
  windowAround
} from './rules';

const list = ['702.1', '702.2', '702.3', '702.4', '702.5', '702.6'].map((rule_id) => ({ rule_id }));

describe('rule ids', () => {
  it('normalises the way the API does', () => {
    expect(normalizeRuleId(' 702.19B. ')).toBe('702.19b');
  });

  it('finds the entry a rule belongs to', () => {
    expect(['702.2c', '702.2', '702', '100.1a'].map(entryIdOf)).toEqual([
      '702.2',
      '702.2',
      '702',
      '100.1'
    ]);
  });

  it('knows top-level rules and sections', () => {
    expect(isTopLevel('702')).toBe(true);
    expect(isTopLevel('702.2')).toBe(false);
    expect(sectionOf('702.2c')).toBe(7);
    expect(sectionOf('bogus')).toBeNull();
    expect(CR_SECTIONS[7]).toBe('Additional Rules');
  });
});

describe('neighbours', () => {
  it('finds previous and next', () => {
    const n = neighbours(list, '702.3');
    expect([n.prev?.rule_id, n.next?.rule_id]).toEqual(['702.2', '702.4']);
  });

  it('is null at either end and for unknown ids', () => {
    expect(neighbours(list, '702.1').prev).toBeNull();
    expect(neighbours(list, '702.6').next).toBeNull();
    expect(neighbours(list, '999.1')).toEqual({ prev: null, next: null });
  });
});

describe('windowAround', () => {
  const ids = (xs: { rule_id: string }[]) => xs.map((x) => x.rule_id);

  it('centres on the id', () => {
    expect(ids(windowAround(list, '702.4', 3))).toEqual(['702.3', '702.4', '702.5']);
  });

  it('clamps at the start and end', () => {
    expect(ids(windowAround(list, '702.1', 3))).toEqual(['702.1', '702.2', '702.3']);
    expect(ids(windowAround(list, '702.6', 3))).toEqual(['702.4', '702.5', '702.6']);
  });

  it('returns the whole list when it is short', () => {
    expect(windowAround(list, '702.2', 14)).toHaveLength(6);
  });
});
