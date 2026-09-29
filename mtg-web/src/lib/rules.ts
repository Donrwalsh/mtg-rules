// The Comprehensive Rules' nine sections (mirrors CR_SECTIONS in the API's
// rules_index.py; only the raw rules text names them).
export const CR_SECTIONS: Record<number, string> = {
  1: 'Game Concepts',
  2: 'Parts of a Card',
  3: 'Card Types',
  4: 'Zones',
  5: 'Turn Structure',
  6: 'Spells, Abilities, and Effects',
  7: 'Additional Rules',
  8: 'Multiplayer Rules',
  9: 'Casual Variants'
};

// Same tolerance as the API: "702.11B." -> "702.11b".
export function normalizeRuleId(input: string): string {
  return input.trim().replace(/\.$/, '').toLowerCase();
}

// The entry a rule belongs to: 702.2c -> 702.2. Entries and top-level
// rules are their own entry.
export function entryIdOf(ruleId: string): string {
  const sub = ruleId.match(/^(\d{3}\.\d+)[a-z]+$/);
  return sub ? sub[1] : ruleId;
}

export function isTopLevel(ruleId: string): boolean {
  return /^\d{3}$/.test(ruleId);
}

export function sectionOf(ruleId: string): number | null {
  const m = ruleId.match(/^(\d)\d{2}(?:\.|$)/);
  return m ? Number(m[1]) : null;
}

export function neighbours<T extends { rule_id: string }>(
  list: T[],
  id: string
): { prev: T | null; next: T | null } {
  const i = list.findIndex((r) => r.rule_id === id);
  if (i === -1) return { prev: null, next: null };
  return { prev: list[i - 1] ?? null, next: list[i + 1] ?? null };
}

// Up to `size` items centred on `id`, clamped to the list's ends.
export function windowAround<T extends { rule_id: string }>(list: T[], id: string, size = 14): T[] {
  if (list.length <= size) return list;
  const i = Math.max(0, list.findIndex((r) => r.rule_id === id));
  const start = Math.min(Math.max(0, i - Math.floor(size / 2)), list.length - size);
  return list.slice(start, start + size);
}
