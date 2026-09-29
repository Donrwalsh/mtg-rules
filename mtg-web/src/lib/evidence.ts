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
