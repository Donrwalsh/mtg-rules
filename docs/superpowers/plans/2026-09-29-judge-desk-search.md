# Judge's Desk Search Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild `/` as the Judge's Desk: an answer with a summary line, selectable citations that highlight their sentence and evidence card, an evidence panel with tabs, a phone bottom sheet, and every empty, loading, limit and error state from the design canvas.

**Architecture:** Pure TypeScript modules (answer layout, evidence normalisation, status wording, dates) carry the logic and are unit-tested with Vitest. Small Svelte 5 components under `src/lib/desk/` render them with the Tailwind tokens from PR 1. `+page.svelte` holds one view state machine and the citation selection, and picks the desktop, tablet or phone layout with `MediaQuery`. In dev, `?mock=<name>` swaps the API for fixtures so every board can be checked by eye.

**Tech Stack:** SvelteKit 2 + Svelte 5 (runes, snippets, `svelte/reactivity` `MediaQuery`, `$props.id()`), Tailwind v4 tokens, Vitest.

**Spec:** [docs/superpowers/specs/2026-09-29-judge-desk-search-design.md](../specs/2026-09-29-judge-desk-search-design.md)

**Design canvas:** https://claude.ai/artifact/XzoXtd9SdWQsQvPr2cF6G6. Board sources can be read with the Artifact tool (`read`, `path: "project/<Board>.dc.html"`) when a detail is unclear.

## Global Constraints

- Depends on PR 1 (tokens, `AppHeader` with `center` snippet, runes) and PR 2 (`card`, `heading` on results, citations and rule details; `fetchMeta`). Start from `main` after both merge.
- Colours only via the PR 1 tokens (`bg-card`, `text-fg-muted`, …). No hex values in `.svelte` files.
- Breakpoints: phone `< 640px` (`max-sm:` / `sm:`), desktop `≥ 1100px` (`desk:` / `max-desk:`).
- Tap targets ≥ 44px on phone (markers ≥ 32px tall with 8px padding, as drawn).
- Model output is rendered as text segments only, never `{@html}`.
- Every icon is inline SVG (`Icon.svelte`); icon-only buttons have `aria-label`.
- Summary line only when the first sentence is ≤ 140 characters, ends in `.`, `!` or `?`, and more text follows.
- Copy strings exactly as the spec gives them.
- Dev fixtures are reachable only when `import.meta.env.DEV`.
- Commits: conventional prefixes, ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Branch: `feature/judge-desk-search`, cut from `main`.

## File Structure

**Logic (`mtg-web/src/lib/`)**, each with a sibling `*.test.ts`:
- `format.ts`: `shortDate`, `calendarDate`, `resetTime`.
- `status.ts`: `noticeFor`, `isRunningLow`, `answersLeftLabel`.
- `evidence.ts`: `EvidenceItem`, `sourceKind`, `fromCitation`, `fromResult`, `uncitedItems`, `groupByKind`, `statLine`, `cardSummary`, `parentRule`.
- `answer/layout.ts`: `Piece`, `assignSentences`, `layoutAnswer`.
- `selection.ts`: `Selection` type.
- `rulePreview.ts`: cached rule previews.
- `meta.svelte.ts`: `meta` state and `loadMeta()`.
- `fixtures/index.ts`: dev fixtures and `mockQuery()`.

**Components (`mtg-web/src/lib/desk/`)**
- `Icon.svelte`, `Popover.svelte`, `SearchForm.svelte`
- `StatusChips.svelte`, `CitationMarker.svelte`, `RuleLink.svelte`, `AnswerBody.svelte`, `RulesReferenced.svelte`
- `EvidenceCard.svelte`, `EvidencePanel.svelte`, `SourceSheet.svelte`
- `EmptyState.svelte`, `LoadingState.svelte`, `ErrorState.svelte`, `StatusNotice.svelte`, `MatchingSources.svelte`

**Page**: `mtg-web/src/routes/+page.svelte` (rewritten). `CitedAnswer.svelte` and `SourcesList.svelte` stay for `/history` until PR 4.

---

### Task 1: Formatting, status and evidence logic

**Files:**
- Create: `mtg-web/src/lib/format.ts`, `format.test.ts`, `status.ts`, `status.test.ts`, `evidence.ts`, `evidence.test.ts`

**Interfaces:**
- Consumes: `CardDetails`, `Citation`, `QueryResult`, `QueryResponse` from `$lib/api` (PR 2 fields `card?`, `heading?`).
- Produces:

```ts
// format.ts
export function shortDate(iso: string, locale?: string): string;            // "Sep 27"
export function calendarDate(date: string, locale?: string): string;        // "2023-06-16" -> "Jun 16, 2023" (UTC)
export function resetTime(now?: Date, locale?: string, timeZone?: string): string; // next UTC midnight, local "8:00 PM"
// status.ts
export type Notice = 'paused' | 'quota_used' | 'breather' | 'no_answer';
export const RUNNING_LOW_AT = 3;
export function noticeFor(r: Pick<QueryResponse, 'answer' | 'degraded' | 'answers_remaining'>): Notice | null;
export function isRunningLow(remaining: number | null): boolean;
export function answersLeftLabel(n: number, compact?: boolean): string;
// evidence.ts
export type SourceKind = 'rule' | 'card' | 'ruling';
export interface EvidenceItem {
  key: string; number: number | null; kind: SourceKind; title: string;
  ruleId: string | null; heading: string | null; cardName: string | null;
  card: CardDetails | null; text: string; url: string | null; publishedAt: string | null;
}
export function sourceKind(source: string): SourceKind;
export function fromCitation(c: Citation): EvidenceItem;
export function fromResult(r: QueryResult, index: number): EvidenceItem;
export function uncitedItems(results: QueryResult[]): EvidenceItem[];
export function groupByKind(items: EvidenceItem[]): Record<SourceKind, EvidenceItem[]>;
export function statLine(card: CardDetails): string;       // "Creature — Dinosaur · 6/6"
export function cardSummary(item: EvidenceItem): string;   // "Creature — Dinosaur · 6/6 · Trample"
export function parentRule(ruleId: string): string | null; // 702.2c -> 702.2 -> 702 -> null
```

- [ ] **Step 1: Write the failing tests**

`src/lib/format.test.ts`:

```ts
import { describe, expect, it } from 'vitest';
import { calendarDate, resetTime, shortDate } from './format';

// ICU may use U+202F before AM/PM; compare with plain spaces.
const plain = (s: string) => s.replace(/\s/g, ' ');

describe('format', () => {
  it('shortDate gives month and day', () => {
    expect(shortDate('2026-09-27T12:00:00Z', 'en-US')).toBe('Sep 27');
  });
  it('calendarDate reads a date-only string as a calendar day', () => {
    expect(calendarDate('2023-06-16', 'en-US')).toBe('Jun 16, 2023');
  });
  it('resetTime is the next UTC midnight in the given zone', () => {
    const now = new Date('2026-09-29T15:00:00Z');
    expect(plain(resetTime(now, 'en-US', 'America/New_York'))).toBe('8:00 PM');
    expect(plain(resetTime(now, 'en-US', 'UTC'))).toBe('12:00 AM');
  });
});
```

`src/lib/status.test.ts`:

```ts
import { describe, expect, it } from 'vitest';
import { answersLeftLabel, isRunningLow, noticeFor } from './status';

describe('noticeFor', () => {
  const base = { answer: 'A.', degraded: null, answers_remaining: 5 } as const;
  it('is null for a normal answer', () => expect(noticeFor(base)).toBeNull());
  it('pauses for the global budget', () =>
    expect(noticeFor({ ...base, answer: null, degraded: 'global_budget' })).toBe('paused'));
  it('is quota_used when none are left', () =>
    expect(noticeFor({ ...base, answer: null, degraded: 'ip_quota', answers_remaining: 0 })).toBe(
      'quota_used'
    ));
  it('is a breather when the window, not the day, is used up', () =>
    expect(noticeFor({ ...base, answer: null, degraded: 'ip_quota', answers_remaining: 12 })).toBe(
      'breather'
    ));
  it('is no_answer when generation failed without degrading', () =>
    expect(noticeFor({ ...base, answer: null })).toBe('no_answer'));
});

describe('answers left', () => {
  it('runs low at 3 or fewer', () => {
    expect(isRunningLow(3)).toBe(true);
    expect(isRunningLow(4)).toBe(false);
    expect(isRunningLow(null)).toBe(false);
  });
  it('labels', () => {
    expect(answersLeftLabel(7)).toBe('7 answers left today');
    expect(answersLeftLabel(7, true)).toBe('7 left today');
    expect(answersLeftLabel(1)).toBe('1 AI answer left today');
    expect(answersLeftLabel(2)).toBe('2 AI answers left today');
  });
});
```

`src/lib/evidence.test.ts`:

```ts
import { describe, expect, it } from 'vitest';
import type { Citation, QueryResult } from './api';
import {
  cardSummary,
  fromCitation,
  fromResult,
  groupByKind,
  parentRule,
  sourceKind,
  statLine,
  uncitedItems
} from './evidence';

const dreadmaw = {
  name: 'Colossal Dreadmaw',
  type_line: 'Creature — Dinosaur',
  mana_cost: '{4}{G}{G}',
  power: '6',
  toughness: '6',
  loyalty: null,
  image_small: null,
  image_normal: null
};

function result(over: Partial<QueryResult>): QueryResult {
  return { source: 'rule', title: '702.2c', text: 'x', score: 1, ...over } as QueryResult;
}

describe('evidence', () => {
  it('maps sources to three kinds', () => {
    expect(['rule', 'card', 'oracle', 'ruling'].map(sourceKind)).toEqual([
      'rule',
      'card',
      'card',
      'ruling'
    ]);
  });

  it('normalises a citation', () => {
    const c: Citation = {
      number: 4,
      source_type: 'card',
      title: 'Card — Colossal Dreadmaw',
      rule_id: null,
      card_name: 'Colossal Dreadmaw',
      oracle_id: 'o',
      text: 'Trample',
      url: 'https://scryfall.com/card/x',
      published_at: null,
      card: dreadmaw,
      heading: null
    };
    expect(fromCitation(c)).toMatchObject({ key: 'c4', number: 4, kind: 'card', card: dreadmaw });
  });

  it('normalises results, linking rules to our route', () => {
    const rule = fromResult(result({ rule_id: '702.2c', heading: 'Deathtouch' }), 3);
    expect(rule).toMatchObject({ key: 'r3', number: null, ruleId: '702.2c', url: '/rules/702.2c' });
    const ruling = fromResult(
      result({ source: 'ruling', title: 'Windswift Slice', card_name: 'Windswift Slice' }),
      0
    );
    expect(ruling).toMatchObject({ kind: 'ruling', cardName: 'Windswift Slice' });
  });

  it('keeps only uncited results, in order', () => {
    const items = uncitedItems([
      result({ title: 'a', cited: true }),
      result({ title: 'b' }),
      result({ title: 'c' })
    ]);
    expect(items.map((i) => [i.title, i.key])).toEqual([
      ['b', 'r1'],
      ['c', 'r2']
    ]);
  });

  it('groups by kind', () => {
    const g = groupByKind(
      [result({}), result({ source: 'oracle' }), result({ source: 'ruling' })].map(fromResult)
    );
    expect([g.rule.length, g.card.length, g.ruling.length]).toEqual([1, 1, 1]);
  });

  it('describes a card', () => {
    expect(statLine(dreadmaw)).toBe('Creature — Dinosaur · 6/6');
    expect(statLine({ ...dreadmaw, power: null, toughness: null, loyalty: '3' })).toBe(
      'Creature — Dinosaur · Loyalty 3'
    );
    const item = fromResult(result({ source: 'card', text: 'Trample\nMore', card: dreadmaw }), 0);
    expect(cardSummary(item)).toBe('Creature — Dinosaur · 6/6 · Trample');
  });

  it('finds a rule parent', () => {
    expect(['702.2c', '702.2', '702', 'bogus'].map(parentRule)).toEqual([
      '702.2',
      '702',
      null,
      null
    ]);
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run (in `mtg-web/`): `npm test`
Expected: FAIL (cannot resolve `./format`, `./status`, `./evidence`).

- [ ] **Step 3: Implement**

`src/lib/format.ts`:

```ts
export function shortDate(iso: string, locale?: string): string {
  return new Date(iso).toLocaleDateString(locale, { month: 'short', day: 'numeric' });
}

