# Card Image Zoom Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Mouse users get a hover preview of a larger card image. On any
device, clicking or tapping card art fills the screen with the card, and
back, Escape or a tap closes it.

**Architecture:** A `CardArt` button wraps each zoomable thumbnail. It
shows a fixed-position preview, placed by the pure `placePreview()`, and
opens the zoom through `overlays.ts`. Zoom and sheet visibility live in
SvelteKit shallow-routing state (`page.state`), so the browser's back
button closes them in order. One `CardZoom` dialog in `+layout.svelte`
renders the zoom. The API adds `image_large`. Playwright covers the
interactions in CI.

**Tech Stack:** SvelteKit 2 / Svelte 5 runes, Tailwind 4, vitest,
Playwright (new), FastAPI/pydantic with pytest.

**Spec:** `docs/superpowers/specs/2026-09-30-card-image-zoom-design.md`

## Global Constraints

- Branch `feature/card-image-zoom`. Don't touch the user's uncommitted `.env.example` / `docker-compose.yml` edits, and never `git add -A`.
- Hover preview only when `(hover: hover) and (pointer: fine)` matches. Delay 150ms. Size 250×348. `image_normal`.
- Zoom: the card alone, `width: min(92vw, calc(90dvh * 488 / 680))`, aspect 488/680, on the existing `backdrop:bg-scrim`. Shows `normal` first, then `large` once it has loaded. Falls back to `normal` when `large` is missing.
- Art button accessible name: `Enlarge {card name}`.
- The compact phone rows (`EvidenceCard` `compact`) are not changed.
- Placeholder art (no image) stays a non-interactive `<span>`.
- `CardDetails.image_large` is optional in TypeScript (`image_large?: string | null`), because stored history and cached answers lack it.
- Playwright uses Chromium only. Assertions wait on visible state, with no fixed sleeps.
- Web commands run in `mtg-web/`, API commands in `mtg-api/`.

---

### Task 1: API returns `image_large`

**Files:**
- Modify: `mtg-api/src/mtg_api/card_details.py`
- Test: `mtg-api/tests/test_card_details.py`

**Interfaces:**
- Produces: JSON field `card.image_large: str | None` on every card detail the API returns.

- [ ] **Step 1: Write the failing test.** In `test_card_details_from_a_parsed_row`, after the `image_small` assert, add:

```python
    assert details.image_large == "https://cards.scryfall.io/large/front/a/4/abc.jpg?1783907750"
```

In `test_card_details_tolerates_rows_from_before_the_new_fields`, change the last assert to:

```python
    assert details.image_small is None and details.image_normal is None
    assert details.image_large is None
```

- [ ] **Step 2: Run it and confirm it fails.** From `mtg-api/`, run `pytest tests/test_card_details.py -v`. Expected: FAIL with `AttributeError`, or a pydantic error, on `image_large`.

- [ ] **Step 3: Implement.** In `card_details.py`, add the field after `image_normal: str | None = None`:

```python
    image_large: str | None = None
```

In `card_details()`, add after `image_normal=normal,`:

```python
        image_large=normal.replace("/normal/", "/large/", 1) if normal else None,
```

- [ ] **Step 4: Run the full suite and lint.** From `mtg-api/`, run `pytest && ruff check . && ruff format --check .`. Expected: all pass. Tests that compare whole card dicts would also fail here if any exist; update their expected dicts to include `image_large`.

- [ ] **Step 5: Commit.**

```bash
git add mtg-api/src/mtg_api/card_details.py mtg-api/tests/test_card_details.py
git commit -m "Return image_large on card details"
```

---

### Task 2: Web type and `images` fixture

**Files:**
- Modify: `mtg-web/src/lib/api.ts:12-23` (`CardDetails`)
- Modify: `mtg-web/src/lib/fixtures/index.ts`
- Test: `mtg-web/src/lib/fixtures/fixtures.test.ts`

**Interfaces:**
- Produces: `CardDetails.image_large?: string | null`, and `/?mock=images` in `npm run dev`. That fixture is `answered`, with Scryfall art on Colossal Dreadmaw (citation 4), Windswift Slice (ruling 5), Mirror Shield and Ohran Frostfang. Basilisk Collar (citation 3) keeps `null` images.

- [ ] **Step 1: Write the failing test.** Add to `fixtures.test.ts`, inside the `describe`:

