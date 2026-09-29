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
  return { source: 'rule', title: '702.2c', text: 'x', score: 1, ...over };
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
    expect(rule).toMatchObject({
      key: 'r3',
      number: null,
      ruleId: '702.2c',
      heading: 'Deathtouch',
      url: '/rules/702.2c'
    });
    const ruling = fromResult(
      result({ source: 'ruling', title: 'Windswift Slice', card_name: 'Windswift Slice' }),
      0
    );
    expect(ruling).toMatchObject({ kind: 'ruling', cardName: 'Windswift Slice', ruleId: null });
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