// Scryfall and rules dates are calendar days ("2023-06-16"). Format them in
// UTC so a western time zone doesn't show the day before.
export function calendarDate(date: string, locale?: string): string {
  return new Date(`${date}T00:00:00Z`).toLocaleDateString(locale, {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    timeZone: 'UTC'
  });
}

// Quotas and the budget reset at UTC midnight; show it in local time.
export function resetTime(now = new Date(), locale?: string, timeZone?: string): string {
  const next = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() + 1));
  return next.toLocaleTimeString(locale, { hour: 'numeric', minute: '2-digit', timeZone });
}
```

`src/lib/status.ts`:

```ts
import type { QueryResponse } from './api';

export type Notice = 'paused' | 'quota_used' | 'breather' | 'no_answer';

export const RUNNING_LOW_AT = 3;

// Which notice replaces the answer, if any. An ip_quota degrade with answers
// still left means the 10-minute window, not the day, is used up.
export function noticeFor(
  r: Pick<QueryResponse, 'answer' | 'degraded' | 'answers_remaining'>
): Notice | null {
  if (r.degraded === 'global_budget') return 'paused';
  if (r.degraded === 'ip_quota') return r.answers_remaining ? 'breather' : 'quota_used';
  return r.answer ? null : 'no_answer';
}

export function isRunningLow(remaining: number | null): boolean {
  return remaining !== null && remaining <= RUNNING_LOW_AT;
}

export function answersLeftLabel(n: number, compact = false): string {
  if (compact) return `${n} left today`;
  if (isRunningLow(n)) return `${n} AI answer${n === 1 ? '' : 's'} left today`;
  return `${n} answers left today`;
}
```

`src/lib/evidence.ts`:

```ts
import type { CardDetails, Citation, QueryResult } from './api';

export type SourceKind = 'rule' | 'card' | 'ruling';

// One shape for everything the evidence panel, the sheet and the matching-
// sources list show, whether it came from a citation or a plain result.
export interface EvidenceItem {
  key: string;
  number: number | null; // citation number; null for uncited results
  kind: SourceKind;
  title: string;
  ruleId: string | null;
  heading: string | null;
  cardName: string | null;
  card: CardDetails | null;
  text: string;
  url: string | null;
  publishedAt: string | null;
}

export function sourceKind(source: string): SourceKind {
  if (source === 'card' || source === 'oracle') return 'card';
  if (source === 'ruling') return 'ruling';
  return 'rule';
}

export function fromCitation(c: Citation): EvidenceItem {
  return {
    key: `c${c.number}`,
    number: c.number,
    kind: sourceKind(c.source_type),
    title: c.title,
    ruleId: c.rule_id,
    heading: c.heading ?? null,
    cardName: c.card_name,
    card: c.card ?? null,
    text: c.text,
    url: c.url,
    publishedAt: c.published_at
  };
}

export function fromResult(r: QueryResult, index: number): EvidenceItem {
  const kind = sourceKind(r.source);
  const ruleId = kind === 'rule' ? (r.rule_id ?? r.title) : null;
  return {
    key: `r${index}`,
    number: null,
    kind,
    title: r.title,
    ruleId,
    heading: r.heading ?? null,
    cardName: kind === 'rule' ? null : (r.card_name ?? r.title),
    card: r.card ?? null,
    text: r.text,
    url: ruleId ? `/rules/${ruleId}` : (r.scryfall_uri ?? null),
    publishedAt: r.published_at ?? null
  };
}

export function uncitedItems(results: QueryResult[]): EvidenceItem[] {
  return results.flatMap((r, i) => (r.cited ? [] : [fromResult(r, i)]));
}

export function groupByKind(items: EvidenceItem[]): Record<SourceKind, EvidenceItem[]> {
  const groups: Record<SourceKind, EvidenceItem[]> = { rule: [], card: [], ruling: [] };
  for (const item of items) groups[item.kind].push(item);
  return groups;
}

export function statLine(card: CardDetails): string {
  const parts = [card.type_line];
  if (card.power !== null && card.toughness !== null) parts.push(`${card.power}/${card.toughness}`);
  else if (card.loyalty !== null) parts.push(`Loyalty ${card.loyalty}`);
  return parts.filter(Boolean).join(' · ');
}

// The one-line description on a compact (phone) card row.
export function cardSummary(item: EvidenceItem): string {
  const firstLine = item.text.split('\n')[0].trim();
  return [item.card ? statLine(item.card) : '', firstLine].filter(Boolean).join(' · ');
}

export function parentRule(ruleId: string): string | null {
  const sub = ruleId.match(/^(\d{3}\.\d+)[a-z]$/);
  if (sub) return sub[1];
  const rule = ruleId.match(/^(\d{3})\.\d+$/);
  return rule ? rule[1] : null;
}
```

- [ ] **Step 4: Run tests and typecheck**

Run: `npm test && npm run check`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mtg-web/src/lib/format.ts mtg-web/src/lib/format.test.ts mtg-web/src/lib/status.ts mtg-web/src/lib/status.test.ts mtg-web/src/lib/evidence.ts mtg-web/src/lib/evidence.test.ts
git commit -m "feat: date, status and evidence helpers for the search page"
```

---

### Task 2: Answer layout: sentences, summary line and paragraphs

**Files:**
- Create: `mtg-web/src/lib/answer/layout.ts`, `mtg-web/src/lib/answer/layout.test.ts`, `mtg-web/src/lib/selection.ts`

**Interfaces:**
- Consumes: `Segment`, `segmentAnswer` from `$lib/segments`.
- Produces:

```ts
export type Piece = Segment & { sentence: number; index: number };
export function assignSentences(segments: Segment[]): Piece[];
export interface AnswerLayout { lead: Piece[] | null; paragraphs: Piece[][] }
export function layoutAnswer(pieces: Piece[], maxLeadChars?: number): AnswerLayout;
// selection.ts
export interface Selection { number: number; occurrence: number | null } // occurrence = Piece.index of the clicked marker
```

Rules:
- A sentence ends at `.`, `!` or `?` (one or more) followed by whitespace or the end of a text segment, or at a newline.
- A marker that comes right after a boundary, with only whitespace between them ("…excess. [5]"), belongs to the sentence before.
- Rule links don't end sentences.

- [ ] **Step 1: Write the failing tests** (`src/lib/answer/layout.test.ts`)

```ts
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
  list.map((p) => (p.kind === 'text' ? p.text : p.kind === 'rule' ? p.ruleId : `[${p.numbers}]`)).join('');

describe('assignSentences', () => {
  it('gives each marker the sentence it closes', () => {
    const cites = pieces().filter((p) => p.kind === 'cite');
    expect(cites.map((p) => [p.kind === 'cite' && p.numbers[0], p.sentence])).toEqual([
      [1, 1],
      [2, 2],
      [4, 4],
      [3, 4]
    ]);
  });

  it('numbers pieces in order', () => {
    expect(pieces().map((p) => p.index)).toEqual(pieces().map((_, i) => i));
  });

  it('attaches a marker after the full stop to the sentence before', () => {
    const ps = pieces('Excess damage. [1] Next sentence.');
    const cite = ps.find((p) => p.kind === 'cite')!;
    const first = ps.find((p) => p.kind === 'text')!;
    expect(cite.sentence).toBe(first.sentence);
  });

  it('does not end a sentence at a rule number', () => {
    const ps = pieces('See 510.1c for details [1].');
    expect(new Set(ps.filter((p) => p.kind !== 'text' || p.text.trim() !== '.').map((p) => p.sentence)).size).toBe(1);
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
```

- [ ] **Step 2: Run to verify failure**

Run: `npm test`
Expected: FAIL (cannot resolve `./layout`).

- [ ] **Step 3: Implement**

`src/lib/selection.ts`:

```ts
// The citation the reader is looking at. `occurrence` is the Piece.index of
// the marker they clicked, so the sentence highlight follows that marker when
// the same source is cited more than once; null when chosen from the sheet.
export interface Selection {
  number: number;
  occurrence: number | null;
}
```

`src/lib/answer/layout.ts`:

```ts
import type { Segment } from '../segments';

export type Piece = Segment & { sentence: number; index: number };

export interface AnswerLayout {
  lead: Piece[] | null;
  paragraphs: Piece[][];
}

// A run of newlines is one boundary, so a paragraph break doesn't count as
// two sentences.
const BOUNDARY = /[.!?]+(?=\s|$)|\n+/g;

/** Tag every segment with the sentence it belongs to, splitting text
 * segments at sentence boundaries. */
export function assignSentences(segments: Segment[]): Piece[] {
  const pieces: Piece[] = [];
  let sentence = 0;
  // Nothing but whitespace since the last boundary: a marker here belongs
  // to the sentence that just ended ("…excess. [5]").
  let fresh = true;

  const pushText = (text: string, s: number) => {
    if (!text) return;
    pieces.push({ kind: 'text', text, sentence: s, index: pieces.length });
    if (text.trim()) fresh = false;
  };

  for (const seg of segments) {
    if (seg.kind === 'cite') {
      const s = fresh && sentence > 0 ? sentence - 1 : sentence;
      pieces.push({ ...seg, sentence: s, index: pieces.length });
      continue;
    }
    if (seg.kind === 'rule') {
      pieces.push({ ...seg, sentence, index: pieces.length });
      fresh = false;
      continue;
    }
    let cursor = 0;
    for (const match of seg.text.matchAll(BOUNDARY)) {
      const end = (match.index ?? 0) + match[0].length;
      pushText(seg.text.slice(cursor, end), sentence);
      sentence += 1;
      fresh = true;
      cursor = end;
    }
    pushText(seg.text.slice(cursor), sentence);
  }
  return pieces;
}

const plainText = (list: Piece[]) => list.map((p) => (p.kind === 'text' ? p.text : '')).join('');

const hasContent = (list: Piece[]) => list.some((p) => p.kind !== 'text' || p.text.trim() !== '');

function trimEdges(list: Piece[]): Piece[] {
  const out = list.filter((p, i) => !(p.kind === 'text' && !p.text.trim() && (i === 0 || i === list.length - 1)));
  const first = out[0];
  if (first?.kind === 'text') out[0] = { ...first, text: first.text.trimStart() };
  const last = out[out.length - 1];
  if (last?.kind === 'text') out[out.length - 1] = { ...last, text: last.text.trimEnd() };
  return out;
}

function splitParagraphs(list: Piece[]): Piece[][] {
  const paragraphs: Piece[][] = [[]];
  for (const piece of list) {
    if (piece.kind !== 'text' || !/\n{2,}/.test(piece.text)) {
      paragraphs[paragraphs.length - 1].push(piece);
      continue;
    }
    piece.text.split(/\n{2,}/).forEach((part, i) => {
      if (i > 0) paragraphs.push([]);
      if (part) paragraphs[paragraphs.length - 1].push({ ...piece, text: part });
    });
  }
  return paragraphs.map(trimEdges).filter(hasContent);
}

/** The first sentence becomes the summary line when it is short, ends like
 * a sentence, and more text follows; the rest is split into paragraphs. */
export function layoutAnswer(pieces: Piece[], maxLeadChars = 140): AnswerLayout {
  const lead = pieces.filter((p) => p.sentence === 0);
  const rest = pieces.filter((p) => p.sentence !== 0);
  const leadText = plainText(lead).trim();
  const ok =
    /[.!?]$/.test(leadText) &&
    leadText.length <= maxLeadChars &&
    !leadText.includes('\n') &&
    hasContent(rest);
  return ok
    ? { lead: trimEdges(lead), paragraphs: splitParagraphs(rest) }
    : { lead: null, paragraphs: splitParagraphs(pieces) };
}
```

Paragraph pieces made by splitting a text piece share that piece's `index`. The highlight goes by `sentence`, not `index`, so that's fine. Key `{#each}` blocks by position, not by `index`.

- [ ] **Step 4: Run tests**

