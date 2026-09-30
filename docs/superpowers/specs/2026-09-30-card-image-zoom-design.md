# Card image zoom

Status: design agreed, awaiting implementation
Date: 2026-09-30
Branch: `feature/card-image-zoom` (cut from `main`)

## Purpose

Card art on the Judge's Desk is small: 96×134 on evidence cards, 150×209
in the phone source sheet, 52×72 and 40×56 elsewhere. This adds two ways
to see a card properly:

- **Hover preview** (mouse only): a larger copy of the card beside the
  thumbnail.
- **Zoom** (every device, mainly for phones): a click or tap fills the
  screen with a large image of the card until dismissed.

## Investigation findings

1. **Four places show card art.**
   - `EvidenceCard.svelte`, full layout: 96×134, `image_small`. Used on
     the search page at tablet and desk widths and on the history page.
   - `EvidenceCard.svelte`, compact layout: 52×72, `image_small`, inside
     a `<button>` that opens the source sheet. Used only on phones
     (`max-width: 639px`).
   - `SourceSheet.svelte`, card view: 150×209, `image_normal`.
   - `SourceSheet.svelte`, ruling view: 40×56, `image_small`, inside an
     `<a>` to the card's Scryfall page.
2. **Scryfall sizes share one URL.** The API derives `image_small` by
   swapping `/normal/` for `/small/` in the stored `normal` URL. `large`
   (672×936) is the same swap, so it needs no ingestion change.
   `small` is 146px wide, so the 96px thumbnail is soft on 2× screens.
3. **Stored answers predate any new field.** History rows and cached
   answers keep the `CardDetails` dumps from when they were written, so
   some cards will arrive without `image_large`.
4. **The desk evidence panel scrolls** (`desk:overflow-y-auto`). An
   absolutely positioned popover inside it would be clipped, unlike the
   citation `Popover`, which sits in the answer text.
5. **The source sheet is a native `<dialog>` opened with `showModal()`**
   and adds no history entry, so Android back leaves the page while it
   is open.
6. **Hover is already gated** by `new MediaQuery('hover: hover')` in the
   pages, passed down as `canHover`.
7. **Tests are vitest only**, on pure `src/**/*.test.ts` modules. There
   are no component or browser tests. `?mock=<fixture>` in `npm run dev`
   answers from `src/lib/fixtures` without a backend. Every fixture card
   has `image_* = null`.

## Decisions

| Question | Decision |
|---|---|
| Which art zooms | Full `EvidenceCard` art, sheet card art, sheet ruling thumbnail. Compact phone rows are unchanged: tapping still opens the sheet, and you zoom from there. |
| Where hover applies | `(hover: hover) and (pointer: fine)` only. A desktop window narrowed below 640px shows compact rows with no preview; accepted. |
| Hover preview | Fixed-position popover beside the thumbnail, 250px wide, `image_normal`, after a 150ms delay. It flips sides and clamps to stay on screen. |
| Zoom image | Shows `image_normal` at once (usually cached), then swaps to `image_large` when it has loaded. Falls back to `image_normal` when `image_large` is missing. |
| Zoom chrome | The card alone, up to 90vh, on the dimmed scrim. No name, links or buttons. |
| Closing zoom | A tap or click anywhere, Escape, or browser back. |
| Keyboard | Art is a `<button aria-label="Enlarge {name}">`. Enter or Space zooms, focus does not show the preview, and focus returns to the button on close. |
| Preview vs. click | A click hides the preview at once. Scroll and pointer leave also hide it. Only one preview is shown at a time. |
| Zoom over the sheet | The zoom is a second modal dialog layered above the sheet. Closing it returns to the sheet. |
| Sheet back button | The sheet also gets a history entry, so back closes the sheet. |
| No image | The placeholder stays a plain `<span>`, with no preview and no zoom. |
| Thumbnail source | Stays `image_small`. Revisit after seeing it in the browser. |
| Double-faced cards | Front face only; out of scope. |
| Tests | Playwright end-to-end tests in CI, plus vitest for the preview placement maths and pytest for `image_large`. |

## Changes

### API (`mtg-api`)

`CardDetails` gains `image_large: str | None`, derived in `card_details()`
like `image_small` (`/normal/` → `/large/`). `test_card_details.py`
asserts it, including `None` when there is no image. No other API change.

### Web (`mtg-web`)

**Types.** `CardDetails.image_large?: string | null`. It is optional
because stored history and cached answers lack it.

**`src/lib/overlays.ts`** holds the zoom and sheet state, both kept in
SvelteKit shallow-routing state (`pushState` / `page.state`):

- `App.PageState` gains `sheet?: true` and
  `zoom?: { name: string; normal: string; large: string | null }`.
- `openZoom(card)` calls `pushState('', { ...page.state, zoom })`.
  Spreading keeps `sheet`, so back from a zoom returns to the open sheet.
- `closeZoom()` calls `history.back()` when `page.state.zoom` is set.
  Back therefore always pops exactly the entry that opened the zoom.
- `openSheet()` and `closeSheet()` do the same for `sheet`.
- `page.state` is empty on a fresh load, so a reload never reopens a
  zoom or sheet.

