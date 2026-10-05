// Dev-only stand-ins for POST /api/v1/query, so every state of the search
// page can be checked without spending quota: /?mock=<name> in `npm run dev`.
// Content follows the design canvas's worked example (trample + deathtouch).
// Card images are null so the placeholders show, except in `images`, which
// uses real Scryfall URLs (and keeps Basilisk Collar as the placeholder).
import {
  RateLimitedError,
  TOO_MANY_REQUESTS,
  type CardDetails,
  type Citation,
  type QueryResponse,
  type QueryResult,
  type StreamHandlers
} from '../api';
import { splitResponse, type StreamEvent } from '../answer-stream/protocol';
import { liveCitations, visibleDraft } from '../answer-stream/live';
import type { AnswerSource } from '../answer-stream/sources';

export const FIXTURE_NAMES = [
  'answered',
  'images',
  'uncited',
  'quota',
  'breather',
  'paused',
  'error',
  'slow',
  'ratelimited',
  'streaming',
  'thinking',
  'cutoff',
  'streamerror',
  'stalled'
] as const;

const SCRY = 'https://scryfall.com/card/';

function card(
  name: string,
  type_line: string,
  mana_cost: string,
  pt: [string, string] | null = null
): CardDetails {
  return {
    name,
    type_line,
    mana_cost,
    power: pt?.[0] ?? null,
    toughness: pt?.[1] ?? null,
    loyalty: null,
    image_small: null,
    image_normal: null
  };
}

const COLLAR = card('Basilisk Collar', 'Artifact — Equipment', '{1}');
const DREADMAW = card('Colossal Dreadmaw', 'Creature — Dinosaur', '{4}{G}{G}', ['6', '6']);
const SLICE = card('Windswift Slice', 'Instant', '{2}{G}');

function ruleCitation(number: number, ruleId: string, heading: string, text: string): Citation {
  return {
    number,
    source_type: 'rule',
    title: `Rule ${ruleId}`,
    rule_id: ruleId,
    card_name: null,
    oracle_id: null,
    text,
    url: `/rules/${ruleId}`,
    published_at: null,
    heading,
    card: null
  };
}

function cardCitation(number: number, details: CardDetails, text: string): Citation {
  return {
    number,
    source_type: 'card',
    title: `Card — ${details.name}`,
    rule_id: null,
    card_name: details.name,
    oracle_id: `o-${number}`,
    text,
    url: `${SCRY}${details.name.toLowerCase().replace(/\W+/g, '-')}`,
    published_at: null,
    heading: null,
    card: details
  };
}

const CITATIONS: Citation[] = [
  ruleCitation(
    1,
    '702.2c',
    'Deathtouch',
    'Any nonzero amount of combat damage assigned to a creature by a source with deathtouch is considered to be lethal damage for the purposes of determining if a proposed combat damage assignment is valid, regardless of that creature’s toughness.'
  ),
  ruleCitation(
    2,
    '702.19b',
    'Trample',
    'The controller of an attacking creature with trample first assigns damage to the creature(s) blocking it. Once all those blocking creatures are assigned lethal damage, any excess damage is assigned as its controller chooses among those blocking creatures and the player, planeswalker, or battle the creature is attacking.'
  ),
  cardCitation(3, COLLAR, 'Equipped creature has deathtouch and lifelink.\nEquip {2}'),
  cardCitation(4, DREADMAW, 'Trample'),
  {
    number: 5,
    source_type: 'ruling',
    title: 'Ruling — Windswift Slice (2023-06-16)',
    rule_id: null,
    card_name: 'Windswift Slice',
    oracle_id: 'o-slice',
    text: 'Even 1 damage dealt to a creature from a source with deathtouch is considered lethal damage, so any amount greater than that will cause excess damage to be dealt, even if the total amount of damage isn’t greater than the creature’s toughness.',
    url: `${SCRY}windswift-slice`,
    published_at: '2023-06-16',
    heading: null,
    card: SLICE
  }
];

function citedResult(c: Citation): QueryResult {
  return {
    source: c.source_type,
    title: c.rule_id ?? c.card_name ?? c.title,
    text: c.text,
    score: 1,
    match_type: 'fixture',
    oracle_id: c.oracle_id,
    rule_id: c.rule_id,
    card_name: c.card_name,
    published_at: c.published_at,
    scryfall_uri: c.url?.startsWith('http') ? c.url : null,
    cited: true,
    card: c.card,
    heading: c.heading
  };
}

function ruleResult(rule_id: string, heading: string, text: string): QueryResult {
  return { source: 'rule', title: rule_id, text, score: 0.7, match_type: 'fixture', rule_id, heading };
}