```ts
  it('images gives art to every card but Basilisk Collar', async () => {
    const r = await mockQuery('images');
    const cards = [...r.citations.map((c) => c.card), ...r.results.map((x) => x.card)].filter(
      (c) => c != null
    );
    for (const c of cards) {
      if (c.name === 'Basilisk Collar') {
        expect(c.image_normal).toBeNull();
      } else {
        expect(c.image_normal).toMatch(/^https:\/\/cards\.scryfall\.io\/normal\//);
        expect(c.image_small).toBe(c.image_normal!.replace('/normal/', '/small/'));
        expect(c.image_large).toBe(c.image_normal!.replace('/normal/', '/large/'));
      }
    }
    expect(cards.some((c) => c.name === 'Basilisk Collar')).toBe(true);
    expect(cards.some((c) => c.name === 'Windswift Slice' && c.image_normal)).toBe(true);
  });

  it('answered keeps null art', async () => {
    const r = await mockQuery('answered');
    expect(r.citations.every((c) => !c.card?.image_normal)).toBe(true);
  });
```

- [ ] **Step 2: Run it and confirm it fails.** From `mtg-web/`, run `npx vitest run src/lib/fixtures`. Expected: the `images` test fails, because unknown names fall back to `answered`, which has no art.

- [ ] **Step 3: Implement.**

In `api.ts`, add to `CardDetails` after `image_normal: string | null;`:

```ts
  // Missing from answers stored before the API returned it.
  image_large?: string | null;
```

In `fixtures/index.ts`:

1. Add `'images'` to `FIXTURE_NAMES`, after `'answered'`.
2. Change the header comment's last two lines to:

```ts
// Card images are null so the placeholders show, except in `images`, which
// uses real Scryfall URLs (and keeps Basilisk Collar as the placeholder).
```

3. Add this after the `OTHERS` array:

```ts
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
```

4. Add this entry to `FIXTURES`, after `answered: () => BASE,`:

```ts
  images: () => ({
    ...BASE,
    citations: BASE.citations.map((c) => ({ ...c, card: c.card && withArt(c.card) })),
    results: BASE.results.map((r) => ({ ...r, card: r.card && withArt(r.card) }))
  }),
```

- [ ] **Step 4: Run the tests and type check.** From `mtg-web/`, run `npm test && npm run check`. Expected: all pass, with 0 errors.

- [ ] **Step 5: Commit.**

```bash
git add mtg-web/src/lib/api.ts mtg-web/src/lib/fixtures/index.ts mtg-web/src/lib/fixtures/fixtures.test.ts
git commit -m "Add image_large to CardDetails and an images fixture"
```

---

### Task 3: Preview placement function

**Files:**
- Create: `mtg-web/src/lib/cardPreview.ts`
- Test: `mtg-web/src/lib/cardPreview.test.ts`

**Interfaces:**
- Produces: `placePreview(anchor: Box, viewport: Size, size: Size, gap: number): { left: number; top: number }`, plus `export const PREVIEW_SIZE: Size = { width: 250, height: 348 }`. `Box` is `{ left; top; width; height }`, which a `DOMRect` satisfies. `Size` is `{ width; height }`.

- [ ] **Step 1: Write the failing tests.** Create `cardPreview.test.ts`:

```ts
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
```

- [ ] **Step 2: Run them and confirm they fail.** From `mtg-web/`, run `npx vitest run src/lib/cardPreview`. Expected: FAIL, because the module can't be resolved.

- [ ] **Step 3: Implement.** Create `cardPreview.ts`:

```ts
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
```

- [ ] **Step 4: Run the tests and confirm they pass.** From `mtg-web/`, run `npx vitest run src/lib/cardPreview`. Expected: 7 passed.

- [ ] **Step 5: Commit.**

```bash
git add mtg-web/src/lib/cardPreview.ts mtg-web/src/lib/cardPreview.test.ts
git commit -m "Add hover preview placement"
```

---

### Task 4: Playwright setup and CI

**Files:**
- Modify: `mtg-web/package.json`, `mtg-web/package-lock.json` (via npm)
- Create: `mtg-web/playwright.config.ts`
- Create: `mtg-web/e2e/helpers.ts`
- Create: `mtg-web/e2e/smoke.desktop.spec.ts`
- Modify: `mtg-web/.gitignore`
- Modify: `.github/workflows/frontend-tests.yml`

**Interfaces:**
- Consumes: `/?mock=images` (Task 2).
- Produces: `npm run test:e2e`. The `desktop` project runs `e2e/*.desktop.spec.ts` at 1280×800 with a mouse. The `phone` project runs `e2e/*.phone.spec.ts` on the Pixel 7 profile, with touch and `hover: none`. The helper is `askFixture(page: Page): Promise<void>`: it stubs `/api/v1/**` (503) and `cards.scryfall.io` (a 1×1 PNG, with `/large/` delayed 300ms), loads `/?mock=images`, and submits a question. It resolves once the answer has rendered.

