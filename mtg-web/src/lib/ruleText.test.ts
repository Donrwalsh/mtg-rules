import { describe, expect, it } from 'vitest';
import { splitRuleText } from './ruleText';

describe('splitRuleText', () => {
  it('links a three-digit rule after "rule"', () => {
    expect(splitRuleText('See rule 704.')).toEqual([
      { kind: 'text', text: 'See rule ' },
      { kind: 'rule', text: '704', ruleId: '704' },
      { kind: 'text', text: '.' }
    ]);
  });

  it('links dotted rule ids anywhere', () => {
    expect(splitRuleText('Under 702.19b and rules 510.1c.')).toEqual([
      { kind: 'text', text: 'Under ' },
      { kind: 'rule', text: '702.19b', ruleId: '702.19b' },
      { kind: 'text', text: ' and rules ' },
      { kind: 'rule', text: '510.1c', ruleId: '510.1c' },
      { kind: 'text', text: '.' }
    ]);
  });

  it('leaves plain numbers, prices and versions as text', () => {
    for (const s of ['Draw 100 cards.', 'It costs $100.50.', 'Version 100.5.2']) {
      expect(splitRuleText(s)).toEqual([{ kind: 'text', text: s }]);
    }
  });
});
