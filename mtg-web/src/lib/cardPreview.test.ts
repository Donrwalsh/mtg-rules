import { describe, expect, it } from 'vitest';
import { PREVIEW_SIZE, placePreview } from './cardPreview';

const VIEW = { width: 1280, height: 800 };
const art = (left: number, top: number) => ({ left, top, width: 96, height: 134 });

describe('placePreview', () => {
  it('goes right of the art, top-aligned', () => {
    expect(placePreview(art(100, 200), VIEW, PREVIEW_SIZE, 12)).toEqual({ left: 208, top: 200 });
  });

  it('flips left when the right side would overflow', () => {
    expect(placePreview(art(1000, 200), VIEW, PREVIEW_SIZE, 12)).toEqual({ left: 738, top: 200 });
  });

  it('keeps an 8px margin at the bottom', () => {
    expect(placePreview(art(100, 700), VIEW, PREVIEW_SIZE, 12).top).toBe(800 - 8 - 348);
  });

  it('keeps an 8px margin at the top when the art is scrolled half out', () => {
    expect(placePreview(art(100, -50), VIEW, PREVIEW_SIZE, 12).top).toBe(8);
  });

  it('uses the roomier side when neither fits', () => {
    const narrow = { width: 400, height: 800 };
    // right: 400 - 8 - 258 = 134; left: 150 - 12 - 8 = 130
    expect(placePreview(art(150, 200), narrow, PREVIEW_SIZE, 12).left).toBe(258);
  });

  it('never goes past the left margin', () => {
    const narrow = { width: 300, height: 800 };
    // Right: 300 - 8 - 168 = 124; left: 60 - 12 - 8 = 40. Neither fits.
    expect(placePreview(art(60, 200), narrow, PREVIEW_SIZE, 12).left).toBe(168);
    // Left is roomier here, and clamps to the margin.
    expect(placePreview(art(200, 200), narrow, PREVIEW_SIZE, 12).left).toBe(8);
  });

  it('pins to the top margin in a viewport shorter than the preview', () => {
    expect(placePreview(art(100, 100), { width: 1280, height: 300 }, PREVIEW_SIZE, 12).top).toBe(
      8
    );
  });
});
