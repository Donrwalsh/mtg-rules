// Helpers for showing an answer while it streams. The server validates
// citations only when the answer is done; until then markers link to the
// `sources` sent up front, and `done` replaces all of this.
import type { Citation, QueryResult } from './api';

// An opening bracket at the very end that hasn't closed yet: "[", "[1", "[1, ".
const OPEN_MARKER = /\[[\d,\s]*$/;
const MARKER = /\[(\s*\d+(?:\s*,\s*\d+)*\s*)\]/g;

/** The draft as it can be shown: an unfinished citation marker at the end
 * waits until it closes, so it never flashes up as "[1". */
export function visibleDraft(draft: string): string {
  return draft.replace(OPEN_MARKER, '');
}

/** The known sources the draft cites so far, ordered by number like the
 * server's list. Unknown numbers stay plain text until `done` removes them. */
export function liveCitations(draft: string, sources: Citation[]): Citation[] {
  const byNumber = new Map(sources.map((s) => [s.number, s]));
  const cited = new Set<number>();
  for (const match of draft.matchAll(MARKER)) {
    for (const part of match[1].split(',')) {
      const n = parseInt(part.trim(), 10);
      if (byNumber.has(n)) cited.add(n);
    }
  }
  return [...cited].sort((a, b) => a - b).map((n) => byNumber.get(n)!);
}

/** Results with `cited` set from the live citations (result i is source
 * i + 1), so a source isn't listed as both cited and "also retrieved". */
export function liveResults(results: QueryResult[], citations: Citation[]): QueryResult[] {
  const numbers = new Set(citations.map((c) => c.number));
  return results.map((r, i) => ({ ...r, cited: numbers.has(i + 1) }));
}
