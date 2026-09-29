import { describe, expect, it } from 'vitest';
import { segmentAnswer } from './segments';

describe('segmentAnswer', () => {
  it('splits text, citation markers and rule references', () => {
    const segs = segmentAnswer('Lethal [1, 2]. See 702.2c.', new Set([1, 2]), new Set(['702.2c']));
    expect(segs).toEqual([
      { kind: 'text', text: 'Lethal ' },
      { kind: 'cite', numbers: [1, 2] },
      { kind: 'text', text: '. See ' },
      { kind: 'rule', ruleId: '702.2c' },
      { kind: 'text', text: '.' }
    ]);
  });

  it('leaves unknown citation numbers and unknown rules as text', () => {
    const segs = segmentAnswer('Odd [9] and 999.1a.', new Set([1]), new Set());
    expect(segs).toEqual([{ kind: 'text', text: 'Odd [9] and 999.1a.' }]);
  });

  it('keeps only the valid numbers of a mixed marker', () => {
    const segs = segmentAnswer('X [1, 9]', new Set([1]), new Set());
    expect(segs).toEqual([
      { kind: 'text', text: 'X ' },
      { kind: 'cite', numbers: [1] }
    ]);
  });

  it('does not treat prices or versions as rule numbers', () => {
    const segs = segmentAnswer('$100.50 or v100.5.2', new Set(), new Set(['100.5']));
    expect(segs).toEqual([{ kind: 'text', text: '$100.50 or v100.5.2' }]);
  });
});
