// Where the hover preview of a card goes: beside its thumbnail, in fixed
// (viewport) coordinates, so a scrolling evidence panel can't clip it.

export interface Size {
  width: number;
  height: number;
}

// A DOMRect satisfies this.
export interface Box extends Size {
  left: number;
  top: number;
}

// Scryfall's `normal` image is 488×680.
export const PREVIEW_SIZE: Size = { width: 250, height: 348 };

const MARGIN = 8;

export function placePreview(
  anchor: Box,
  viewport: Size,
  size: Size,
  gap: number
): { left: number; top: number } {
  const rightSide = anchor.left + anchor.width + gap;
  const leftSide = anchor.left - gap - size.width;
  const roomRight = viewport.width - MARGIN - rightSide;
  const roomLeft = anchor.left - gap - MARGIN;
  let left: number;
  if (roomRight >= size.width) left = rightSide;
  else if (roomLeft >= size.width) left = leftSide;
  else left = roomRight >= roomLeft ? rightSide : leftSide;
  const maxTop = viewport.height - MARGIN - size.height;
  return {
    left: Math.max(MARGIN, left),
    top: Math.max(MARGIN, Math.min(anchor.top, maxTop))
  };
}