- [ ] **Step 1: Install.** From `mtg-web/`, run `npm install --save-dev @playwright/test && npx playwright install chromium`.

- [ ] **Step 2: Add the script.** In `package.json` `scripts`, add after `"test"`:

```json
    "test:e2e": "playwright test"
```

- [ ] **Step 3: Write the config.** Create `playwright.config.ts`:

```ts
import { defineConfig, devices } from '@playwright/test';

// Runs against `vite dev` and the `images` fixture (/?mock=images), so no
// backend is needed. Port 5174 so a normal `npm run dev` can stay up.
const PORT = 5174;

export default defineConfig({
  testDir: 'e2e',
  forbidOnly: !!process.env.CI,
  reporter: process.env.CI ? [['github'], ['html', { open: 'never' }]] : 'list',
  use: { baseURL: `http://localhost:${PORT}`, trace: 'retain-on-failure' },
  webServer: {
    command: `npm run dev -- --port ${PORT} --strictPort`,
    url: `http://localhost:${PORT}`,
    reuseExistingServer: !process.env.CI
  },
  projects: [
    {
      name: 'desktop',
      testMatch: /\.desktop\.spec\.ts$/,
      use: { ...devices['Desktop Chrome'], viewport: { width: 1280, height: 800 } }
    },
    { name: 'phone', testMatch: /\.phone\.spec\.ts$/, use: { ...devices['Pixel 7'] } }
  ]
});
```

- [ ] **Step 4: Write the helper.** Create `e2e/helpers.ts`:

```ts
import { expect, type Page } from '@playwright/test';

// 1×1 PNG standing in for every Scryfall image, so no test needs the network.
const PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==',
  'base64'
);

export async function askFixture(page: Page): Promise<void> {
  // No backend: meta and admin status fall back to their defaults.
  await page.route('**/api/v1/**', (route) => route.fulfill({ status: 503, body: '' }));
  await page.route('https://cards.scryfall.io/**', async (route) => {
    // Delay `large` so the zoom visibly starts on `normal`.
    if (route.request().url().includes('/large/')) await new Promise((r) => setTimeout(r, 300));
    await route.fulfill({ status: 200, contentType: 'image/png', body: PNG });
  });
  await page.goto('/?mock=images');
  const box = page.getByRole('textbox');
  await box.fill('Does trample plus deathtouch only need 1 damage on each blocker?');
  await box.press('Enter');
  await expect(page.locator('#source-4')).toBeVisible();
}
```

- [ ] **Step 5: Write a smoke test.** Create `e2e/smoke.desktop.spec.ts`:

```ts
import { expect, test } from '@playwright/test';
import { askFixture } from './helpers';

test('the images fixture renders the cited cards', async ({ page }) => {
  await askFixture(page);
  // By selector, not role: once the art is inside a button (Task 5), ARIA
  // makes the image presentational.
  await expect(page.locator('#source-4 img')).toBeVisible();
});
```

- [ ] **Step 6: Run it.** From `mtg-web/`, run `npm run test:e2e`. Expected: 1 passed (desktop). If `askFixture` times out, the page may not be rendering `#source-4` at desktop width. Check the trace with `npx playwright show-trace test-results/**/trace.zip` before changing anything.

- [ ] **Step 7: Ignore the outputs.** Append to `mtg-web/.gitignore`:

```
test-results/
playwright-report/
```

- [ ] **Step 8: Add to CI.** In `.github/workflows/frontend-tests.yml`, rename the job's `name:` from `Build` to `Test and Build`. Insert this after the `Unit tests` step:

```yaml
      # `install` skips the download when the cached browser matches.
      - name: Cache Playwright browsers
        uses: actions/cache@v4
        with:
          path: ~/.cache/ms-playwright
          key: playwright-${{ hashFiles('mtg-web/package-lock.json') }}

      - name: Install Playwright
        run: npx playwright install --with-deps chromium

      - name: End-to-end tests
        run: npm run test:e2e

      - name: Upload Playwright report
        if: failure()
        uses: actions/upload-artifact@v4
        with:
          name: playwright-report
          path: mtg-web/playwright-report
          retention-days: 7
```

- [ ] **Step 9: Check that vitest and svelte-check still pass.** From `mtg-web/`, run `npm test && npm run check`. Expected: pass. vitest's `include` is `src/**/*.test.ts`, so it doesn't pick up `e2e/`.

