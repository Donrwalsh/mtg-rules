import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { RateLimitedError, type QueryResponse } from '../api';
import { createAnswerStream } from './answerStream.svelte';
import type { StreamEvent } from './protocol';
import { StreamEndedError, type AnswerSource } from './sources';
import { done, head } from './testing';

/** A source that yields `events`, then throws `end` if given, or hangs until
 * aborted when `end` is 'hang'. */
function scripted(events: StreamEvent[], end?: Error | 'hang'): AnswerSource {
  return async function* (_q, { signal }) {
    for (const e of events) yield e;
    if (end === 'hang') {
      await new Promise((_, reject) =>
        signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')))
      );
    }
    if (end) throw end;
  };
}

/** A source that plays each script in turn, one per ask. */
function sequence(...sources: AnswerSource[]): AnswerSource {
  let i = 0;
  return (q, opts) => sources[i++](q, opts);
}

/** Lets the source and the binding run until they wait on something. */
const settle = () => new Promise((resolve) => setTimeout(resolve, 5));
const results: StreamEvent = { type: 'results', head: head() };

// Animation frames run only when a test calls paint().
let frames: FrameRequestCallback[] = [];
function paint() {
  const due = frames;
  frames = [];
  for (const cb of due) cb(0);
}

beforeEach(() => {
  frames = [];
  vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => frames.push(cb));
  vi.stubGlobal('cancelAnimationFrame', () => {});
});
afterEach(() => vi.unstubAllGlobals());

describe('createAnswerStream', () => {
  it('runs a stream to its result', async () => {
    const answer = createAnswerStream(
      scripted([results, { type: 'delta', text: 'Yes' }, { type: 'done', done: done() }])
    );
    expect(await answer.ask('q')).toBe('ok');
    expect(answer.state.phase).toBe('result');
    expect(answer.shown?.answer).toBe('Yes [1].');
    expect(answer.busy).toBe(false);
  });

  it('refuses to ask while an answer streams', async () => {
    const answer = createAnswerStream(scripted([results], 'hang'));
    const first = answer.ask('q');
    await settle();
    expect(answer.state.phase).toBe('streaming');
    expect(answer.busy).toBe(true);
    expect(await answer.ask('again')).toBe('busy');
    expect(answer.state.phase).toBe('streaming');
    answer.reset();
    expect(await first).toBe('ok');
    expect(answer.state.phase).toBe('idle');
  });

  it('shows the draft once per frame while streaming', async () => {
    const answer = createAnswerStream(
      scripted([results, { type: 'delta', text: 'Yes [1].' }], 'hang')
    );
    void answer.ask('q');
    await settle();
    expect(answer.state.phase).toBe('streaming');
    expect(answer.shown?.answer).toBe('');
    paint();
    expect(answer.shown?.answer).toBe('Yes [1].');
    answer.reset();
  });

  it('a broken stream, then a 429, keeps the cut-off answer', async () => {
    const answer = createAnswerStream(
      sequence(
        scripted([results, { type: 'delta', text: 'Yes [1].' }], new StreamEndedError('gone')),
        scripted([], new RateLimitedError('busy'))
      )
    );
    await answer.ask('q');
    const cutOff = answer.shown;
    expect(cutOff?.answer_complete).toBe(false);
    expect(await answer.ask('again')).toBe('rate-limited');
    expect(answer.state.phase).toBe('result');
    expect(answer.shown).toEqual(cutOff);
  });

  it('a failure before results shows the error', async () => {
    const answer = createAnswerStream(scripted([], new Error('query failed: 502')));
    await answer.ask('q');
    expect(answer.state).toEqual({ phase: 'failed', message: 'query failed: 502' });
  });

  it('load shows a saved response, or fails with its error', async () => {
    const answer = createAnswerStream(scripted([]));
    const saved = { query: 'q', answer: 'Saved' } as QueryResponse;
    expect(await answer.load(async () => saved)).toEqual(saved);
    expect(answer.shown?.answer).toBe('Saved');
    const err = await answer.load(async () => {
      throw new Error('replay failed: 500');
    });
    expect(err).toBeInstanceOf(Error);
    expect(answer.state).toEqual({ phase: 'failed', message: 'replay failed: 500' });
  });

  it('a later load wins over an earlier one', async () => {
    const answer = createAnswerStream(scripted([]));
    let finish!: (r: QueryResponse) => void;
    const first = answer.load(() => new Promise<QueryResponse>((resolve) => (finish = resolve)));
    const second = answer.load(async () => ({ query: 'b', answer: 'B' }) as QueryResponse);
    finish({ query: 'a', answer: 'A' } as QueryResponse);
    expect(await first).toBe('aborted');
    await second;
    expect(answer.shown?.answer).toBe('B');
  });
});