const OTHERS: QueryResult[] = [
  ruleResult(
    '510.1c',
    'Combat Damage Step',
    'A blocked creature assigns its combat damage to the creatures blocking it.'
  ),
  ruleResult(
    '510.1a',
    'Combat Damage Step',
    'Each attacking creature and each blocking creature assigns combat damage equal to its power.'
  ),
  ruleResult(
    '702.19c',
    'Trample',
    'Assigning lethal damage to a blocker with trample considers damage already dealt this turn.'
  ),
  ruleResult(
    '702.2b',
    'Deathtouch',
    'A creature with toughness greater than 0 that’s been dealt damage by a source with deathtouch since the last time state-based actions were checked is destroyed.'
  ),
  {
    source: 'ruling',
    title: 'Mirror Shield',
    text: 'Unless the equipped creature has trample, it won’t deal combat damage to the player or planeswalker it’s attacking.',
    score: 0.6,
    match_type: 'fixture',
    card_name: 'Mirror Shield',
    published_at: '2020-01-24',
    scryfall_uri: `${SCRY}mirror-shield`,
    card: card('Mirror Shield', 'Artifact — Equipment', '{3}')
  },
  {
    source: 'oracle',
    title: 'Ohran Frostfang',
    text: 'Attacking creatures you control have deathtouch.',
    score: 0.55,
    match_type: 'fixture',
    card_name: 'Ohran Frostfang',
    scryfall_uri: `${SCRY}ohran-frostfang`,
    card: card('Ohran Frostfang', 'Creature — Snake', '{3}{G}{G}', ['2', '6'])
  }
];

// `normal` URLs from cards_2026-09-29.jsonl; the other sizes share the path.
const ART: Record<string, string> = {
  'Colossal Dreadmaw':
    'https://cards.scryfall.io/normal/front/8/0/8059c52b-5d25-4052-b48a-e9e219a7a546.jpg?1783930678',
  'Windswift Slice':
    'https://cards.scryfall.io/normal/front/f/8/f8097193-1d32-4235-afd4-f6839602e4fb.jpg?1783916024',
  'Mirror Shield':
    'https://cards.scryfall.io/normal/front/e/7/e7624e84-93ce-4983-8624-ebc934cab67f.jpg?1783931516',
  'Ohran Frostfang':
    'https://cards.scryfall.io/normal/front/5/5/55fb93e6-d057-4b70-ad12-e98291fd4a2c.jpg?1783903764'
};

function withArt(c: CardDetails): CardDetails {
  const normal = ART[c.name];
  if (!normal) return c;
  return {
    ...c,
    image_small: normal.replace('/normal/', '/small/'),
    image_normal: normal,
    image_large: normal.replace('/normal/', '/large/')
  };
}

const ANSWER =
  'Yes — each blocker only needs 1 damage. Deathtouch makes any nonzero amount of combat damage count as lethal when assigning it [1]. ' +
  'Trample only requires lethal damage on each blocker before the excess can be assigned to the player [2]. ' +
  'A published ruling confirms the reading: with deathtouch, anything beyond 1 damage to a creature is excess damage [5].\n\n' +
  'A Colossal Dreadmaw [4] equipped with Basilisk Collar [3] and blocked by two creatures can assign 1 to each blocker and 4 to the defending player. ' +
  'See 510.1c for how assignment works.';

const BASE: QueryResponse = {
  query: 'Does trample plus deathtouch only need 1 damage on each blocker?',
  results: [...CITATIONS.map(citedResult), ...OTHERS],
  answer: ANSWER,
  citations: CITATIONS,
  rule_references: ['510.1c', '702.2c', '702.19b'],
  citation_stats: { cited_count: 5, invalid_count: 0, uncited_answer: false },
  answer_complete: true,
  cached_at: '2026-09-27T18:04:00Z',
  degraded: null,
  answers_remaining: 7
};

const uncitedResults = BASE.results.map((r) => ({ ...r, cited: false }));

const retrievalOnly: QueryResponse = {
  ...BASE,
  answer: null,
  citations: [],
  rule_references: [],
  results: uncitedResults,
  answer_complete: null,
  cached_at: null
};

const FIXTURES: Record<string, () => QueryResponse> = {
  answered: () => BASE,
  images: () => ({
    ...BASE,
    citations: BASE.citations.map((c) => ({ ...c, card: c.card && withArt(c.card) })),
    results: BASE.results.map((r) => ({ ...r, card: r.card && withArt(r.card) }))
  }),
  uncited: () => ({
    ...BASE,
    query: 'What happens when two replacement effects apply to the same event?',
    answer:
      'The affected player or controller chooses one to apply first, then checks whether the other still applies. Repeat until none are left.',
    citations: [],
    rule_references: [],
    results: uncitedResults,
    citation_stats: { cited_count: 0, invalid_count: 0, uncited_answer: true },
    cached_at: null,
    answers_remaining: 12
  }),
  quota: () => ({ ...retrievalOnly, degraded: 'ip_quota', answers_remaining: 0 }),
  breather: () => ({ ...retrievalOnly, degraded: 'ip_quota', answers_remaining: 12 }),
  paused: () => ({ ...retrievalOnly, degraded: 'global_budget', answers_remaining: 0 })
};

