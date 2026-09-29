import { describe, expect, it } from 'vitest';
import { segmentAnswer } from '../segments';
import { assignSentences, layoutAnswer, type Piece } from './layout';

const ANSWER =
  'Yes — each blocker only needs 1 damage. Deathtouch makes it lethal [1]. ' +
  'Trample assigns the excess [2].\n\nA Dreadmaw [4] with Basilisk Collar [3] assigns 1 each. See 510.1c.';

function pieces(answer = ANSWER): Piece[] {
  return assignSentences(segmentAnswer(answer, new Set([1, 2, 3, 4]), new Set(['510.1c'])));
}

const textOf = (list: Piece[]) =>
  list
    .map((p) => (p.kind === 'text' ? p.text : p.kind === 'rule' ? p.ruleId : `[${p.numbers}]`))
    .join('');

describe('assignSentences', () => {
  it('gives each marker the sentence it closes', () => {
    const cites = pieces().flatMap((p) => (p.kind === 'cite' ? [[p.numbers[0], p.sentence]] : []));
    expect(cites).toEqual([
      [1, 1],
      [2, 2],
      [4, 4],
      [3, 4]
    ]);
  });

  it('numbers pieces in order', () => {
    const ps = pieces();
    expect(ps.map((p) => p.index)).toEqual(ps.map((_, i) => i));
  });

  it('attaches a marker after the full stop to the sentence before', () => {
    const ps = pieces('Excess damage. [1] Next sentence.');
    const cite = ps.find((p) => p.kind === 'cite')!;
    const first = ps.find((p) => p.kind === 'text')!;
    expect(cite.sentence).toBe(first.sentence);
  });

  it('does not end a sentence at a rule number', () => {
    const ps = pieces('See 510.1c for details [1].');
    const sentences = new Set(
      ps.filter((p) => p.kind !== 'text' || p.text.trim() !== '.').map((p) => p.sentence)
    );
    expect(sentences.size).toBe(1);
  });
});

describe('layoutAnswer', () => {
  it('pulls out a short first sentence as the lead', () => {
    const layout = layoutAnswer(pieces());
    expect(textOf(layout.lead!)).toBe('Yes — each blocker only needs 1 damage.');
    expect(layout.paragraphs).toHaveLength(2);
    expect(textOf(layout.paragraphs[0])).toBe(
      'Deathtouch makes it lethal [1]. Trample assigns the excess [2].'
    );
    expect(textOf(layout.paragraphs[1])).toBe(
      'A Dreadmaw [4] with Basilisk Collar [3] assigns 1 each. See 510.1c.'
    );
  });

  it('has no lead when the first sentence is too long', () => {
    const long = 'x'.repeat(141) + '. Then more.';
    expect(layoutAnswer(pieces(long)).lead).toBeNull();
  });

  it('has no lead for a one-sentence answer', () => {
    expect(layoutAnswer(pieces('Only this.')).lead).toBeNull();
  });

  it('has no lead when the first line has no terminal punctuation', () => {
    expect(layoutAnswer(pieces('Short answer\nMore text here.')).lead).toBeNull();
  });

  it('keeps every piece when there is no lead', () => {
    const ps = pieces('Only this.');
    expect(textOf(layoutAnswer(ps).paragraphs.flat())).toBe('Only this.');
  });
});