**`src/lib/desk/CardZoom.svelte`** is one instance in `+layout.svelte`.
It is a native `<dialog>` whose `showModal()` and `close()` follow
`page.state.zoom`. The `close` event (Escape) and any click call
`closeZoom()`. It records `document.activeElement` on open and focuses it
again on close, as `SourceSheet` does. The `<img>` starts on `normal`. A
preloaded `Image()` of `large` replaces the `src` on `load`, and an
error leaves `normal` in place.

**`src/lib/desk/CardArt.svelte`** is the zoomable thumbnail, used by
every zoomable site:

- Props: `card: CardDetails`, `src` (the thumbnail URL to show), `width`,
  `height`, `class`.
- It renders a `<button type="button" aria-label="Enlarge {name}">`
  wrapping the `<img>`. A click hides the preview, stops propagation and
  calls `openZoom`.
- When `(hover: hover) and (pointer: fine)` matches, which it reads from
  its own `MediaQuery`: `pointerenter` starts a 150ms timer to show the
  preview, and `pointerleave` or a window `scroll` (capture, so the
  panel's scroll counts too) hides it.
- The preview is a `position: fixed` element, placed by
  `placePreview()` and rendered with `role="presentation"`. The button's
  label already names the card.

**`src/lib/cardPreview.ts`** exports the pure placement function
`placePreview(anchor, viewport, size, gap)` → `{ left, top }`. It places
the preview right of the anchor, top-aligned. If that overflows, it uses
the left side, and if neither fits, the side with more room. `top` is
clamped so the preview stays inside the viewport with an 8px margin.
`cardPreview.test.ts` covers the right side, the left flip, the clamp
at the top and bottom, and a too-narrow viewport.

**`EvidenceCard.svelte`.** Full-layout art uses `CardArt` with
`src={image_small}`. Compact art is unchanged.

**`SourceSheet.svelte`.**

- Card view art uses `CardArt` with `src={image_normal}`.
- The ruling view's `<a>` row becomes a `<div>` holding two siblings:
  `CardArt` (`image_small`), and an `<a>` wrapping the text and chevron
  that links to Scryfall. A button can't sit inside a link.
- `open` becomes a plain prop, `open={!!page.state.sheet}`, and a new
  `onclose` callback replaces `bind:open`. The Close button, the
  backdrop and Escape (the dialog's `close` event while `open` is still
  true) all call `onclose`, which is `closeSheet()`. `+page.svelte`'s
  `openSheet` calls the module's `openSheet()` after setting items and
  index. `ask()` can't run while the modal sheet is open, so its
  `sheetOpen = false` goes away.
- `+page.svelte`'s window Escape handler clears the citation selection
  only when neither the sheet nor a zoom is open. Escape keydown reaches
  the window before the dialog closes.

**Fixtures.** A new `images` fixture is `answered` with real Scryfall
`normal` / `small` / `large` URLs on every card (taken from
`cards_2026-09-29.jsonl`), except Basilisk Collar (citation 3), which
keeps `null` as the placeholder case. Windswift Slice (ruling 5) keeps
its art so the ruling-sheet zoom can be tested. The existing fixtures keep `null` images.

### End-to-end tests (`mtg-web/e2e`)

- Playwright with Chromium only, `@playwright/test` as a dev dependency.
- `npm run test:e2e` runs `playwright test`. Its `webServer` runs
  `vite dev`, and the tests visit `/?mock=images`, so no backend is
  needed.
- `page.route('https://cards.scryfall.io/**')` serves a local PNG from
  `e2e/assets/`, so no test depends on the network. `large` requests are
  served with a short delay, so the normal → large swap is observable.
- Projects: `desktop` (1280×800, mouse) and `phone` (a Pixel 7 device
  profile, touch, `hover: none`).
- Desktop tests:
  - Hovering the art shows the preview, and the preview is inside the
    viewport. Leaving hides it.
  - A click opens the zoom and hides the preview. The zoom shows `large`
    after the swap.
  - Escape closes the zoom, and focus is back on the art button.
  - A click on the backdrop closes the zoom.
  - Back closes the zoom and leaves the URL on the search page.
  - The placeholder card has no "Enlarge" button.
- Phone tests:
  - Tapping a compact card row opens the sheet, not a zoom.
  - Tapping the sheet art opens the zoom above the sheet.
  - Back closes the zoom with the sheet still open, and back again
    closes the sheet with the page still loaded.
  - Tapping ruling-sheet art zooms, and tapping the ruling row's text
    has an external Scryfall `href`.
  - No hover preview ever appears.
- Assertions wait on visible state. No fixed sleeps.
- `.github/workflows/frontend-tests.yml` adds, after unit tests:
  `npx playwright install --with-deps chromium` (with
  `~/.cache/ms-playwright` cached on the lockfile hash), then
  `npm run test:e2e`. The Playwright report is uploaded as an artifact
  on failure.
- `vitest`'s `include` stays `src/**/*.test.ts`, so it never picks up
  `e2e/`.

## Out of scope

- Back faces of double-faced cards.
- Sharper thumbnails (`image_normal` for the 96px art).
- Pinch-zoom or pan inside the zoom; the browser's own pinch still works.
- The citation `Popover` and rule-link previews.

## Manual check

In `npm run dev` at `/?mock=images`, at desk, tablet and phone widths
(DevTools touch emulation for the phone):

- Hover, zoom and closing work as above.
- Whether the 96px thumbnails look soft now that they are still
  `image_small`. This is a reminder the user asked for.