export function mockQuery(name: string): Promise<QueryResponse> {
  if (name === 'slow') return new Promise(() => {});
  if (name === 'error') return Promise.reject(new Error('query failed: 502'));
  if (name === 'ratelimited')
    return Promise.reject(
      new RateLimitedError(TOO_MANY_REQUESTS)
    );
  const make = FIXTURES[name] ?? FIXTURES.answered;
  // A short delay so the loading state shows between states.
  return new Promise((resolve) => setTimeout(() => resolve(structuredClone(make())), 400));
}

// Streamed versions of the fixtures, for the stream the page really reads.
// `streaming`, `thinking`, `cutoff`, `streamerror` and `stalled` write the
// worked example as it arrives; every other name answers at once, like a
// cache hit or a degraded request.

function wait(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(new DOMException('Aborted', 'AbortError'));
    const timer = setTimeout(resolve, ms);
    signal?.addEventListener(
      'abort',
      () => {
        clearTimeout(timer);
        reject(new DOMException('Aborted', 'AbortError'));
      },
      { once: true }
    );
  });
}

// Fixed-size pieces, the way Gemini's chunks break mid-marker and mid-word.
function pieces(text: string, size = 14): string[] {
  const out: string[] = [];
  for (let i = 0; i < text.length; i += size) out.push(text.slice(i, i + size));
  return out;
}

// How long each streamed fixture thinks, and what share of the answer it
// writes before stopping (null: all of it; 0: none, then an error).
const STREAMED: Record<string, { thinkMs: number; stopAt: number | null; stall?: boolean }> = {
  streaming: { thinkMs: 600, stopAt: null },
  thinking: { thinkMs: 2500, stopAt: null },
  cutoff: { thinkMs: 600, stopAt: 0.6 },
  stalled: { thinkMs: 300, stopAt: 0.4, stall: true },
  streamerror: { thinkMs: 1200, stopAt: 0 }
};

/** Never yields; rejects when `signal` aborts (the `slow` and `stalled` hangs). */
function never(signal: AbortSignal): Promise<never> {
  return new Promise((_, reject) =>
    signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), {
      once: true
    })
  );
}

export function fixtureSource(name: string): AnswerSource {
  return async function* (_query, { signal }): AsyncGenerator<StreamEvent> {
    if (name === 'slow') await never(signal);
    if (name === 'error') throw new Error('query failed: 502');
    if (name === 'ratelimited') throw new RateLimitedError(TOO_MANY_REQUESTS);
    const plan = STREAMED[name];
    if (!plan) {
      const { head, done } = splitResponse(
        structuredClone((FIXTURES[name] ?? FIXTURES.answered)()),
        []
      );
      await wait(400, signal);
      yield { type: 'results', head };
      yield { type: 'done', done };
      return;
    }

    const base = structuredClone({ ...BASE, cached_at: null });
    await wait(300, signal);
    yield {
      type: 'results',
      head: splitResponse({ ...base, results: uncitedResults }, base.citations).head
    };
    yield { type: 'thinking' };
    await wait(plan.thinkMs, signal);

    const full = base.answer!;
    const text = plan.stopAt === null ? full : full.slice(0, Math.floor(full.length * plan.stopAt));
    for (const piece of pieces(text)) {
      yield { type: 'delta', text: piece };
      await wait(40, signal);
    }
    if (plan.stall) await never(signal);

    if (plan.stopAt === 0) {
      yield { type: 'error', message: 'Gemini returned no answer: SAFETY' };
      yield {
        type: 'done',
        done: splitResponse({ ...retrievalOnly, answer_complete: null }, []).done
      };
      return;
    }
    if (plan.stopAt === null) {
      yield { type: 'done', done: splitResponse(base, []).done };
      return;
    }
    const answer = visibleDraft(text).trimEnd();
    const citations = liveCitations(answer, base.citations);
    const cited = new Set(citations.map((c) => c.number));
    yield {
      type: 'done',
      done: splitResponse(
        {
          ...base,
          answer,
          citations,
          rule_references: base.rule_references.filter((id) => answer.includes(id)),
          results: base.results.map((r, i) => ({ ...r, cited: cited.has(i + 1) })),
          citation_stats: { cited_count: citations.length, invalid_count: 0, uncited_answer: false },
          answer_complete: false
        },
        []
      ).done
    };
  };
}

/** The old handler interface over fixtureSource, until the page switches (Task 6). */
export async function mockStream(
  name: string,
  on: StreamHandlers,
  signal: AbortSignal = new AbortController().signal
): Promise<void> {
  for await (const e of fixtureSource(name)('', { fresh: false, signal })) {
    if (signal.aborted) return;
    if (e.type === 'results') on.results(e.head);
    else if (e.type === 'thinking') on.thinking();
    else if (e.type === 'delta') on.delta(e.text);
    else if (e.type === 'error') on.error(e.message);
    else on.done(e.done);
  }
}