- [ ] **Step 10: Commit.**

```bash
git add mtg-web/package.json mtg-web/package-lock.json mtg-web/playwright.config.ts mtg-web/e2e mtg-web/.gitignore .github/workflows/frontend-tests.yml
git commit -m "Add Playwright end-to-end tests to the frontend"
```

---

### Task 5: Hover preview and zoom on desktop

**Files:**
- Create: `mtg-web/src/app.d.ts`
- Create: `mtg-web/src/lib/overlays.ts`
- Create: `mtg-web/src/lib/desk/CardZoom.svelte`
- Create: `mtg-web/src/lib/desk/CardArt.svelte`
- Modify: `mtg-web/src/routes/+layout.svelte`
- Modify: `mtg-web/src/lib/desk/EvidenceCard.svelte:21-42` (the `art` snippet) and `:83` (full-layout render)
- Modify: `mtg-web/src/routes/+page.svelte` (window Escape guard)
- Test: `mtg-web/e2e/zoom.desktop.spec.ts`

**Interfaces:**
- Consumes: `placePreview`, `PREVIEW_SIZE` (Task 3), `askFixture` (Task 4), `CardDetails.image_large` (Task 2).
- Produces:
  - `overlays.ts`: `interface ZoomedCard { name: string; normal: string; large: string | null }`, `openZoom(card: ZoomedCard): void`, `closeZoom(): void`, `openSheet(): void`, `closeSheet(): void`.
  - `App.PageState { sheet?: true; zoom?: ZoomedCard }`.
  - `CardArt.svelte` props: `{ name: string; thumb: string; normal: string; large?: string | null; width: number; height: number; class?: string }`.
  - The zoom is `role=dialog` named after the card. The preview has `data-testid="card-preview"`.

- [ ] **Step 1: Write the failing end-to-end tests.** Create `e2e/zoom.desktop.spec.ts`:

```ts
import { expect, test } from '@playwright/test';
import { askFixture } from './helpers';

test.beforeEach(async ({ page }) => {
  await askFixture(page);
});

const art = (page: import('@playwright/test').Page) =>
  page.getByRole('button', { name: 'Enlarge Colossal Dreadmaw' });
const zoom = (page: import('@playwright/test').Page) =>
  page.getByRole('dialog', { name: 'Colossal Dreadmaw', exact: true });

test('hovering art shows a preview inside the viewport', async ({ page }) => {
  await art(page).hover();
  const preview = page.getByTestId('card-preview');
  await expect(preview).toBeVisible();
  await expect(preview).toBeInViewport({ ratio: 1 });
  await page.mouse.move(5, 5);
  await expect(preview).toBeHidden();
});

test('clicking art zooms, hides the preview, then swaps in the large image', async ({ page }) => {
  await art(page).hover();
  await expect(page.getByTestId('card-preview')).toBeVisible();
  await art(page).click();
  await expect(zoom(page)).toBeVisible();
  await expect(page.getByTestId('card-preview')).toBeHidden();
  await expect(zoom(page).getByRole('img')).toHaveAttribute('src', /\/large\//);
});

test('Escape closes the zoom and returns focus to the art', async ({ page }) => {
  await art(page).click();
  await expect(zoom(page)).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(zoom(page)).toBeHidden();
  await expect(art(page)).toBeFocused();
});

test('keyboard opens the zoom', async ({ page }) => {
  await art(page).focus();
  await page.keyboard.press('Enter');
  await expect(zoom(page)).toBeVisible();
  await expect(page.getByTestId('card-preview')).toHaveCount(0);
});

test('a click anywhere closes the zoom', async ({ page }) => {
  await art(page).click();
  await page.mouse.click(10, 10);
  await expect(zoom(page)).toBeHidden();
});

test('back closes the zoom and stays on the page', async ({ page }) => {
  await art(page).click();
  await expect(zoom(page)).toBeVisible();
  await page.goBack();
  await expect(zoom(page)).toBeHidden();
  await expect(art(page)).toBeVisible();
  await expect(page).toHaveURL(/mock=images/);
});

test('a card without art has no zoom', async ({ page }) => {
  await expect(page.locator('#source-3')).toContainText('Basilisk Collar');
  await expect(page.getByRole('button', { name: 'Enlarge Basilisk Collar' })).toHaveCount(0);
});
```

- [ ] **Step 2: Run them and confirm they fail.** From `mtg-web/`, run `npx playwright test zoom.desktop`. Expected: every test except "a card without art" fails, because there is no `Enlarge Colossal Dreadmaw` button.