Run: `npm test`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mtg-web/src/lib/answer mtg-web/src/lib/selection.ts
git commit -m "feat: answer layout with sentence tracking and summary line"
```

---

### Task 3: Meta state, rule previews and dev fixtures

**Files:**
- Create: `mtg-web/src/lib/meta.svelte.ts`, `mtg-web/src/lib/rulePreview.ts`, `mtg-web/src/lib/rulePreview.test.ts`, `mtg-web/src/lib/fixtures/index.ts`, `mtg-web/src/lib/fixtures/fixtures.test.ts`

**Interfaces:**
- Consumes: `fetchMeta`, `fetchRule`, `MAX_QUERY_CHARS`, `RateLimitedError`, `QueryResponse` from `$lib/api`.
- Produces:

```ts
// meta.svelte.ts
export const meta: Meta;           // $state, filled once by loadMeta()
export function loadMeta(): void;
// rulePreview.ts
export interface RulePreview { title: string; text: string }
export function previewRule(ruleId: string): Promise<RulePreview | null>;
// fixtures/index.ts
export const FIXTURE_NAMES: readonly string[];
export function mockQuery(name: string): Promise<QueryResponse>;
```

- [ ] **Step 1: Write the failing tests**

`src/lib/rulePreview.test.ts`:

```ts
import { beforeEach, describe, expect, it, vi } from 'vitest';

const fetchRule = vi.fn();
vi.mock('./api', () => ({ fetchRule: (id: string) => fetchRule(id) }));

const { previewRule } = await import('./rulePreview');

describe('previewRule', () => {
  beforeEach(() => fetchRule.mockReset());

  it('titles a rule with its heading and caches it', async () => {
    fetchRule.mockResolvedValue({ rule_id: '702.2c', text: 'Lethal.', heading: 'Deathtouch' });
    expect(await previewRule('702.2c')).toEqual({ title: '702.2c · Deathtouch', text: 'Lethal.' });
    await previewRule('702.2c');
    expect(fetchRule).toHaveBeenCalledTimes(1);
  });

  it('returns null on failure and retries next time', async () => {
    fetchRule.mockRejectedValueOnce(new Error('boom'));
    expect(await previewRule('999.1')).toBeNull();
    fetchRule.mockResolvedValue({ rule_id: '999.1', text: 'T.', heading: null });
    expect(await previewRule('999.1')).toEqual({ title: 'Rule 999.1', text: 'T.' });
  });
});
```

`src/lib/fixtures/fixtures.test.ts`:

```ts
import { describe, expect, it } from 'vitest';
import { RateLimitedError } from '../api';
import { mockQuery } from './index';

