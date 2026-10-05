// Builders for answer-stream tests.
import type { Citation, QueryResult } from '../api';
import type { StreamDone, StreamHead } from './protocol';

export const citation = (number: number): Citation => ({
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

export const result = (title: string, cited = false): QueryResult => ({
  source: 'rule',
  title,
  text: 't',
  score: 1,
  cited
});

export function head(over: Partial<StreamHead> = {}): StreamHead {
  return {
    results: [result('a'), result('b')],
    sources: [citation(1), citation(2)],
    degraded: null,
    answers_remaining: 7,
    cached_at: null,
    ...over
  };
}

export function done(over: Partial<StreamDone> = {}): StreamDone {
  return {
    results: [result('a', true), result('b')],
    answer: 'Yes [1].',
    citations: [citation(1)],
    rule_references: [],
    citation_stats: { cited_count: 1, invalid_count: 0, uncited_answer: false },
    answer_complete: true,
    ...over
  };
}
