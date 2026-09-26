// Splits an answer into plain text, citation markers, and rule references so
// each can be rendered as its own element -- model output is never injected
// as HTML. The server has already removed invalid markers and validated rule
// references; this only needs to find them and check membership.

export type Segment =
  | { kind: 'text'; text: string }
  | { kind: 'cite'; numbers: number[] }
  | { kind: 'rule'; ruleId: string };

// Group 1: a well-formed marker's numbers ([1], [1, 3]).
// Group 2: a rule-number candidate, with the same lookarounds as the server
// (mtg_api/citations.py) so prices, dotted dates and versions don't match.
const TOKEN_RE =
  /\[(\s*\d+(?:\s*,\s*\d+)*\s*)\]|(?<![\d.$€£])(\d{3}\.\d+[a-z]?)(?![a-z\d]|\.\d)/g;

export function segmentAnswer(
  answer: string,
  citationNumbers: Set<number>,
  ruleRefs: Set<string>
): Segment[] {
  const segments: Segment[] = [];
  const pushText = (text: string) => {
    if (!text) return;
    const last = segments[segments.length - 1];
    if (last && last.kind === 'text') last.text += text;
    else segments.push({ kind: 'text', text });
  };

  let cursor = 0;
  for (const match of answer.matchAll(TOKEN_RE)) {
    const start = match.index ?? 0;
    let segment: Segment | null = null;
    if (match[1] !== undefined) {
      const numbers = match[1]
        .split(',')
        .map((n) => parseInt(n.trim(), 10))
        .filter((n) => citationNumbers.has(n));
      if (numbers.length) segment = { kind: 'cite', numbers };
    } else if (ruleRefs.has(match[2])) {
      segment = { kind: 'rule', ruleId: match[2] };
    }
    if (!segment) continue; // left in place as plain text
    pushText(answer.slice(cursor, start));
    segments.push(segment);
    cursor = start + match[0].length;
  }
  pushText(answer.slice(cursor));
  return segments;
}