describe('fixtures', () => {
  it('answered cites five sources and retrieves six more', async () => {
    const r = await mockQuery('answered');
    expect(r.citations.map((c) => c.number)).toEqual([1, 2, 3, 4, 5]);
    expect(r.results.filter((x) => !x.cited)).toHaveLength(6);
  });

  it.each([
    ['uncited', null],
    ['quota', 'ip_quota'],
    ['breather', 'ip_quota'],
    ['paused', 'global_budget']
  ])('%s degrades as %s', async (name, degraded) => {
    expect((await mockQuery(name)).degraded).toBe(degraded);
  });

  it('error and ratelimited reject', async () => {
    await expect(mockQuery('error')).rejects.toThrow('query failed: 502');
    await expect(mockQuery('ratelimited')).rejects.toBeInstanceOf(RateLimitedError);
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `npm test`
Expected: FAIL (modules missing).

- [ ] **Step 3: Implement `meta.svelte.ts` and `rulePreview.ts`**

```ts
// meta.svelte.ts
import { fetchMeta, MAX_QUERY_CHARS, type Meta } from './api';

// Public facts the page states. Defaults hold until (or if never) the API answers.
export const meta = $state<Meta>({
  answers_per_day: null,
  max_query_chars: MAX_QUERY_CHARS,
  rules_as_of: null
});

let started = false;

export function loadMeta(): void {
  if (started) return;
  started = true;
  fetchMeta()
    .then((m) => Object.assign(meta, m))
    .catch(() => {
      started = false; // try again on the next page visit
    });
}
```

```ts
// rulePreview.ts
import { fetchRule } from './api';

export interface RulePreview {
  title: string;
  text: string;
}

// One request per rule per page load; failures are forgotten so a later
// hover can retry.
const cache = new Map<string, Promise<RulePreview | null>>();

export function previewRule(ruleId: string): Promise<RulePreview | null> {
  let preview = cache.get(ruleId);
  if (!preview) {
    preview = fetchRule(ruleId).then(
      (r) => ({ title: r.heading ? `${r.rule_id} · ${r.heading}` : `Rule ${r.rule_id}`, text: r.text }),
      () => {
        cache.delete(ruleId);
        return null;
      }
    );
    cache.set(ruleId, preview);
  }
  return preview;
}
```

- [ ] **Step 4: Implement `fixtures/index.ts`**

Content is the design's worked example (trample + deathtouch). Card images are `null` so placeholders render; real images are checked against the live API in Task 9.

```ts
import { RateLimitedError, type Citation, type QueryResponse, type QueryResult } from '../api';

export const FIXTURE_NAMES = [
  'answered', 'uncited', 'quota', 'breather', 'paused', 'error', 'slow', 'ratelimited'
] as const;

const card = (name: string, type_line: string, mana_cost: string, pt: [string, string] | null) => ({
  name, type_line, mana_cost,
  power: pt?.[0] ?? null, toughness: pt?.[1] ?? null, loyalty: null,
  image_small: null, image_normal: null
});

const COLLAR = card('Basilisk Collar', 'Artifact — Equipment', '{1}', null);
const DREADMAW = card('Colossal Dreadmaw', 'Creature — Dinosaur', '{4}{G}{G}', ['6', '6']);
const SLICE = card('Windswift Slice', 'Instant', '{2}{G}', null);
const SCRY = 'https://scryfall.com/card/';

const CITATIONS: Citation[] = [
  { number: 1, source_type: 'rule', title: 'Rule 702.2c', rule_id: '702.2c', card_name: null, oracle_id: null,
    text: 'Any nonzero amount of combat damage assigned to a creature by a source with deathtouch is considered to be lethal damage for the purposes of determining if a proposed combat damage assignment is valid, regardless of that creature’s toughness.',
    url: '/rules/702.2c', published_at: null, heading: 'Deathtouch', card: null },
  { number: 2, source_type: 'rule', title: 'Rule 702.19b', rule_id: '702.19b', card_name: null, oracle_id: null,
    text: 'The controller of an attacking creature with trample first assigns damage to the creature(s) blocking it. Once all those blocking creatures are assigned lethal damage, any excess damage is assigned as its controller chooses among those blocking creatures and the player, planeswalker, or battle the creature is attacking.',
    url: '/rules/702.19b', published_at: null, heading: 'Trample', card: null },
  { number: 3, source_type: 'card', title: 'Card — Basilisk Collar', rule_id: null, card_name: 'Basilisk Collar', oracle_id: 'o-collar',
    text: 'Equipped creature has deathtouch and lifelink.\nEquip {2}', url: `${SCRY}basilisk-collar`, published_at: null, heading: null, card: COLLAR },
  { number: 4, source_type: 'card', title: 'Card — Colossal Dreadmaw', rule_id: null, card_name: 'Colossal Dreadmaw', oracle_id: 'o-dreadmaw',
    text: 'Trample', url: `${SCRY}colossal-dreadmaw`, published_at: null, heading: null, card: DREADMAW },
  { number: 5, source_type: 'ruling', title: 'Ruling — Windswift Slice (2023-06-16)', rule_id: null, card_name: 'Windswift Slice', oracle_id: 'o-slice',
    text: 'Even 1 damage dealt to a creature from a source with deathtouch is considered lethal damage, so any amount greater than that will cause excess damage to be dealt, even if the total amount of damage isn’t greater than the creature’s toughness.',
    url: `${SCRY}windswift-slice`, published_at: '2023-06-16', heading: null, card: SLICE }
];

const cited = (c: Citation): QueryResult => ({
  source: c.source_type === 'card' ? 'card' : c.source_type, title: c.rule_id ?? c.card_name ?? c.title,
  text: c.text, score: 1, match_type: 'fixture', oracle_id: c.oracle_id, rule_id: c.rule_id,
  card_name: c.card_name, published_at: c.published_at, scryfall_uri: c.url?.startsWith('http') ? c.url : null,
  cited: true, card: c.card, heading: c.heading
});

const rule = (rule_id: string, heading: string, text: string): QueryResult => ({
  source: 'rule', title: rule_id, text, score: 0.7, match_type: 'fixture', rule_id, heading, cited: false
});

const OTHERS: QueryResult[] = [
  rule('510.1c', 'Combat Damage Step', 'A blocked creature assigns its combat damage to the creatures blocking it.'),
  rule('510.1a', 'Combat Damage Step', 'Each attacking creature and each blocking creature assigns combat damage equal to its power.'),
  rule('702.19c', 'Trample', 'Assigning lethal damage to a blocker with trample considers damage already dealt this turn.'),
  rule('702.2b', 'Deathtouch', 'A creature with toughness greater than 0 that’s been dealt damage by a source with deathtouch since the last time state-based actions were checked is destroyed.'),
  { source: 'ruling', title: 'Mirror Shield', text: 'Unless the equipped creature has trample, it won’t deal combat damage to the player or planeswalker it’s attacking.',
    score: 0.6, match_type: 'fixture', card_name: 'Mirror Shield', published_at: '2020-01-24', scryfall_uri: `${SCRY}mirror-shield`, cited: false,
    card: card('Mirror Shield', 'Artifact — Equipment', '{3}', null) },
  { source: 'oracle', title: 'Ohran Frostfang', text: 'Attacking creatures you control have deathtouch.', score: 0.55, match_type: 'fixture',
    card_name: 'Ohran Frostfang', scryfall_uri: `${SCRY}ohran-frostfang`, cited: false,
    card: card('Ohran Frostfang', 'Creature — Snake', '{3}{G}{G}', ['2', '6']) }
];

const ANSWER =
  'Yes — each blocker only needs 1 damage. Deathtouch makes any nonzero amount of combat damage count as lethal when assigning it [1]. ' +
  'Trample only requires lethal damage on each blocker before the excess can be assigned to the player [2]. ' +
  'A published ruling confirms the reading: with deathtouch, anything beyond 1 damage to a creature is excess damage [5].\n\n' +
  'A Colossal Dreadmaw [4] equipped with Basilisk Collar [3] and blocked by two creatures can assign 1 to each blocker and 4 to the defending player. ' +
  'See 510.1c for how assignment works.';

const BASE: QueryResponse = {
  query: 'Does trample plus deathtouch only need 1 damage on each blocker?',
  results: [...CITATIONS.map(cited), ...OTHERS],
  answer: ANSWER,
  citations: CITATIONS,
  rule_references: ['510.1c', '702.2c', '702.19b'],
  citation_stats: { cited_count: 5, invalid_count: 0, uncited_answer: false },
  cached_at: '2026-09-27T18:04:00Z',
  degraded: null,
  answers_remaining: 7
};

const uncitedResults = BASE.results.map((r) => ({ ...r, cited: false }));
const retrievalOnly = { ...BASE, answer: null, citations: [], rule_references: [], results: uncitedResults, cached_at: null };

const FIXTURES: Record<string, () => QueryResponse> = {
  answered: () => BASE,
  uncited: () => ({
    ...BASE,
    query: 'What happens when two replacement effects apply to the same event?',
    answer: 'The affected player or controller chooses one to apply first, then checks whether the other still applies. Repeat until none are left.',
    citations: [], rule_references: [], results: uncitedResults,
    citation_stats: { cited_count: 0, invalid_count: 0, uncited_answer: true },
    cached_at: null, answers_remaining: 12
  }),
  quota: () => ({ ...retrievalOnly, degraded: 'ip_quota', answers_remaining: 0 }),
  breather: () => ({ ...retrievalOnly, degraded: 'ip_quota', answers_remaining: 12 }),
  paused: () => ({ ...retrievalOnly, degraded: 'global_budget', answers_remaining: 0 })
};

export function mockQuery(name: string): Promise<QueryResponse> {
  if (name === 'slow') return new Promise(() => {});
  if (name === 'error') return Promise.reject(new Error('query failed: 502'));
  if (name === 'ratelimited')
    return Promise.reject(new RateLimitedError('Too many requests. Wait a few seconds and try again.'));
  const make = FIXTURES[name] ?? FIXTURES.answered;
  // A short delay so the loading state is visible between states.
  return new Promise((resolve) => setTimeout(() => resolve(structuredClone(make())), 400));
}
```

Run Prettier over the file afterwards (`npx prettier --write src/lib/fixtures/index.ts` if Prettier is available; otherwise wrap long lines by hand to ≤ 100 columns).

- [ ] **Step 5: Run tests and typecheck**

Run: `npm test && npm run check`
Expected: PASS. (If `check` flags `QueryResult` literals missing optional fields, the fixture is wrong, not the type: add the field.)

- [ ] **Step 6: Commit**

```bash
git add mtg-web/src/lib/meta.svelte.ts mtg-web/src/lib/rulePreview.ts mtg-web/src/lib/rulePreview.test.ts mtg-web/src/lib/fixtures
git commit -m "feat: meta state, cached rule previews and dev fixtures"
```

---

### Task 4: Primitives: icons, popover, search form

**Files:**
- Create: `mtg-web/src/lib/desk/Icon.svelte`, `mtg-web/src/lib/desk/Popover.svelte`, `mtg-web/src/lib/desk/SearchForm.svelte`

**Interfaces:**
- Produces:
  - `<Icon name size? class? />`, where `name` is one of `'clock' | 'pause' | 'warning' | 'info' | 'chevron-left' | 'chevron-right' | 'close' | 'spinner'`.
  - `<Popover id open title text />`: absolutely positioned above its `relative` parent, rendered only when `open`.
  - `<SearchForm bind:value loading? retrievalOnly? variant?: 'header' | 'hero' maxChars error? onsubmit />`. The button reads "Searching…" while loading, "Search" when `retrievalOnly`, else "Ask". It shows the `Q` label on sm+ and a visually hidden "Rules question" label on phone, an `Enter` hint on sm+, a `{len} / {max}` counter at 100 characters from the limit, and a `role="alert"` error linked with `aria-describedby`. The hero variant on phone uses a 3-row textarea where Enter submits.

- [ ] **Step 1: `Icon.svelte`**

```svelte
<script lang="ts">
  import type { ClassValue } from 'svelte/elements';

  type Name =
    | 'clock' | 'pause' | 'warning' | 'info' | 'chevron-left' | 'chevron-right' | 'close' | 'spinner';

  let { name, size = 18, class: cls = '' }: { name: Name; size?: number; class?: ClassValue } =
    $props();
</script>

<svg
  width={size}
  height={size}
  viewBox="0 0 24 24"
  fill="none"
  stroke="currentColor"
  stroke-width="1.8"
  stroke-linecap="round"
  stroke-linejoin="round"
  aria-hidden="true"
  class={cls}
>
  {#if name === 'clock'}<circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" />
  {:else if name === 'pause'}<circle cx="12" cy="12" r="9" /><path d="M10 9v6M14 9v6" />
  {:else if name === 'warning'}<path d="M12 3l9.5 17h-19z" /><path d="M12 10v4M12 17.5v.01" />
  {:else if name === 'info'}<circle cx="12" cy="12" r="9" /><path d="M12 8v5M12 16.5v.01" />
  {:else if name === 'chevron-left'}<path d="M15 6l-6 6 6 6" />
  {:else if name === 'chevron-right'}<path d="M9 6l6 6-6 6" />
  {:else if name === 'close'}<path d="M6 6l12 12M18 6L6 18" />
  {:else if name === 'spinner'}<path d="M12 3a9 9 0 1 0 9 9" />
  {/if}
</svg>
```

- [ ] **Step 2: `Popover.svelte`**

```svelte
<script lang="ts">
  let { id, open, title, text }: { id: string; open: boolean; title: string; text: string } =
    $props();
</script>

{#if open}
  <span
    role="tooltip"
    {id}
    class="absolute bottom-[calc(100%+6px)] left-0 z-20 block w-[min(28rem,80vw)] rounded-lg border border-line-strong bg-card px-3.5 py-2.5 text-left font-sans text-sm leading-snug font-normal whitespace-normal text-fg shadow-[0_8px_24px_rgb(0_0_0/0.4)]"
  >
    <span class="block font-mono text-xs text-fg-muted">{title}</span>
    <span class="mt-1 line-clamp-4 block text-fg-body">{text}</span>
  </span>
{/if}
```

- [ ] **Step 3: `SearchForm.svelte`**

```svelte
<script lang="ts">
  import { MediaQuery } from 'svelte/reactivity';

  let {
    value = $bindable(''),
    loading = false,
    retrievalOnly = false,
    variant = 'header',
    maxChars,
    error = '',
    onsubmit
  }: {
    value?: string;
    loading?: boolean;
    retrievalOnly?: boolean;
    variant?: 'header' | 'hero';
    maxChars: number;
    error?: string;
    onsubmit: () => void;
  } = $props();

  const phone = new MediaQuery('max-width: 639px');
  const uid = $props.id();
  const label = $derived(loading ? 'Searching…' : retrievalOnly ? 'Search' : 'Ask');
  const placeholder = $derived(
    variant === 'hero' ? 'e.g. Can I respond to a spell with split second?' : 'Ask a rules question'
  );

  function submit(event: SubmitEvent) {
    event.preventDefault();
    if (!loading && value.trim()) onsubmit();
  }

  function onTextareaKey(event: KeyboardEvent) {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      (event.currentTarget as HTMLTextAreaElement).form?.requestSubmit();
    }
  }
</script>

<form class="flex flex-col gap-1.5" onsubmit={submit}>
  <div
    class={[
      'flex gap-3 rounded-[10px] border bg-field',
      error ? 'border-danger-line' : 'border-line-strong',
      variant === 'hero' && phone.current ? 'flex-col items-stretch p-3' : 'items-center',
      variant === 'hero' && !phone.current && 'py-2 pr-2 pl-5',
      variant === 'header' && 'py-1 pr-1 pl-3 sm:py-1.5 sm:pr-1.5 sm:pl-4'
    ]}
  >
    <label for="q-{uid}" class={phone.current ? 'sr-only' : 'font-mono text-[13px] text-fg-muted'}>
      {phone.current ? 'Rules question' : 'Q'}
    </label>
    {#if variant === 'hero' && phone.current}
      <textarea
        id="q-{uid}"
        rows="3"
        bind:value
        maxlength={maxChars}
        {placeholder}
        aria-describedby={error ? `qe-${uid}` : undefined}
        onkeydown={onTextareaKey}
        class="min-w-0 resize-none border-0 bg-transparent font-sans text-base text-fg outline-none placeholder:text-fg-muted"
      ></textarea>
    {:else}
      <input
        id="q-{uid}"
        type="text"
        bind:value
        maxlength={maxChars}
        {placeholder}
        aria-describedby={error ? `qe-${uid}` : undefined}
        class="min-h-9 min-w-0 flex-1 border-0 bg-transparent font-sans text-base text-fg outline-none placeholder:text-fg-muted"
      />
    {/if}
    {#if !phone.current}
      <kbd
        class="rounded border border-line-strong px-1.5 py-0.5 font-mono text-xs text-fg-muted"
        aria-hidden="true">Enter</kbd
      >
    {/if}
    <button
      type="submit"
      disabled={loading}
      class="min-h-11 min-w-14 cursor-pointer rounded-[7px] border-0 bg-gold px-[18px] font-sans text-[15px] font-semibold text-gold-ink disabled:cursor-default disabled:bg-gold-off disabled:text-gold-off-fg sm:min-h-10 sm:text-sm"
      >{label}</button
    >
  </div>
  {#if error}
    <p id="qe-{uid}" role="alert" class="m-0 text-sm text-danger">{error}</p>
  {/if}
  {#if value.length >= maxChars - 100}
    <p class="m-0 text-right font-mono text-xs text-fg-muted">{value.length} / {maxChars}</p>
  {/if}
</form>
```

- [ ] **Step 4: Typecheck**

Run: `npm run check`
Expected: 0 errors. (If `$props.id()` is reported as unknown, the installed Svelte is older than 5.20: `npm install -D svelte@5` and re-run.)

- [ ] **Step 5: Commit**

```bash
git add mtg-web/src/lib/desk/Icon.svelte mtg-web/src/lib/desk/Popover.svelte mtg-web/src/lib/desk/SearchForm.svelte
git commit -m "feat: icon, popover and search form components"
```

---

### Task 5: The answer column

**Files:**
- Create: `mtg-web/src/lib/desk/StatusChips.svelte`, `CitationMarker.svelte`, `RuleLink.svelte`, `AnswerBody.svelte`, `RulesReferenced.svelte`

**Interfaces:**
- Consumes: Tasks 1–4.
- Produces:
  - `<StatusChips response isAdmin loading compact onfresh />`.
  - `<CitationMarker citation occurrence active canHover onselect />`, where `onselect: (number: number, occurrence: number) => void`.
  - `<RuleLink ruleId key canHover />`.
  - `<AnswerBody answer citations ruleReferences selection canHover onselect />`, where `selection: Selection | null`.
  - `<RulesReferenced ruleIds boxed />`.

- [ ] **Step 1: `StatusChips.svelte`**

```svelte
<script lang="ts">
  import type { QueryResponse } from '$lib/api';
  import { shortDate } from '$lib/format';
  import { answersLeftLabel, isRunningLow } from '$lib/status';

  let {
    response,
    isAdmin,
    loading,
    compact,
    onfresh
  }: {
    response: QueryResponse;
    isAdmin: boolean;
    loading: boolean;
    compact: boolean;
    onfresh: () => void;
  } = $props();

  const count = $derived(response.citations.length);
  const remaining = $derived(response.answers_remaining);
</script>

<div class="flex flex-wrap items-center gap-2 font-mono text-[11px] text-fg-muted sm:gap-2.5 sm:text-xs">
  {#if response.citation_stats.uncited_answer}
    <span class="rounded bg-caution-chip px-2 py-[3px] text-caution">No sources cited</span>
  {:else}
    <span class="rounded bg-teal-chip px-2 py-[3px] text-teal">
      {count} {compact ? 'cited' : `source${count === 1 ? '' : 's'} cited`}
    </span>
  {/if}
  {#if response.cached_at}
    <span class="rounded bg-chip px-2 py-[3px]">
      cached · {isAdmin ? 'first generated ' : ''}{shortDate(response.cached_at)}
    </span>
    {#if isAdmin}
      <button
        type="button"
        onclick={onfresh}
        disabled={loading}
        class="min-h-9 cursor-pointer rounded-[7px] border border-line-strong bg-transparent px-3 font-sans text-[13px] text-gold disabled:text-fg-disabled"
        >Get a fresh answer</button
      >
    {/if}
  {/if}
  {#if remaining !== null}
    {#if isRunningLow(remaining)}
      <span class="rounded bg-notice px-2 py-[3px] text-notice-fg">
        {answersLeftLabel(remaining, compact)}
      </span>
    {:else}
      <span>{answersLeftLabel(remaining, compact)}</span>
    {/if}
  {/if}
</div>
```

- [ ] **Step 2: `CitationMarker.svelte`**

```svelte
<script lang="ts">
  import type { Citation } from '$lib/api';
  import { sourceKind } from '$lib/evidence';
  import Popover from './Popover.svelte';

  let {
    citation,
    occurrence,
    active,
    canHover,
    onselect
  }: {
    citation: Citation;
    occurrence: number;
    active: boolean;
    canHover: boolean;
    onselect: (number: number, occurrence: number) => void;
  } = $props();

  let open = $state(false);
  const popId = $derived(`cite-pop-${occurrence}-${citation.number}`);
  const ruling = $derived(sourceKind(citation.source_type) === 'ruling');
  const title = $derived(
    citation.rule_id && citation.heading ? `${citation.rule_id} · ${citation.heading}` : citation.title
  );

  function onclick(event: MouseEvent) {
    event.preventDefault();
    open = false;
    onselect(citation.number, occurrence);
  }
</script>

<span class="relative inline-block"
  ><a
    href="#source-{citation.number}"
    aria-current={active ? 'true' : undefined}
    aria-describedby={canHover ? popId : undefined}
    {onclick}
    onmouseenter={() => canHover && (open = true)}
    onmouseleave={() => (open = false)}
    onfocus={() => canHover && (open = true)}
    onblur={() => (open = false)}
    onkeydown={(e) => e.key === 'Escape' && (open = false)}
    class={[
      'inline-flex items-center rounded-[5px] border px-[5px] py-px font-mono text-xs leading-none no-underline max-sm:min-h-8 max-sm:px-2 max-sm:text-[13px]',
      active && ruling && 'border-teal bg-teal text-gold-ink hover:text-gold-ink',
      active && !ruling && 'border-gold bg-gold text-gold-ink hover:text-gold-ink',
      !active && 'border-line-muted text-fg-body hover:text-fg'
    ]}>{citation.number}</a
  >{#if canHover}<Popover id={popId} {open} {title} text={citation.text} />{/if}</span
>
```

- [ ] **Step 3: `RuleLink.svelte`**

```svelte
<script lang="ts">
  import { previewRule, type RulePreview } from '$lib/rulePreview';
  import Popover from './Popover.svelte';

  let { ruleId, key, canHover }: { ruleId: string; key: string; canHover: boolean } = $props();

  let open = $state(false);
  let preview = $state<RulePreview | null>(null);

  async function show() {
    if (!canHover) return;
    open = true;
    preview ??= await previewRule(ruleId);
  }
</script>

<span class="relative inline-block"
  ><a
    href="/rules/{ruleId}"
    class="font-mono text-[0.9em]"
    aria-describedby={canHover && preview ? `rule-pop-${key}` : undefined}
    onmouseenter={show}
    onmouseleave={() => (open = false)}
    onfocus={show}
    onblur={() => (open = false)}
    onkeydown={(e) => e.key === 'Escape' && (open = false)}>{ruleId}</a
  >{#if preview}<Popover id="rule-pop-{key}" {open} title={preview.title} text={preview.text} />{/if}</span
>
```

- [ ] **Step 4: `AnswerBody.svelte`**

```svelte
<script lang="ts">
  import type { Citation } from '$lib/api';
  import { assignSentences, layoutAnswer, type Piece } from '$lib/answer/layout';
  import { sourceKind } from '$lib/evidence';
  import { segmentAnswer } from '$lib/segments';
  import type { Selection } from '$lib/selection';
  import CitationMarker from './CitationMarker.svelte';
  import RuleLink from './RuleLink.svelte';

  let {
    answer,
    citations,
    ruleReferences,
    selection,
    canHover,
    onselect
  }: {
    answer: string;
    citations: Citation[];
    ruleReferences: string[];
    selection: Selection | null;
    canHover: boolean;
    onselect: (number: number, occurrence: number) => void;
  } = $props();

  const byNumber = $derived(new Map(citations.map((c) => [c.number, c])));
  const pieces = $derived(
    assignSentences(segmentAnswer(answer, new Set(byNumber.keys()), new Set(ruleReferences)))
  );
  const layout = $derived(layoutAnswer(pieces));

  // The sentence to highlight: the one the clicked marker closes, or the
  // first sentence citing the selected source.
  const marked = $derived.by(() => {
    if (!selection) return null;
    const { number, occurrence } = selection;
    const piece =
      occurrence !== null
        ? pieces[occurrence]
        : pieces.find((p) => p.kind === 'cite' && p.numbers.includes(number));
    return piece ? piece.sentence : null;
  });
  const markRuling = $derived(
    selection ? sourceKind(byNumber.get(selection.number)?.source_type ?? '') === 'ruling' : false
  );

  const isActive = (n: number, index: number) =>
    selection?.number === n && (selection.occurrence === null || selection.occurrence === index);
</script>

<!-- Every piece of model output is rendered as text; nothing uses {@html}. -->
{#snippet run(list: Piece[])}{#each list as p, i (i)}{#if p.kind === 'text'}{#if p.sentence === marked && p.text.trim()}{@const lead = p.text.length - p.text.trimStart().length}{p.text.slice(0, lead)}<mark
          class={[
            'rounded-[3px] px-[3px]',
            markRuling ? 'bg-teal-mark text-teal-mark-fg' : 'bg-gold-mark text-gold-hover'
          ]}>{p.text.slice(lead)}</mark
        >{:else}{p.text}{/if}{:else if p.kind === 'rule'}<RuleLink
        ruleId={p.ruleId}
        key={String(p.index)}
        {canHover}
      />{:else}{#each p.numbers as n, j (n)}{@const c = byNumber.get(n)}{#if c}{#if j}&nbsp;{/if}<CitationMarker
            citation={c}
            occurrence={p.index}
            active={isActive(n, p.index)}
            {canHover}
            {onselect}
          />{/if}{/each}{/if}{/each}{/snippet}

{#if layout.lead}
  <p class="m-0 text-[21px] leading-[1.35] font-medium text-fg sm:text-2xl desk:text-[26px] desk:leading-[1.4]">
    {@render run(layout.lead)}
  </p>
{/if}
{#each layout.paragraphs as paragraph, i (i)}
  <p class="m-0 text-base leading-[1.75] whitespace-pre-wrap text-fg-body sm:text-[17px]">{@render run(paragraph)}</p>
{/each}
```

Check the lead `<p>` in the browser: the formatter may add whitespace around `{@render run(...)}`. That's harmless there, since the lead has no `pre-wrap`. The paragraph `<p>` must stay on one line.

- [ ] **Step 5: `RulesReferenced.svelte`**

```svelte
<script lang="ts">
  let { ruleIds, boxed }: { ruleIds: string[]; boxed: boolean } = $props();
</script>

{#if ruleIds.length}
  <div
    class={boxed
      ? 'mt-auto flex flex-col gap-2.5 rounded-[10px] border border-line bg-well px-[18px] py-4'
      : 'flex flex-wrap items-center gap-2'}
  >
    <div
      class={boxed
        ? 'font-mono text-xs tracking-[0.08em] text-fg-muted uppercase'
        : 'mr-1 font-mono text-xs text-fg-muted max-sm:sr-only'}
    >
      Rules referenced
    </div>
    <div class="flex flex-wrap gap-2 font-mono text-[13px]">
      {#each ruleIds as id (id)}
        <a href="/rules/{id}" class="rounded-md bg-chip px-2.5 py-1.5 no-underline max-sm:p-3">{id}</a>
      {/each}
    </div>
  </div>
{/if}
```

- [ ] **Step 6: Typecheck**

Run: `npm run check`
Expected: 0 errors.

- [ ] **Step 7: Commit**

```bash
git add mtg-web/src/lib/desk
git commit -m "feat: answer column with summary line, citation markers and rule previews"
```

---

### Task 6: Evidence cards and panel

**Files:**
- Create: `mtg-web/src/lib/desk/EvidenceCard.svelte`, `mtg-web/src/lib/desk/EvidencePanel.svelte`

**Interfaces:**
- Consumes: `EvidenceItem`, `fromCitation`, `uncitedItems`, `statLine`, `cardSummary`, `calendarDate`, `Icon`.
- Produces:
  - `<EvidenceCard item active? compact? onopen? />`. Full cards have the DOM id `source-{n}` for citations and `evidence-{key}` otherwise. Compact cards are `<button>`s that call `onopen`.
  - `<EvidencePanel citations results selectedNumber layout onopen />`, where `layout: 'side' | 'grid' | 'list'` and `onopen: (items: EvidenceItem[], index: number) => void`.

- [ ] **Step 1: `EvidenceCard.svelte`**

```svelte
<script lang="ts">
  import { cardSummary, statLine, type EvidenceItem } from '$lib/evidence';
  import { calendarDate } from '$lib/format';
  import Icon from './Icon.svelte';

  let {
    item,
    active = false,
    compact = false,
    onopen
  }: { item: EvidenceItem; active?: boolean; compact?: boolean; onopen?: () => void } = $props();

  const label = $derived(`${item.number !== null ? `${item.number} · ` : ''}${item.kind.toUpperCase()}`);
  const domId = $derived(item.number !== null ? `source-${item.number}` : `evidence-${item.key}`);
  const name = $derived(item.card?.name ?? item.cardName ?? item.title);
  const external = { target: '_blank', rel: 'noopener noreferrer' };
</script>

{#snippet art(w: number, h: number, big: boolean)}
  {#if (big ? item.card?.image_normal : item.card?.image_small)}
    <img
      src={big ? item.card?.image_normal : item.card?.image_small}
      alt={name}
      width={w}
      height={h}
      loading="lazy"
      decoding="async"
      class="shrink-0 rounded-md"
      style:width="{w}px"
      style:height="{h}px"
    />
  {:else}
    <span
      aria-hidden="true"
      class="flex shrink-0 items-center justify-center rounded-md border border-line-muted bg-art font-mono text-[10px] text-fg-muted"
      style:width="{w}px"
      style:height="{h}px">art</span
    >
  {/if}
{/snippet}

{#if compact}
  <button
    type="button"
    id={domId}
    onclick={onopen}
    class={[
      'flex w-full cursor-pointer gap-3 rounded-[10px] border text-left font-sans text-fg',
      item.kind === 'ruling' ? 'border-teal-line bg-teal-wash' : 'border-line bg-card',
      item.kind === 'card' ? 'items-center p-2.5' : 'flex-col gap-1 px-3.5 py-3'
    ]}
  >
    {#if item.kind === 'card'}
      {@render art(52, 72, false)}
      <span class="flex min-w-0 flex-1 flex-col gap-[3px]">
        <span class="font-mono text-xs text-fg-muted">{label}</span>
        <span class="text-[15px] font-semibold">{name}</span>
        <span class="truncate text-[13px] text-fg-muted">{cardSummary(item)}</span>
      </span>
      <Icon name="chevron-right" class="shrink-0 text-fg-muted" />
    {:else if item.kind === 'ruling'}
      <span class="flex justify-between font-mono text-xs text-fg-muted">
        <span class="text-teal">{label}</span>
        {#if item.publishedAt}<span>{calendarDate(item.publishedAt)}</span>{/if}
      </span>
      <span class="text-[13px] text-fg-muted">on {item.cardName}</span>
      <span class="line-clamp-2 text-sm leading-normal text-fg-body italic">“{item.text}”</span>
    {:else}
      <span class="flex justify-between font-mono text-xs text-fg-muted">
        <span>{label}</span><span class="text-gold">{item.ruleId}</span>
      </span>
      <span class="line-clamp-2 text-sm leading-normal text-fg-body">{item.text}</span>
    {/if}
  </button>
{:else if item.kind === 'card'}
  <article
    id={domId}
    class={['flex scroll-mt-4 gap-3.5 rounded-[10px] border p-3.5', active ? 'border-gold bg-gold-wash' : 'border-line bg-card']}
  >
    {@render art(96, 134, false)}
    <div class="flex min-w-0 flex-col gap-1.5">
      <div class={['font-mono text-xs', active ? 'text-gold' : 'text-fg-muted']}>{label}</div>
      <div class="text-base font-semibold">
        {name}
        {#if item.card?.mana_cost}<span class="font-mono text-xs font-normal text-fg-muted">{item.card.mana_cost}</span>{/if}
      </div>
      {#if item.card}<div class="text-[13px] text-fg-muted">{statLine(item.card)}</div>{/if}
      <p class="m-0 text-sm leading-normal whitespace-pre-line text-fg-body">{item.text}</p>
      {#if item.url}<a href={item.url} {...external} class="text-[13px]">Scryfall ↗</a>{/if}
    </div>
  </article>
{:else if item.kind === 'ruling'}
  <article
    id={domId}
    class={['flex scroll-mt-4 flex-col gap-2 rounded-[10px] border bg-teal-wash px-4 py-3.5', active ? 'border-teal' : 'border-teal-line']}
  >
    <div class="flex justify-between font-mono text-xs text-fg-muted">
      <span class="text-teal">{label}</span>
      {#if item.publishedAt}<span>{calendarDate(item.publishedAt)}</span>{/if}
    </div>
    <div class="text-[13px] text-fg-muted">
      Ruling on {item.cardName}{#if item.url}&nbsp;· <a href={item.url} {...external}>Scryfall ↗</a>{/if}
    </div>
    <blockquote class="m-0 border-l-2 border-teal pl-3 text-sm leading-[1.55] text-fg-body italic">
      “{item.text}”
    </blockquote>
  </article>
{:else}
  <article
    id={domId}
    class={['flex scroll-mt-4 flex-col gap-1.5 rounded-[10px] border px-4 py-3.5', active ? 'border-gold bg-gold-wash' : 'border-line bg-card']}
  >
    <div class="flex justify-between font-mono text-xs text-fg-muted">
      <span class={active ? 'text-gold' : ''}>{label}</span>
      <a href="/rules/{item.ruleId}">{item.ruleId}</a>
    </div>
    {#if item.heading}<div class="text-sm font-medium text-fg">{item.heading}</div>{/if}
    <p class="m-0 line-clamp-3 text-sm leading-[1.55] text-fg-body">{item.text}</p>
  </article>
{/if}
```

- [ ] **Step 2: `EvidencePanel.svelte`**

```svelte
<script lang="ts">
  import { tick } from 'svelte';
  import { MediaQuery } from 'svelte/reactivity';
  import type { Citation, QueryResult } from '$lib/api';
  import { fromCitation, uncitedItems, type EvidenceItem } from '$lib/evidence';
  import EvidenceCard from './EvidenceCard.svelte';

  let {
    citations,
    results,
    selectedNumber,
    layout,
    onopen
  }: {
    citations: Citation[];
    results: QueryResult[];
    selectedNumber: number | null;
    layout: 'side' | 'grid' | 'list';
    onopen: (items: EvidenceItem[], index: number) => void;
  } = $props();

  const OTHERS_PREVIEW = 3;
  const reduceMotion = new MediaQuery('prefers-reduced-motion: reduce');

  const cited = $derived(citations.map(fromCitation));
  const others = $derived(uncitedItems(results));
  let chosen = $state<'cited' | 'other'>('cited');
  let showAll = $state(false);
  const tab = $derived(cited.length ? chosen : 'other');
  const list = $derived(tab === 'cited' ? cited : showAll ? others : others.slice(0, OTHERS_PREVIEW));
  const hidden = $derived(tab === 'other' && !showAll ? others.length - list.length : 0);

  // Selecting a citation in the answer brings its card into view.
  $effect(() => {
    if (selectedNumber === null || layout === 'list') return;
    chosen = 'cited';
    const id = `source-${selectedNumber}`;
    tick().then(() =>
      document
        .getElementById(id)
        ?.scrollIntoView({ block: 'nearest', behavior: reduceMotion.current ? 'auto' : 'smooth' })
    );
  });

  function onTabKey(event: KeyboardEvent) {
    if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
    if (!cited.length) return;
    chosen = tab === 'cited' ? 'other' : 'cited';
    tick().then(() => document.getElementById(`tab-${chosen}`)?.focus());
  }

  const tabClass = (on: boolean) => [
    'min-h-11 cursor-pointer border-0 border-b-2 bg-transparent px-3.5 font-sans text-sm disabled:cursor-default disabled:text-fg-disabled max-sm:min-h-12 max-sm:flex-1',
    on ? 'border-gold font-semibold text-fg' : 'border-transparent text-fg-muted'
  ];
</script>

<section aria-label="Evidence" class="flex min-h-0 flex-col bg-panel">
  <div
    role="tablist"
    tabindex="-1"
    aria-label="Evidence"
    onkeydown={onTabKey}
    class="flex gap-1 border-b border-line px-5 pt-3.5 max-desk:px-7 max-desk:pt-2.5 max-sm:px-2 max-sm:pt-1"
  >
    <button
      type="button"
      role="tab"
      id="tab-cited"
      aria-controls="evidence-panel"
      aria-selected={tab === 'cited'}
      tabindex={tab === 'cited' ? 0 : -1}
      disabled={!cited.length}
      onclick={() => (chosen = 'cited')}
      class={tabClass(tab === 'cited')}>Cited · {cited.length}</button
    >
    <button
      type="button"
      role="tab"
      id="tab-other"
      aria-controls="evidence-panel"
      aria-selected={tab === 'other'}
      tabindex={tab === 'other' ? 0 : -1}
      onclick={() => (chosen = 'other')}
      class={tabClass(tab === 'other')}
      >{cited.length ? 'Also retrieved' : 'Retrieved'} · {others.length}</button
    >
  </div>
  <div
    role="tabpanel"
    id="evidence-panel"
    aria-labelledby="tab-{tab}"
    class={layout === 'grid'
      ? 'grid grid-cols-2 gap-3.5 px-7 py-6'
      : 'flex flex-col gap-3 p-5 max-sm:gap-2.5 max-sm:p-4'}
  >
    {#each list as item, i (item.key)}
      <div class={layout === 'grid' && item.kind === 'ruling' ? 'col-span-2' : ''}>
        <EvidenceCard
          {item}
          active={item.number !== null && item.number === selectedNumber}
          compact={layout === 'list'}
          onopen={() => onopen(list, i)}
        />
      </div>
    {/each}
    {#if hidden > 0}
      <button
        type="button"
        onclick={() => (showAll = true)}
        class="min-h-11 cursor-pointer rounded-[10px] border border-line-strong bg-transparent font-sans text-sm text-fg-soft"
        >Show {hidden} more</button
      >
    {/if}
  </div>
</section>
```

- [ ] **Step 3: Typecheck**

Run: `npm run check`
Expected: 0 errors. Fix any a11y warning in the markup rather than silencing it. The one exception: if svelte-check objects to the key handler on `role="tablist"` (the ARIA tabs pattern puts arrow-key handling there), add a `<!-- svelte-ignore <code> -->` naming the exact warning code it prints.

- [ ] **Step 4: Commit**

```bash
git add mtg-web/src/lib/desk/EvidenceCard.svelte mtg-web/src/lib/desk/EvidencePanel.svelte
git commit -m "feat: evidence cards and tabbed evidence panel"
```

---

### Task 7: Phone source sheet

**Files:**
- Create: `mtg-web/src/lib/desk/SourceSheet.svelte`

**Interfaces:**
- Consumes: `EvidenceItem`, `statLine`, `parentRule`, `calendarDate`, `Icon`.
- Produces: `<SourceSheet items bind:index bind:open onchange />`, where `onchange: (item: EvidenceItem) => void` fires whenever the shown item changes while open.

- [ ] **Step 1: Implement**

```svelte
<script lang="ts">
  import { parentRule, statLine, type EvidenceItem } from '$lib/evidence';
  import { calendarDate } from '$lib/format';
  import Icon from './Icon.svelte';

  let {
    items,
    index = $bindable(0),
    open = $bindable(false),
    onchange
  }: {
    items: EvidenceItem[];
    index?: number;
    open?: boolean;
    onchange?: (item: EvidenceItem) => void;
  } = $props();

  let dialog: HTMLDialogElement;
  let opener: HTMLElement | null = null;
  const item = $derived(items[index]);
  const external = { target: '_blank', rel: 'noopener noreferrer' };
  const iconButton =
    'flex size-11 cursor-pointer items-center justify-center border-0 bg-transparent text-fg disabled:cursor-default disabled:text-line-muted';
  const outlineButton =
    'flex min-h-12 items-center justify-center rounded-lg border border-line-strong text-sm font-medium no-underline';

  $effect(() => {
    if (open && !dialog.open) {
      opener = document.activeElement as HTMLElement | null;
      dialog.showModal();
    } else if (!open && dialog.open) {
      dialog.close();
    }
  });

  $effect(() => {
    if (open && item) onchange?.(item);
  });

  function onclose() {
    open = false;
    opener?.focus();
  }
</script>

<!-- A click on the backdrop lands on the dialog element itself. -->
<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_noninteractive_element_interactions -->
<dialog
  bind:this={dialog}
  {onclose}
  onclick={(e) => e.target === dialog && (open = false)}
  aria-label={item ? `Source ${item.number ?? index + 1}: ${item.title}` : 'Source'}
  class="mx-0 mt-auto mb-0 max-h-[85vh] w-full max-w-full overflow-y-auto rounded-t-[18px] border-0 border-t border-line-strong bg-card p-0 text-fg backdrop:bg-scrim"
>
  {#if item}
    <div class="flex min-h-[60vh] flex-col gap-3.5 px-[18px] pt-2 pb-6">
      <div class="h-1 w-10 self-center rounded-sm bg-line-muted" aria-hidden="true"></div>
      <div class="flex items-center justify-between">
        <div class="flex items-center gap-1">
          <button type="button" aria-label="Previous source" disabled={index === 0} onclick={() => index--} class={iconButton}>
            <Icon name="chevron-left" size={20} />
          </button>
          <span class="font-mono text-[13px] text-fg-muted">Source {index + 1} of {items.length}</span>
          <button type="button" aria-label="Next source" disabled={index === items.length - 1} onclick={() => index++} class={iconButton}>
            <Icon name="chevron-right" size={20} />
          </button>
        </div>
        <button type="button" aria-label="Close" onclick={() => (open = false)} class="flex size-11 cursor-pointer items-center justify-center rounded-full border-0 bg-chip text-fg">
          <Icon name="close" />
        </button>
      </div>

      {#if item.kind === 'card'}
        <div class="flex gap-4">
          {#if item.card?.image_normal}
            <img src={item.card.image_normal} alt={item.card.name} width="150" height="209" class="h-[209px] w-[150px] shrink-0 rounded-lg" />
          {:else}
            <div aria-hidden="true" class="flex h-[209px] w-[150px] shrink-0 items-center justify-center rounded-lg border border-line-muted bg-art font-mono text-[11px] text-fg-muted">card image</div>
          {/if}
          <div class="flex min-w-0 flex-col gap-1.5">
            <div class="font-mono text-xs text-gold">CARD</div>
            <div class="text-[19px] leading-tight font-semibold">{item.card?.name ?? item.cardName}</div>
            {#if item.card?.mana_cost}<div class="font-mono text-xs text-fg-muted">{item.card.mana_cost}</div>{/if}
            {#if item.card}<div class="text-[13px] text-fg-muted">{statLine(item.card)}</div>{/if}
          </div>
        </div>
        <p class="m-0 rounded-lg bg-panel px-3.5 py-3 text-[15px] leading-[1.6] whitespace-pre-line text-fg-body">{item.text}</p>
        {#if item.url}
          <div class="mt-auto grid grid-cols-2 gap-2.5">
            <a href="{item.url}#rulings" {...external} class={outlineButton}>Rulings</a>
            <a href={item.url} {...external} class={outlineButton}>Scryfall ↗</a>
          </div>
        {/if}
      {:else if item.kind === 'ruling'}
        <div class="flex flex-col gap-1">
          <div class="flex justify-between font-mono text-xs">
            <span class="text-teal">RULING</span>
            {#if item.publishedAt}<span class="text-fg-muted">Published {calendarDate(item.publishedAt)}</span>{/if}
          </div>
          <div class="text-[19px] leading-tight font-semibold">Ruling on {item.cardName}</div>
        </div>
        <blockquote class="m-0 rounded-lg bg-panel px-4 py-3.5 text-base leading-[1.6] text-fg italic">“{item.text}”</blockquote>
        {#if item.url}
          <a href={item.url} {...external} class="flex items-center gap-3 rounded-[10px] border border-line px-2.5 py-2 text-fg no-underline">
            {#if item.card?.image_small}
              <img src={item.card.image_small} alt="" width="40" height="56" class="h-14 w-10 shrink-0 rounded" />
            {:else}
              <span aria-hidden="true" class="flex h-14 w-10 shrink-0 items-center justify-center rounded border border-line-muted bg-art font-mono text-[8px] text-fg-muted">art</span>
            {/if}
            <span class="flex min-w-0 flex-1 flex-col gap-0.5">
              <span class="text-xs text-fg-muted">From the card</span>
              <span class="text-[15px] font-semibold">{item.cardName}</span>
            </span>
            <Icon name="chevron-right" class="text-fg-muted" />
          </a>
          <div class="mt-auto grid grid-cols-2 gap-2.5">
            <a href="{item.url}#rulings" {...external} class={outlineButton}>All rulings (card)</a>
            <a href={item.url} {...external} class={outlineButton}>Scryfall ↗</a>
          </div>
        {/if}
      {:else}
        <div class="flex flex-col gap-1">
          <div class="font-mono text-xs text-fg-muted">RULE {item.ruleId}</div>
          {#if item.heading}<div class="text-[19px] leading-tight font-semibold">{item.heading}</div>{/if}
        </div>
        <p class="m-0 rounded-lg bg-panel px-3.5 py-3 text-[15px] leading-[1.6] whitespace-pre-line text-fg-body">{item.text}</p>
        <div class="mt-auto grid grid-cols-2 gap-2.5">
          <a href="/rules/{item.ruleId}" class={outlineButton}>Open rule {item.ruleId}</a>
          {#if item.ruleId && parentRule(item.ruleId)}
            <a href="/rules/{parentRule(item.ruleId)}" class={outlineButton}>Parent rule</a>
          {/if}
        </div>
      {/if}
    </div>
  {/if}
</dialog>
```

- [ ] **Step 2: Typecheck**

Run: `npm run check`
Expected: 0 errors.

- [ ] **Step 3: Commit**

```bash
git add mtg-web/src/lib/desk/SourceSheet.svelte
git commit -m "feat: phone source sheet for cited cards, rulings and rules"
```

---

### Task 8: Empty, loading, error, limit notices and matching sources

**Files:**
- Create: `mtg-web/src/lib/desk/EmptyState.svelte`, `LoadingState.svelte`, `ErrorState.svelte`, `StatusNotice.svelte`, `MatchingSources.svelte`

**Interfaces:**
- Consumes: `SearchForm`, `Icon`, `EvidenceCard`, `meta`, `calendarDate`, `resetTime`, `Notice`, `fromResult`, `groupByKind`, `SourceKind`.
- Produces:
  - `<EmptyState bind:query error onsubmit onexample />`
  - `<LoadingState />`
  - `<ErrorState message onretry />`
  - `<StatusNotice kind remaining wide />`, where `wide` switches "on the right" to "below"
  - `<MatchingSources results phone onopen />`

- [ ] **Step 1: `EmptyState.svelte`**

```svelte
<script lang="ts">
  import { MediaQuery } from 'svelte/reactivity';
  import { calendarDate } from '$lib/format';
  import { meta } from '$lib/meta.svelte';
  import SearchForm from './SearchForm.svelte';

  let {
    query = $bindable(''),
    error = '',
    onsubmit,
    onexample
  }: { query?: string; error?: string; onsubmit: () => void; onexample: (q: string) => void } =
    $props();

  const EXAMPLES = [
    'Does trample plus deathtouch only need 1 damage on each blocker?',
    'What happens when two replacement effects apply to the same event?',
    "Can I cast an instant during my opponent's cleanup step?"
  ];
  const phone = new MediaQuery('max-width: 639px');
  const examples = $derived(phone.current ? [EXAMPLES[0], EXAMPLES[2]] : EXAMPLES);
</script>

<main class="mx-auto flex w-full max-w-[720px] flex-col gap-7 px-4 pt-12 pb-12 sm:px-8 sm:pt-24">
  <div class="flex flex-col gap-2">
    <h1 class="m-0 text-[28px] leading-tight font-medium sm:text-4xl">Ask a rules question</h1>
    <p class="m-0 text-base text-fg-soft">
      Answers cite the Comprehensive Rules, {phone.current ? '' : 'Scryfall '}card text and official rulings.
    </p>
  </div>
  <SearchForm bind:value={query} variant="hero" maxChars={meta.max_query_chars} {error} {onsubmit} />
  <div class="flex flex-col gap-2.5">
    <div class="font-mono text-xs tracking-[0.08em] text-fg-muted uppercase">Try one of these</div>
    {#each examples as example (example)}
      <button
        type="button"
        onclick={() => onexample(example)}
        class="min-h-11 cursor-pointer rounded-[10px] border border-line bg-card px-4 py-3 text-left font-sans text-[15px] text-fg-body hover:border-line-strong hover:text-fg"
        >{example}</button
      >
    {/each}
  </div>
  <div class="flex flex-wrap gap-x-5 gap-y-2 font-mono text-xs text-fg-muted">
    {#if meta.rules_as_of && !phone.current}
      <span>Comprehensive Rules · as of {calendarDate(meta.rules_as_of)}</span>
    {/if}
    {#if !phone.current}<span>Scryfall oracle text &amp; rulings</span>{/if}
    {#if meta.answers_per_day}<span>{meta.answers_per_day} AI answers a day</span>{/if}
  </div>
</main>
```

- [ ] **Step 2: `LoadingState.svelte`** (board "Loading")

```svelte
<script lang="ts">
  import Icon from './Icon.svelte';
  const bar = 'h-3.5 rounded bg-skeleton';
</script>

<div aria-busy="true" class="desk:grid desk:grid-cols-[minmax(0,1fr)_460px]">
  <main class="flex flex-col gap-5 px-[18px] py-5 sm:px-9 sm:py-8 desk:border-r desk:border-line desk:px-12 desk:py-9">
    <div role="status" class="flex items-center gap-2.5 font-mono text-[13px] text-fg-soft">
      <Icon name="spinner" size={16} class="animate-spin text-gold motion-reduce:animate-none" />
      Searching rules, cards and rulings, then writing an answer…
    </div>
    <div class="h-7 w-3/5 rounded bg-skeleton"></div>
    <div class="flex flex-col gap-3">
      <div class="{bar} w-full"></div>
      <div class="{bar} w-[96%]"></div>
      <div class="{bar} w-[92%]"></div>
      <div class="{bar} w-2/3"></div>
    </div>
    <div class="flex flex-col gap-3">
      <div class="{bar} w-[94%]"></div>
      <div class="{bar} w-1/2"></div>
    </div>
    <p class="m-0 text-sm text-fg-muted">This usually takes a few seconds.</p>
  </main>
  <aside aria-label="Evidence" class="flex flex-col gap-3 bg-panel p-5">
    <div class="flex gap-5 border-b border-line pb-3 text-sm text-fg-disabled">
      <span>Cited</span><span>Also retrieved</span>
    </div>
    {#each [0, 1, 2] as i (i)}
      <div class="h-24 rounded-[10px] border border-line bg-card"></div>
    {/each}
  </aside>
</div>
```

- [ ] **Step 3: `ErrorState.svelte`** (board "Request failed")

```svelte
<script lang="ts">
  import Icon from './Icon.svelte';

  let { message, onretry }: { message: string; onretry: () => void } = $props();
</script>

<main class="flex justify-center px-4 py-16 sm:py-24">
  <div role="alert" class="flex w-full max-w-[560px] flex-col items-center gap-3.5 text-center">
    <Icon name="warning" size={40} class="text-danger" />
    <h1 class="m-0 text-2xl font-medium">Search didn't go through</h1>
    <p class="m-0 text-base leading-normal text-fg-soft">
      The server didn't answer. Your question is still in the box, so you can try again in a moment.
    </p>
    <button
      type="button"
      onclick={onretry}
      class="min-h-11 cursor-pointer rounded-[7px] border-0 bg-gold px-5 font-sans text-sm font-semibold text-gold-ink"
      >Try again</button
    >
    <details class="text-sm text-fg-muted">
      <summary class="cursor-pointer">Details</summary>
      <code class="font-mono text-xs">{message}</code>
    </details>
  </div>
</main>
```

- [ ] **Step 4: `StatusNotice.svelte`** (boards "Daily AI answers used", "Site-wide budget reached", "Status messages")

```svelte
<script lang="ts">
  import { resetTime } from '$lib/format';
  import { meta } from '$lib/meta.svelte';
  import type { Notice } from '$lib/status';
  import Icon from './Icon.svelte';

  let { kind, remaining }: { kind: Notice; remaining: number | null } = $props();

  const reset = resetTime();
  const perDay = $derived(meta.answers_per_day);
  const paused = $derived(kind === 'paused');
</script>

<div
  role="status"
  class={[
    'flex items-start gap-4 rounded-xl border px-5 py-[18px] max-sm:gap-3 max-sm:px-4 max-sm:py-3.5',
    paused ? 'border-paused-line bg-paused-bg' : 'border-notice-line bg-notice'
  ]}
>
  <Icon
    name={paused ? 'pause' : kind === 'no_answer' ? 'info' : 'clock'}
    size={22}
    class={['mt-0.5 shrink-0', paused ? 'text-paused' : 'text-gold']}
  />
  {#if kind === 'breather'}
    <div class="text-sm leading-normal text-notice-fg">
      <strong class="text-fg">Taking a short breather.</strong> AI answers pause for a few minutes
      when you ask quickly. You still have {remaining} today. Matching sources are below.
    </div>
  {:else}
    <div class="flex flex-col gap-1">
      <div class={['text-[17px] font-semibold', paused ? 'text-paused-fg' : 'text-fg']}>
        {#if paused}AI answers are paused for today
        {:else if kind === 'quota_used'}You've used today's {perDay ? `${perDay} ` : ''}AI answers
        {:else}Couldn't write an answer this time{/if}
      </div>
      <div class="text-sm leading-normal text-notice-fg">
        {#if paused}
          The site has reached its daily limit for everyone, not just you. Answers resume at {reset}
          (midnight UTC). Here are the matching rules, cards and rulings.
        {:else if kind === 'quota_used'}
          They reset at {reset} (midnight UTC). Search still works: here are the rules, cards and
          rulings that match your question.
        {:else}
          Try asking again in a moment. Here are the rules, cards and rulings that match your
          question.
        {/if}
      </div>
    </div>
  {/if}
</div>
```

(The `no_answer` copy isn't on a board; it covers generation errors that return retrieval results with `degraded: null`.)

- [ ] **Step 5: `MatchingSources.svelte`**

```svelte
<script lang="ts">
  import type { QueryResult } from '$lib/api';
  import { fromResult, groupByKind, type EvidenceItem, type SourceKind } from '$lib/evidence';
  import EvidenceCard from './EvidenceCard.svelte';

  let {
    results,
    phone,
    onopen
  }: {
    results: QueryResult[];
    phone: boolean;
    onopen: (items: EvidenceItem[], index: number) => void;
  } = $props();

  const KINDS: SourceKind[] = ['rule', 'card', 'ruling'];
  const FILTERS: ('all' | SourceKind)[] = ['all', ...KINDS];
  const LABELS: Record<SourceKind, string> = { rule: 'Rules', card: 'Cards', ruling: 'Rulings' };

  const items = $derived(results.map(fromResult));
  const groups = $derived(groupByKind(items));
  let filter = $state<'all' | SourceKind>('all');
  const shown = $derived(filter === 'all' ? items : groups[filter]);
</script>

<div class="flex items-baseline justify-between gap-4">
  <h2 class="m-0 text-base font-semibold">Matching sources · {items.length}</h2>
  <span class="font-mono text-xs text-fg-muted">best match first</span>
</div>

{#if phone}
  <div role="group" aria-label="Filter sources" class="flex flex-wrap gap-2">
    {#each FILTERS as kind (kind)}
      {@const count = kind === 'all' ? items.length : groups[kind].length}
      <button
        type="button"
        aria-pressed={filter === kind}
        onclick={() => (filter = kind)}
        class={[
          'min-h-9 cursor-pointer rounded-[18px] border px-3 font-mono text-xs',
          filter === kind ? 'border-gold bg-gold text-gold-ink' : 'border-line-strong bg-transparent text-fg-soft'
        ]}>{kind === 'all' ? 'All' : LABELS[kind]} · {count}</button
      >
    {/each}
  </div>
  <div class="flex flex-col gap-2.5">
    {#each shown as item, i (item.key)}
      <EvidenceCard {item} compact onopen={() => onopen(shown, i)} />
    {/each}
  </div>
{:else}
  <div class="grid gap-6 desk:grid-cols-3">
    {#each KINDS as kind (kind)}
      {#if groups[kind].length}
        <section class="flex flex-col gap-3">
          <h3 class="m-0 font-mono text-xs tracking-[0.08em] text-fg-muted uppercase">
            {LABELS[kind]} · {groups[kind].length}
          </h3>
          {#each groups[kind] as item (item.key)}
            <EvidenceCard {item} />
          {/each}
        </section>
      {/if}
    {/each}
  </div>
{/if}
```

- [ ] **Step 6: Typecheck**

Run: `npm run check`
Expected: 0 errors.

- [ ] **Step 7: Commit**

```bash
git add mtg-web/src/lib/desk
git commit -m "feat: empty, loading, error, limit and matching-sources states"
```

---

### Task 9: Assemble the page, then check it against every board

**Files:**
- Modify: `mtg-web/src/routes/+page.svelte` (full rewrite)

**Interfaces:**
- Consumes: everything above, `AppHeader` (`center` snippet), `admin`, `submitQuery`, `RateLimitedError`.

- [ ] **Step 1: Rewrite `+page.svelte`**

```svelte
<script lang="ts">
  import { onMount } from 'svelte';
  import { MediaQuery } from 'svelte/reactivity';
  import { page } from '$app/state';
  import { admin } from '$lib/admin.svelte';
  import { RateLimitedError, submitQuery, type QueryResponse } from '$lib/api';
  import AppHeader from '$lib/AppHeader.svelte';
  import AnswerBody from '$lib/desk/AnswerBody.svelte';
  import EmptyState from '$lib/desk/EmptyState.svelte';
  import ErrorState from '$lib/desk/ErrorState.svelte';
  import EvidencePanel from '$lib/desk/EvidencePanel.svelte';
  import Icon from '$lib/desk/Icon.svelte';
  import LoadingState from '$lib/desk/LoadingState.svelte';
  import MatchingSources from '$lib/desk/MatchingSources.svelte';
  import RulesReferenced from '$lib/desk/RulesReferenced.svelte';
  import SearchForm from '$lib/desk/SearchForm.svelte';
  import SourceSheet from '$lib/desk/SourceSheet.svelte';
  import StatusChips from '$lib/desk/StatusChips.svelte';
  import StatusNotice from '$lib/desk/StatusNotice.svelte';
  import { fromCitation, type EvidenceItem } from '$lib/evidence';
  import { loadMeta, meta } from '$lib/meta.svelte';
  import type { Selection } from '$lib/selection';
  import { noticeFor } from '$lib/status';

  type View = 'idle' | 'loading' | 'result' | 'failed';

  let query = $state('');
  let view = $state<View>('idle');
  let response = $state<QueryResponse | null>(null);
  let failure = $state('');
  let rateLimited = $state('');
  let selection = $state<Selection | null>(null);
  let sheetItems = $state<EvidenceItem[]>([]);
  let sheetIndex = $state(0);
  let sheetOpen = $state(false);

  const phone = new MediaQuery('max-width: 639px');
  const desk = new MediaQuery('min-width: 1100px');
  const hover = new MediaQuery('hover: hover');

  const notice = $derived(response ? noticeFor(response) : null);
  const citedItems = $derived(response ? response.citations.map(fromCitation) : []);

  onMount(loadMeta);

  // Dev only: ?mock=<fixture> answers from src/lib/fixtures (see the spec).
  const mock = import.meta.env.DEV ? page.url.searchParams.get('mock') : null;

  async function run(q: string, fresh: boolean): Promise<QueryResponse> {
    if (import.meta.env.DEV && mock) return (await import('$lib/fixtures')).mockQuery(mock);
    return submitQuery(q, { fresh });
  }

  async function ask(fresh = false) {
    // A fresh request must regenerate the question whose cached answer is
    // on screen, not whatever is currently sitting in the input box.
    const q = fresh && response ? response.query : query.trim();
    if (!q) return;
    const previous: View = response ? 'result' : 'idle';
    rateLimited = '';
    selection = null;
    sheetOpen = false;
    view = 'loading';
    try {
      response = await run(q, fresh);
      view = 'result';
    } catch (e) {
      if (e instanceof RateLimitedError) {
        rateLimited = e.message;
        view = previous;
      } else {
        failure = e instanceof Error ? e.message : String(e);
        view = 'failed';
      }
    }
  }

  function openSheet(items: EvidenceItem[], index: number) {
    sheetItems = items;
    sheetIndex = Math.max(0, index);
    sheetOpen = true;
  }

  function select(number: number, occurrence: number) {
    if (phone.current) {
      selection = { number, occurrence };
      openSheet(citedItems, citedItems.findIndex((i) => i.number === number));
      return;
    }
    const same = selection?.number === number && selection.occurrence === occurrence;
    selection = same ? null : { number, occurrence };
  }

  // Paging through cited sources in the sheet moves the highlight with it.
  function onSheetChange(item: EvidenceItem) {
    if (item.number === null) return;
    if (selection?.number !== item.number) selection = { number: item.number, occurrence: null };
  }

  function onWindowKey(event: KeyboardEvent) {
    if (event.key === 'Escape' && !sheetOpen) selection = null;
  }
</script>

<svelte:head>
  <title>MTG Rules</title>
</svelte:head>

<svelte:window onkeydown={onWindowKey} />

{#snippet headerForm()}
  <SearchForm
    bind:value={query}
    loading={view === 'loading'}
    retrievalOnly={!!response?.degraded}
    maxChars={meta.max_query_chars}
    error={rateLimited}
    onsubmit={() => ask()}
  />
{/snippet}

<AppHeader center={view === 'idle' ? undefined : headerForm} />

{#if view === 'idle'}
  <EmptyState
    bind:query
    error={rateLimited}
    onsubmit={() => ask()}
    onexample={(q) => {
      query = q;
      ask();
    }}
  />
{:else if view === 'loading'}
  <LoadingState />
{:else if view === 'failed'}
  <ErrorState message={failure} onretry={() => ask()} />
{:else if response && notice}
  <main class="mx-auto flex max-w-[1280px] flex-col gap-6 px-4 py-6 sm:px-8 desk:px-12 desk:py-9">
    <StatusNotice kind={notice} remaining={response.answers_remaining} />
    <MatchingSources results={response.results} phone={phone.current} onopen={openSheet} />
  </main>
{:else if response?.answer}
  <div class="desk:grid desk:grid-cols-[minmax(0,1fr)_460px] desk:items-start">
    <main
      class="flex flex-col gap-5 border-line px-[18px] py-5 max-desk:border-b sm:px-9 sm:py-8 desk:min-h-[calc(100vh-70px)] desk:border-r desk:px-12 desk:py-9"
    >
      <StatusChips
        {response}
        isAdmin={admin.isAdmin}
        loading={false}
        compact={phone.current}
        onfresh={() => ask(true)}
      />
      <AnswerBody
        answer={response.answer}
        citations={response.citations}
        ruleReferences={response.rule_references}
        {selection}
        canHover={hover.current}
        onselect={select}
      />
      {#if response.citation_stats.uncited_answer}
        <div class="flex items-start gap-3 rounded-[10px] border border-caution-line bg-caution-bg px-4 py-3.5">
          <Icon name="info" class="mt-0.5 shrink-0 text-caution" />
          <p class="m-0 text-sm leading-normal text-caution-fg">
            This answer didn't point to any sources, so treat it with care. Check it against the
            passages {desk.current ? 'on the right' : 'below'}, which were retrieved for your question.
          </p>
        </div>
      {/if}
      <RulesReferenced ruleIds={response.rule_references} boxed={desk.current} />
    </main>
    <div class="desk:sticky desk:top-0 desk:max-h-screen desk:overflow-y-auto">
      <EvidencePanel
        citations={response.citations}
        results={response.results}
        selectedNumber={selection?.number ?? null}
        layout={desk.current ? 'side' : phone.current ? 'list' : 'grid'}
        onopen={openSheet}
      />
    </div>
  </div>
{/if}

<SourceSheet
  items={sheetItems}
  bind:index={sheetIndex}
  bind:open={sheetOpen}
  onchange={onSheetChange}
/>
```

- [ ] **Step 2: Build and typecheck**

Run: `npm run check && npm test && npm run build`
Expected: PASS. Confirm the fixtures aren't in the production bundle:

```bash
grep -rl "Colossal Dreadmaw" build/ || echo "fixtures not bundled"
```

Expected: `fixtures not bundled`.

- [ ] **Step 3: Visual check against the boards**

Run `npm run dev` and use the `run` skill (or a browser) at widths 1280, 834 and 390. For each URL, compare against its board on the canvas:

| URL | Board(s) |
|---|---|
| `/` | First visit, Phone — first visit |
| `/?mock=slow` (submit anything) | Loading |
| `/?mock=answered` | Desktop — answered, Tablet — answered, Phone — answered |
| `/?mock=answered`, click marker 3 | Desktop board: marker 3 gold, "Basilisk Collar" sentence highlighted, card 3 highlighted and scrolled into view |
| `/?mock=answered` at 390, tap marker 3 then 5 via "next" | Phone — card citation tapped, Phone — ruling citation tapped (teal highlight) |
| `/?mock=uncited` | Answer with no citations |
| `/?mock=quota` | Daily AI answers used (per visitor), Phone — daily answers used |
| `/?mock=breather` | Status messages: "Taking a short breather" |
| `/?mock=paused` | Site-wide budget reached |
| `/?mock=error` | Request failed |
| `/?mock=ratelimited` | Status messages: 429 row (red border and message under the form) |
| type 400+ characters | Status messages: character counter |
| logged in as admin, `/?mock=answered` | Status messages: cached chip with "Get a fresh answer" |
| hover `510.1c` in the answer (live API) | Status messages: rule-link preview |

Also check with the keyboard only: Tab reaches the form, markers, rule links, tabs and cards. Arrow keys switch tabs. Escape closes the preview, the selection and the sheet, and focus returns to the marker. Check that no width scrolls horizontally.

Fix any differences inside the component that owns them and re-run `npm run check`.

- [ ] **Step 4: Live check**

With the dev stack (`docker compose up -d`, and PR 2's data in place), ask "Does Basilisk Collar give a trampler deathtouch damage assignment?". Card evidence shows real Scryfall images (`cards.scryfall.io/small/…`), rule cards show headings, and the sheet on phone shows the `normal` image.

- [ ] **Step 5: Commit**

```bash
git add mtg-web/src/routes/+page.svelte
git commit -m "feat: Judge's Desk search page with all states"
```

---

### Task 10: README and PR

**Files:**
- Modify: `README.md` (the `mtg-web/` row around line 42)

- [ ] **Step 1: README**

Replace the `mtg-web/` row's description of the search page with: "SvelteKit (Svelte 5) SPA. The search page is the Judge's Desk: the answer with a summary line and selectable citations, an evidence panel (cited sources and other retrieved results, with card images from Scryfall), a phone bottom sheet, and clear states for loading, errors and answer limits. In `npm run dev`, `/?mock=answered|uncited|quota|breather|paused|error|slow|ratelimited` shows each state without calling the API."

- [ ] **Step 2: Final check and PR**

Run (in `mtg-web/`): `npm ci && npm run check && npm test && npm run build && docker build .`
Expected: PASS.

```bash
git add README.md
git commit -m "docs: describe the Judge's Desk search page and dev fixtures"
git push -u origin feature/judge-desk-search
gh pr create --title "Judge's Desk search page" --body "$(cat <<'EOF'
Part 3 of 4 of the Judge's Desk redesign. Spec: docs/superpowers/specs/2026-09-29-judge-desk-search-design.md

- Answer with a summary line; clicking a citation highlights its sentence, marker and evidence card
- Evidence panel with Cited / Also retrieved tabs; card images and rule headings from the API
- Phone bottom sheet with previous/next
- Empty, loading, uncited, quota, breather, site-paused, error and 429 states from the design
- Unit tests for answer layout, evidence, status and dates; dev-only ?mock= fixtures for visual checks

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```
