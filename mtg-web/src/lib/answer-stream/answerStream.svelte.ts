// The answer stream as Svelte state: runs a source through the reducer,
// aborts the previous run, and refreshes the shown draft at most once per
// frame (streaming spec decision 11).
import { RateLimitedError, type QueryResponse } from '../api';
import { isBusy, reduce, shown, type AnswerState } from './reduce';
import type { AnswerSource } from './sources';

export function createAnswerStream(source: AnswerSource) {
  let state = $state<AnswerState>({ phase: 'idle' });
  // The draft as last painted: a copy of the state's, refreshed once a frame.
  let shownDraft = $state('');
  let frame = 0;
  let controller: AbortController | null = null;

  function paintNextFrame() {
    if (frame) return;
    frame = requestAnimationFrame(() => {
      frame = 0;
      shownDraft = state.phase === 'streaming' ? state.draft : '';
    });
  }

  /** Ends whatever is running and returns the new run's controller. */
  function restart(): AbortController {
    controller?.abort();
    cancelAnimationFrame(frame);
    frame = 0;
    shownDraft = '';
    return (controller = new AbortController());
  }

  async function ask(query: string, fresh = false): Promise<'ok' | 'busy' | 'rate-limited'> {
    if (isBusy(state)) return 'busy';
    const before = state;
    const ctrl = restart();
    state = { phase: 'loading' };
    try {
      for await (const event of source(query, { fresh, signal: ctrl.signal })) {
        if (ctrl.signal.aborted) return 'ok';
        state = reduce(state, event, query);
        if (event.type === 'delta') paintNextFrame();
      }
      if (isBusy(state)) {
        // A source that ended quietly without `done`.
        state = reduce(state, { type: 'broken', message: 'The answer stopped arriving.' }, query);
      }
    } catch (e) {
      if (ctrl.signal.aborted) return 'ok';
      if (e instanceof RateLimitedError) {
        state = before;
        return 'rate-limited';
      }
      const message = e instanceof Error ? e.message : String(e);
      state = reduce(state, { type: 'broken', message }, query);
    } finally {
      if (controller === ctrl) {
        cancelAnimationFrame(frame);
        frame = 0;
      }
    }
    return 'ok';
  }

  async function load<T extends QueryResponse>(
    fetch: (signal: AbortSignal) => Promise<T>
  ): Promise<T | 'aborted' | Error> {
    const ctrl = restart();
    state = { phase: 'loading' };
    try {
      const response = await fetch(ctrl.signal);
      if (ctrl.signal.aborted) return 'aborted';
      state = { phase: 'result', response };
      return response;
    } catch (e) {
      if (ctrl.signal.aborted) return 'aborted';
      const error = e instanceof Error ? e : new Error(String(e));
      state = { phase: 'failed', message: error.message };
      return error;
    }
  }

  function reset() {
    restart();
    controller = null;
    state = { phase: 'idle' };
  }

  return {
    get state() {
      return state;
    },
    get shown() {
      return shown(state, shownDraft);
    },
    get busy() {
      return isBusy(state);
    },
    ask,
    load,
    reset
  };
}