- [ ] **Step 3: Add page-state typing.** Create `src/app.d.ts`:

```ts
import type { ZoomedCard } from '$lib/overlays';

declare global {
  namespace App {
    // Shallow-routing state (see $lib/overlays): each open overlay is a
    // history entry, so the browser's back button closes it.
    interface PageState {
      sheet?: true;
      zoom?: ZoomedCard;
    }
  }
}

export {};
```

- [ ] **Step 4: Add the overlay state.** Create `src/lib/overlays.ts`:

```ts
import { pushState } from '$app/navigation';
import { page } from '$app/state';

// The source sheet and card zoom are shallow-routing history entries, so
// the browser's back button closes them in the order they opened. Closing
// from the UI goes back too, which keeps one exit path. page.state starts
// empty on a fresh load, so a reload never reopens either.

export interface ZoomedCard {
  name: string;
  normal: string;
  large: string | null;
}

export function openZoom(card: ZoomedCard): void {
  // Keep `sheet`, so back from a zoom returns to the open sheet.
  pushState('', { ...page.state, zoom: card });
}

export function closeZoom(): void {
  if (page.state.zoom) history.back();
}

export function openSheet(): void {
  pushState('', { sheet: true });
}

export function closeSheet(): void {
  if (page.state.sheet) history.back();
}
```

- [ ] **Step 5: Add the zoom dialog.** Create `src/lib/desk/CardZoom.svelte`:

```svelte
<script lang="ts">
  import { page } from '$app/state';
  import { closeZoom } from '$lib/overlays';

  let dialog: HTMLDialogElement;
  let opener: HTMLElement | null = null;
  let src = $state('');
  const zoom = $derived(page.state.zoom);

  $effect(() => {
    if (zoom && !dialog.open) {
      opener = document.activeElement as HTMLElement | null;
      dialog.showModal();
    } else if (!zoom && dialog.open) {
      dialog.close();
    }
  });

  // `normal` first (usually cached by the hover preview), `large` once it
  // has loaded. A failed `large` leaves `normal` up.
  $effect(() => {
    if (!zoom) return;
    src = zoom.normal;
    const large = zoom.large;
    if (!large) return;
    let live = true;
    const img = new Image();
    img.onload = () => live && (src = large);
    img.src = large;
    return () => {
      live = false;
    };
  });

  // Escape closes the dialog natively; going back drops the history entry.
  // After back has already cleared page.state, closeZoom does nothing.
  function onclose() {
    closeZoom();
    opener?.focus();
  }
</script>

<!-- Any click closes it; keyboard users have Escape (native). -->
<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_noninteractive_element_interactions -->
<dialog
  bind:this={dialog}
  {onclose}
  onclick={closeZoom}
  aria-label={zoom?.name ?? 'Card'}
  class="m-auto max-h-none max-w-none cursor-zoom-out border-0 bg-transparent p-0 backdrop:bg-scrim"
>
  {#if zoom}
    <img
      {src}
      alt={zoom.name}
      class="block h-auto rounded-[4.75%/3.5%]"
      style:width="min(92vw, calc(90dvh * 488 / 680))"
      style:aspect-ratio="488 / 680"
    />
  {/if}
</dialog>
```

- [ ] **Step 6: Mount it once.** In `src/routes/+layout.svelte`, add `import CardZoom from '$lib/desk/CardZoom.svelte';` after the `refreshAdmin` import. Add `<CardZoom />` after `{@render children()}`.

- [ ] **Step 7: Add the art button.** Create `src/lib/desk/CardArt.svelte`:

