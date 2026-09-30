export type TextPart = { kind: 'text'; text: string } | { kind: 'rule'; text: string; ruleId: string };

// Group 1: a dotted rule id (702.19b), with the same guards as segments.ts
// against prices, dotted dates and versions. Group 2: a bare three-digit
// rule, only after "rule " / "rules " so "100 cards" stays text.
const REF_RE = /(?<![\d.$€£])(\d{3}\.\d+[a-z]?)(?![a-z\d]|\.\d)|(?<=\brules?\s)(\d{3})(?!\d|\.\d)/g;

/** Split rule text so references to other rules can be rendered as links;
 * everything stays text, nothing is injected as HTML. */
export function splitRuleText(text: string): TextPart[] {
  const parts: TextPart[] = [];
  let cursor = 0;
  for (const match of text.matchAll(REF_RE)) {
    const start = match.index ?? 0;
    if (start > cursor) parts.push({ kind: 'text', text: text.slice(cursor, start) });
    parts.push({ kind: 'rule', text: match[0], ruleId: match[1] ?? match[2] });
    cursor = start + match[0].length;
  }
  if (cursor < text.length) parts.push({ kind: 'text', text: text.slice(cursor) });
  return parts;
}
