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
  const out = list.filter(
    (p, i) => !(p.kind === 'text' && !p.text.trim() && (i === 0 || i === list.length - 1))
  );
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