```svelte
<script lang="ts">
  import { MediaQuery } from 'svelte/reactivity';
  import { PREVIEW_SIZE, placePreview } from '$lib/cardPreview';
  import { openZoom } from '$lib/overlays';

  // Card art that zooms when clicked and, with a mouse, previews larger on
  // hover. `thumb` is what shows at `width`×`height`; `normal` feeds the
  // preview and the zoom, which swaps in `large` when there is one.
  let {
    name,
    thumb,
    normal,
    large = null,
    width,
    height,
    class: className = ''
  }: {
    name: string;
    thumb: string;
    normal: string;
    large?: string | null;
    width: number;
    height: number;
    class?: string;
  } = $props();

  const HOVER_DELAY_MS = 150;
  const finePointer = new MediaQuery('(hover: hover) and (pointer: fine)');

  let button: HTMLButtonElement;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let spot = $state<{ left: number; top: number } | null>(null);

  function show() {
    const viewport = { width: window.innerWidth, height: window.innerHeight };
    spot = placePreview(button.getBoundingClientRect(), viewport, PREVIEW_SIZE, 12);
  }

  function hide() {
    clearTimeout(timer);
    spot = null;
  }

  function onpointerenter(event: PointerEvent) {
    if (!finePointer.current || event.pointerType !== 'mouse') return;
    clearTimeout(timer);
    timer = setTimeout(show, HOVER_DELAY_MS);
  }

  function onclick(event: MouseEvent) {
    // Inside the sheet's clickable rows, the art zooms instead.
    event.stopPropagation();
    hide();
    openZoom({ name, normal, large });
  }

  // Any scroll, including the evidence panel's own, moves the art away
  // from a fixed preview.
  $effect(() => {
    if (!spot) return;
    window.addEventListener('scroll', hide, { capture: true, passive: true });
    return () => window.removeEventListener('scroll', hide, { capture: true });
  });

  $effect(() => () => clearTimeout(timer));
</script>

<button
  bind:this={button}
  type="button"
  aria-label="Enlarge {name}"
  {onclick}
  {onpointerenter}
  onpointerleave={hide}
  class={['shrink-0 cursor-zoom-in border-0 bg-transparent p-0', className]}
  style:width="{width}px"
  style:height="{height}px"
>
  <img
    src={thumb}
    alt=""
    {width}
    {height}
    loading="lazy"
    decoding="async"
    class="block size-full rounded-[inherit]"
  />
</button>
{#if spot}
  <img
    data-testid="card-preview"
    src={normal}
    alt=""
    class="pointer-events-none fixed z-30 rounded-[4.75%/3.5%] shadow-[0_8px_24px_rgb(0_0_0/0.4)]"
    style:left="{spot.left}px"
    style:top="{spot.top}px"
    style:width="{PREVIEW_SIZE.width}px"
    style:height="{PREVIEW_SIZE.height}px"
  />
{/if}
```

- [ ] **Step 8: Use it in `EvidenceCard`.** The `art` snippet serves both layouts. Only the full layout zooms, so give the snippet a `zoomable` flag. Replace the snippet (lines 21–42) with:

```svelte
{#snippet art(w: number, h: number, zoomable: boolean)}
  {#if zoomable && item.card?.image_normal}
    <CardArt
      {name}
      thumb={item.card.image_small ?? item.card.image_normal}
      normal={item.card.image_normal}
      large={item.card.image_large}
      width={w}
      height={h}
      class="rounded-md"
    />
  {:else if item.card?.image_small}
    <img
      src={item.card.image_small}
      alt={name}
      width={w}
      height={h}
      loading="lazy"
      decoding="async"
      class="shrink-0 rounded-md"
      style:width="{w}px"
      style:height="{h}px"
    />
  {:else}
    <span
      aria-hidden="true"
      class="flex shrink-0 items-center justify-center rounded-md border border-line-muted bg-art font-mono text-[10px] text-fg-muted"
      style:width="{w}px"
      style:height="{h}px">art</span
    >
  {/if}
{/snippet}
```

Change the compact call `{@render art(52, 72)}` to `{@render art(52, 72, false)}`, and the full-layout call `{@render art(96, 134)}` to `{@render art(96, 134, true)}`. Add `import CardArt from './CardArt.svelte';` after the `Icon` import.

- [ ] **Step 9: Guard the page's Escape handler.** In `src/routes/+page.svelte`, replace `onWindowKey` with the version below. Escape keydown reaches the window before an open dialog closes, so without the guard, closing a zoom would also clear the selected citation.

```ts
  function onWindowKey(event: KeyboardEvent) {
    // Escape that closes a sheet or zoom shouldn't also clear the selection.
    if (event.key === 'Escape' && !sheetOpen && !page.state.zoom) selection = null;
  }
```

- [ ] **Step 10: Run the end-to-end tests and confirm they pass.** From `mtg-web/`, run `npx playwright test zoom.desktop`. Expected: 7 passed. Then run `npm test && npm run check && npm run test:e2e`. Expected: all pass, with 0 svelte-check errors or warnings.

- [ ] **Step 11: Commit.**

```bash
git add mtg-web/src/app.d.ts mtg-web/src/lib/overlays.ts mtg-web/src/lib/desk/CardZoom.svelte mtg-web/src/lib/desk/CardArt.svelte mtg-web/src/routes/+layout.svelte mtg-web/src/lib/desk/EvidenceCard.svelte mtg-web/src/routes/+page.svelte mtg-web/e2e/zoom.desktop.spec.ts
git commit -m "Preview card art on hover and zoom it on click"
```

