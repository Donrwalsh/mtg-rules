import { describe, expect, it } from 'vitest';
import type { QueryResponse } from '../api';
import { decode, DONE_KEYS, HEAD_KEYS, splitResponse } from './protocol';

describe('decode', () => {
  it('turns each wire event into a StreamEvent', () => {
    expect(decode('results', '{"results":[],"sources":[]}')).toEqual({
      type: 'results',
      head: { results: [], sources: [] }
    });
    expect(decode('thinking', '{}')).toEqual({ type: 'thinking' });
    expect(decode('delta', '{"text":"Yes"}')).toEqual({ type: 'delta', text: 'Yes' });
    expect(decode('error', '{"message":"Gemini 500"}')).toEqual({
      type: 'error',
      message: 'Gemini 500'
    });
    expect(decode('done', '{"answer":"Yes"}')).toEqual({ type: 'done', done: { answer: 'Yes' } });
  });

  it('ignores events it does not know', () => {
    expect(decode('usage', '{}')).toBeNull();
  });
});

describe('splitResponse', () => {
  const r: QueryResponse = {
    query: 'q',
    results: [],
    answer: 'Yes',
    citations: [],
    rule_references: [],
    citation_stats: { cited_count: 0, invalid_count: 0, uncited_answer: false },
    answer_complete: true,
    cached_at: null,
    degraded: null,
    answers_remaining: 3
  };

  it('puts every field but query in exactly one of head and done, results in both', () => {
    const { head, done } = splitResponse(r, []);
    expect(Object.keys(head).sort()).toEqual([...HEAD_KEYS].sort());
    expect(Object.keys(done).sort()).toEqual([...DONE_KEYS].sort());
    const both = new Set<string>([...HEAD_KEYS, ...DONE_KEYS, 'query']);
    both.delete('sources');
    expect([...both].sort()).toEqual(Object.keys(r).sort());
  });
});
