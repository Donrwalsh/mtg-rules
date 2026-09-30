import { describe, expect, it } from 'vitest';
import type { Citation, QueryResult } from './api';
import { liveCitations, liveResults, visibleDraft } from './stream';

const source = (number: number): Citation => ({
  number,
  source_type: 'rule',
  title: `Rule ${number}`,
  rule_id: String(number),
  card_name: null,
  oracle_id: null,
  text: 't',
  url: null,
  published_at: null
});

const result = (title: string): QueryResult => ({
  source: 'rule',
  title,
  text: 't',
  score: 1
});

describe('visibleDraft', () => {
  it('holds back an unfinished marker at the end', () => {
    expect(visibleDraft('Yes [1')).toBe('Yes ');
    expect(visibleDraft('Yes [1, ')).toBe('Yes ');
    expect(visibleDraft('Yes [')).toBe('Yes ');
  });

  it('keeps finished markers and ordinary text', () => {
    expect(visibleDraft('Yes [1].')).toBe('Yes [1].');
    expect(visibleDraft('Rule 702.19')).toBe('Rule 702.19');
  });
});

describe('liveCitations', () => {
  it('returns cited known sources once each, by number', () => {
    const sources = [source(1), source(2), source(3)];
    expect(liveCitations('A [3]. B [1, 3]. C [9]. D [2', sources).map((c) => c.number)).toEqual([
      1, 3
    ]);
  });
});

describe('liveResults', () => {
  it('marks result i as cited when source i + 1 is cited', () => {
    const out = liveResults([result('a'), result('b')], [source(2)]);
    expect(out.map((r) => r.cited)).toEqual([false, true]);
  });
});