---

### Task 6: Source sheet zoom and back button (phone)

**Files:**
- Modify: `mtg-web/src/lib/desk/SourceSheet.svelte` (props, open/close effect, card art at ~96–111, ruling row at ~149–175)
- Modify: `mtg-web/src/routes/+page.svelte` (sheet state)
- Test: `mtg-web/e2e/zoom.phone.spec.ts`

**Interfaces:**
- Consumes: `CardArt` props, `openSheet()` / `closeSheet()` and `page.state.sheet` (Task 5), `askFixture` (Task 4).
- Produces: `SourceSheet` props become `{ items; index?: $bindable; open: boolean; onclose: () => void; onchange? }`, and `open` is no longer bindable.

- [ ] **Step 1: Write the failing end-to-end tests.** Create `e2e/zoom.phone.spec.ts`:

```ts
import { expect, test, type Page } from '@playwright/test';
import { askFixture } from './helpers';

test.beforeEach(async ({ page }) => {
  await askFixture(page);
});

const sheet = (page: Page, n: number) => page.getByRole('dialog', { name: new RegExp(`^Source ${n}:`) });
const zoom = (page: Page, name: string) => page.getByRole('dialog', { name, exact: true });

test('a compact row opens the sheet, not a zoom', async ({ page }) => {
  await page.locator('#source-4').tap();
  await expect(sheet(page, 4)).toBeVisible();
  await expect(zoom(page, 'Colossal Dreadmaw')).toBeHidden();
});

test('sheet art zooms above the sheet; back closes zoom, then sheet', async ({ page }) => {
  await page.locator('#source-4').tap();
  await sheet(page, 4).getByRole('button', { name: 'Enlarge Colossal Dreadmaw' }).tap();
  await expect(zoom(page, 'Colossal Dreadmaw')).toBeVisible();
  await expect(page.getByTestId('card-preview')).toHaveCount(0);

  await page.goBack();
  await expect(zoom(page, 'Colossal Dreadmaw')).toBeHidden();
  await expect(sheet(page, 4)).toBeVisible();

  await page.goBack();
  await expect(sheet(page, 4)).toBeHidden();
  await expect(page.locator('#source-4')).toBeVisible();
  await expect(page).toHaveURL(/mock=images/);
});

test('tapping the zoom returns to the sheet', async ({ page }) => {
  await page.locator('#source-4').tap();
  await sheet(page, 4).getByRole('button', { name: 'Enlarge Colossal Dreadmaw' }).tap();
  await zoom(page, 'Colossal Dreadmaw').tap();
  await expect(zoom(page, 'Colossal Dreadmaw')).toBeHidden();
  await expect(sheet(page, 4)).toBeVisible();
});

test('the Close button closes the sheet and leaves no history entry', async ({ page }) => {
  await page.locator('#source-4').tap();
  await sheet(page, 4).getByRole('button', { name: 'Close' }).tap();
  await expect(sheet(page, 4)).toBeHidden();
  await expect(page).toHaveURL(/mock=images/);
  // Asking doesn't navigate, so with the sheet's entry gone, back leaves
  // the app entirely (to the blank page before goto).
  await page.goBack();
  await expect(page).not.toHaveURL(/mock=images/);
});

test('ruling art zooms; the rest of the row links to Scryfall', async ({ page }) => {
  await page.locator('#source-5').tap();
  const ruling = sheet(page, 5);
  await ruling.getByRole('button', { name: 'Enlarge Windswift Slice' }).tap();
  await expect(zoom(page, 'Windswift Slice')).toBeVisible();
  await zoom(page, 'Windswift Slice').tap();
  await expect(ruling).toBeVisible();
  await expect(ruling.getByRole('link', { name: /From the card/ })).toHaveAttribute(
    'href',
    /scryfall\.com/
  );
});
```

- [ ] **Step 2: Run them and confirm they fail.** From `mtg-web/`, run `npx playwright test zoom.phone`. Expected: the compact-row test passes. The others fail: there's no `Enlarge` button in the sheet, and back leaves the page while the sheet is open.

- [ ] **Step 3: Drive `SourceSheet` from props.** In `SourceSheet.svelte`:

Replace the props block with:

```ts
  let {
    items,
    index = $bindable(0),
    open,
    onclose,
    onchange
  }: {
    items: EvidenceItem[];
    index?: number;
    open: boolean;
    onclose: () => void;
    onchange?: (item: EvidenceItem) => void;
  } = $props();
```

Add `import CardArt from './CardArt.svelte';` after the `Icon` import.

