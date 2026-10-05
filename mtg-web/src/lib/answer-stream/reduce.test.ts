import { afterEach, describe, expect, it, vi } from 'vitest';
import type { QueryResponse } from '../api';
import { noticeFor } from '../status';
import type { StreamEvent } from './protocol';
import { isBusy, reduce, shown, type AnswerState, type Broken } from './reduce';
import { done, head } from './testing';

const Q = 'Does trample work?';
const results = (h = head()): StreamEvent => ({ type: 'results', head: h });
const thinking: StreamEvent = { type: 'thinking' };
const delta = (text: string): StreamEvent => ({ type: 'delta', text });
const finished = (d = done()): StreamEvent => ({ type: 'done', done: d });
const broken: Broken = { type: 'broken', message: 'The answer stopped arriving.' };

/** Reduces events starting from `loading`, as `ask()` does. */
function run(...events: (StreamEvent | Broken)[]): AnswerState {
  return events.reduce<AnswerState>((s, e) => reduce(s, e, Q), { phase: 'loading' });
}

function response(s: AnswerState): QueryResponse {
  if (s.phase !== 'result') throw new Error(`expected result, got ${s.phase}`);
  return s.response;
}

afterEach(() => vi.restoreAllMocks());

describe('reduce', () => {
  it('a full answer ends with the answer from done, not the draft', () => {
    const r = response(run(results(), thinking, delta('Dra'), delta('ft'), finished()));
    expect(r.query).toBe(Q);
    expect(r.answer).toBe('Yes [1].');
    expect(r.answer_complete).toBe(true);
    expect(r.answers_remaining).toBe(7);
    expect(r.results[0].cited).toBe(true);
  });

  it('a cache hit keeps cached_at from the head', () => {
    const r = response(run(results(head({ cached_at: '2026-01-01T00:00:00Z' })), finished()));
    expect(r.cached_at).toBe('2026-01-01T00:00:00Z');
  });

  it('error then done with no answer shows the no-answer notice', () => {
    const r = response(
      run(
        results(),
        thinking,
        { type: 'error', message: 'Gemini 500' },
        finished(done({ answer: null, citations: [], answer_complete: null }))
      )
    );
    expect(noticeFor(r)).toBe('no_answer');
  });

  it('a stream broken after text keeps the text as a cut-off answer', () => {
    const r = response(run(results(), delta('Yes [1]. And [2'), broken));
    expect(r.answer).toBe('Yes [1]. And');
    expect(r.answer_complete).toBe(false);
    expect(r.citations.map((c) => c.number)).toEqual([1]);
    expect(r.results.map((x) => x.cited)).toEqual([true, false]);
  });

  it('a stream broken before any text is a result with no answer', () => {
    const r = response(run(results(), thinking, broken));
    expect(r.answer).toBeNull();
    expect(r.answer_complete).toBeNull();
  });

  it('a stream broken before results fails with its message', () => {
    expect(run(broken)).toEqual({ phase: 'failed', message: 'The answer stopped arriving.' });
  });

  it.each([
    ['delta before results', [delta('Yes')]],
    ['done before results', [finished()]],
    ['thinking before results', [thinking]]
  ])('%s fails and logs once', (_name, events) => {
    const error = vi.spyOn(console, 'error').mockImplementation(() => {});
    expect(run(...events).phase).toBe('failed');
    expect(error).toHaveBeenCalledOnce();
  });

  it('results twice keeps what was written as a cut-off answer, and logs', () => {
    const error = vi.spyOn(console, 'error').mockImplementation(() => {});
    const r = response(run(results(), delta('Yes [1].'), results()));
    expect(r.answer).toBe('Yes [1].');
    expect(r.answer_complete).toBe(false);
    expect(error).toHaveBeenCalledOnce();
  });

  it('an event after the result leaves the result alone, and logs', () => {
    const error = vi.spyOn(console, 'error').mockImplementation(() => {});
    const r = response(run(results(), finished(), delta('late')));
    expect(r.answer).toBe('Yes [1].');
    expect(error).toHaveBeenCalledOnce();
  });
});

describe('shown', () => {
  it('while streaming: the draft with live citations, unfinished marker held back', () => {
    const s = run(results(), delta('A [1]. B [2'));
    const r = shown(s, 'A [1]. B [2')!;
    expect(r.answer).toBe('A [1]. B ');
    expect(r.citations.map((c) => c.number)).toEqual([1]);
    expect(r.results.map((x) => x.cited)).toEqual([true, false]);
    expect(r.query).toBe(Q);
  });

  it('uses the draft it is given (the throttled copy), not the state draft', () => {
    const s = run(results(), delta('Yes [1].'));
    expect(shown(s, '')!.answer).toBe('');
  });

  it('is the response once there is one, and null before', () => {
    const s = run(results(), finished());
    expect(shown(s, 'ignored')).toBe(response(s));
    expect(shown({ phase: 'idle' }, '')).toBeNull();
    expect(shown({ phase: 'loading' }, '')).toBeNull();
    expect(shown({ phase: 'failed', message: 'x' }, '')).toBeNull();
  });
});

describe('isBusy', () => {
  it('is true while loading or streaming', () => {
    expect(isBusy({ phase: 'idle' })).toBe(false);
    expect(isBusy({ phase: 'loading' })).toBe(true);
    expect(isBusy(run(results()))).toBe(true);
    expect(isBusy(run(results(), finished()))).toBe(false);
    expect(isBusy({ phase: 'failed', message: 'x' })).toBe(false);
  });
});