Replace the `onclose` function with the function below, and rename the dialog's `{onclose}` attribute to `onclose={ondialogclose}`:

```ts
  // Escape closes the dialog natively while `open` is still true; a close
  // driven by `open` (back, or onclose) arrives with it already false.
  function ondialogclose() {
    if (open) onclose();
    opener?.focus();
  }
```

Change the dialog's `onclick` to `onclick={(e) => e.target === dialog && onclose()}`. Change the Close button's `onclick` to `onclick={onclose}`.

- [ ] **Step 4: Make the sheet's card art zoomable.** Replace the card view's `{#if item.card?.image_normal} <img …/>` branch, keeping the `{:else}` placeholder, with:

```svelte
          {#if item.card?.image_normal}
            <CardArt
              name={item.card.name}
              thumb={item.card.image_normal}
              normal={item.card.image_normal}
              large={item.card.image_large}
              width={150}
              height={209}
              class="rounded-lg"
            />
```

- [ ] **Step 5: Split the ruling row.** A button can't sit inside a link. Replace the whole `<a href={item.url} …> … </a>` "From the card" row with:

```svelte
          <div class="flex items-center gap-3 rounded-[10px] border border-line px-2.5 py-2">
            {#if item.card?.image_normal}
              <CardArt
                name={item.card.name}
                thumb={item.card.image_small ?? item.card.image_normal}
                normal={item.card.image_normal}
                large={item.card.image_large}
                width={40}
                height={56}
                class="rounded"
              />
            {:else}
              <span
                aria-hidden="true"
                class="flex h-14 w-10 shrink-0 items-center justify-center rounded border border-line-muted bg-art font-mono text-[8px] text-fg-muted"
                >art</span
              >
            {/if}
            <a
              href={item.url}
              {...external}
              class="flex min-w-0 flex-1 items-center gap-3 self-stretch text-fg no-underline"
            >
              <span class="flex min-w-0 flex-1 flex-col gap-0.5">
                <span class="text-xs text-fg-muted">From the card</span>
                <span class="text-[15px] font-semibold">{item.cardName}</span>
              </span>
              <Icon name="chevron-right" class="text-fg-muted" />
            </a>
          </div>
```

- [ ] **Step 6: Put the sheet in history in `+page.svelte`.**
  - Add `import { closeSheet, openSheet as pushSheet } from '$lib/overlays';`.
  - Delete `let sheetOpen = $state(false);` and add `const sheetOpen = $derived(!!page.state.sheet);`.
  - In `ask()`, delete the `sheetOpen = false;` line. The modal sheet blocks the form, so `ask()` never runs while it's open.
  - In `openSheet`, replace `sheetOpen = true;` with `if (!page.state.sheet) pushSheet();`.
  - In the `<SourceSheet>` usage, replace `bind:open={sheetOpen}` with `open={sheetOpen}` and `onclose={closeSheet}`.

- [ ] **Step 7: Run everything.** From `mtg-web/`, run `npx playwright test zoom.phone`. Expected: 5 passed. Then run `npm test && npm run check && npm run test:e2e`. Expected: all pass, with 0 svelte-check errors or warnings.

- [ ] **Step 8: Commit.**

```bash
git add mtg-web/src/lib/desk/SourceSheet.svelte mtg-web/src/routes/+page.svelte mtg-web/e2e/zoom.phone.spec.ts
git commit -m "Zoom card art from the source sheet and close the sheet with back"
```

---

### Task 7: Manual check in the browser

- [ ] **Step 1:** From `mtg-web/`, run `npm run dev` and open `http://localhost:5173/?mock=images`, then ask any question. Real Scryfall images load here.
- [ ] **Step 2:** At desk width (≥1100px), check that hovering Dreadmaw's art shows the preview after a short delay, and that it flips left near the right edge. Scroll the evidence panel while the preview shows, and check the preview hides. Click the art and check the zoom sharpens when `large` arrives. Check that Esc, a click and back each close it.
- [ ] **Step 3:** At tablet width (640–1099px, grid layout), repeat the hover and zoom checks.
- [ ] **Step 4:** With DevTools phone emulation (touch on), check that a row tap opens the sheet, that tapping the art zooms, and that back, back closes the zoom and then the sheet.
- [ ] **Step 5:** On `/history` as admin, if available, check that zoom works on old entries, which lack `image_large`. They should stay on `normal`.
- [ ] **Step 6 (user reminder):** Look at the 96px thumbnails: do they look soft on `image_small`? If so, switching them to `image_normal` is a small follow-up.
