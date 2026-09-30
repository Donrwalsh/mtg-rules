# Frontend foundation: Svelte 5, Tailwind, Judge's Desk tokens

Status: design agreed, awaiting implementation
Date: 2026-09-29
Branch: `feature/frontend-foundation` (cut from `main`)
Part 1 of 4 of the Judge's Desk redesign. The others:
[API data](2026-09-29-redesign-api-data-design.md),
[search page](2026-09-29-judge-desk-search-design.md),
[secondary pages](2026-09-29-redesign-secondary-pages-design.md).

## Purpose

The Judge's Desk design (Claude Design canvas "MTG Rules — Judge's Desk",
https://claude.ai/artifact/XzoXtd9SdWQsQvPr2cF6G6) replaces the
prototype's unstyled pages with a dark, citation-first reading desk. This
first PR lays the ground every later PR builds on, with no new features:

- Move `mtg-web` to **Svelte 5** and rewrite every component in runes.
- Add **Tailwind CSS v4**, with the design's palette and type as theme
  tokens.
- **Self-host** the two typefaces.
- A shared **app header** (wordmark and nav).
- Move every existing page onto the new colours and type. This is not a
  redesign: layouts stay as they are and only the styling changes.
- A **Vitest** runner and `svelte-check`, both run in CI.

After this PR the site looks dark and uses the new type, and works exactly
as it does today.

## Decisions (from the design review)

- **Dark only.** No light mode. Every colour is a named token, so a light
  theme could be added later without touching components.
- **Tailwind v4**, configured in CSS (`@import "tailwindcss"` plus an
  `@theme` block in `src/app.css`) through the `@tailwindcss/vite` plugin.
  No `tailwind.config.js`. Tailwind's default palette is cleared
  (`--color-*: initial`) so only design tokens can be used.
- **Fonts:** `@fontsource/ibm-plex-sans` (400, 500, 600, 400 italic) and
  `@fontsource/jetbrains-mono` (400, 500), bundled into the static build.
  No request to Google Fonts.
- **Full runes migration:** `$state`, `$derived`, `$effect`, `$props`,
  snippets instead of slots, and `onclick` instead of `on:click`. The
  `isAdmin` store becomes a rune module (`src/lib/admin.svelte.ts`). No
  legacy-mode components are left.
- **Dependencies** go to their current majors: `svelte@5`,
  `@sveltejs/kit@2` (latest), `@sveltejs/vite-plugin-svelte` (latest),
  `vite` (the latest major that plugin supports), and
  `@sveltejs/adapter-static` (latest).
- **Checks:** Vitest for pure TypeScript logic, and `svelte-check` for
  types. There are no component tests. UI is checked by hand in the dev
  server against the design boards.

## Design tokens

The colours are taken straight from the boards. The token name becomes the
Tailwind utility suffix (`bg-page`, `text-fg-muted`, `border-line`).

| Token | Value | Used for |
|---|---|---|
| `page` | `#121418` | page background, header |
| `panel` | `#16191e` | evidence panel, quote wells |
| `well` | `#181b20` | "Rules referenced" box |
| `card` | `#1c1f25` | evidence cards, sheets |
| `field` | `#1b1e24` | text inputs |
| `chip` | `#23262d` | neutral chips, rule pills, close button |
| `skeleton` | `#1d2026` | loading bars |
| `art` | `#2c2c30` | card image placeholder |
| `line` | `#2a2e36` | dividers, card borders |
| `line-strong` | `#353a44` | input and outline-button borders |
| `line-muted` | `#4a505c` | citation marker outline, sheet grabber |
| `fg` | `#e7e5df` | primary text |
| `fg-body` | `#cfccc4` | answer and quote body text |
| `fg-soft` | `#a9aeb7` | secondary prose (loading line, subtitles) |
| `fg-muted` | `#8d929c` | labels, metadata |
| `fg-disabled` | `#5f6570` | disabled tabs and nav |
| `gold` | `#e7c170` | accent: wordmark, links, primary button, active marker |
| `gold-hover` | `#f3d796` | link hover, highlighted cited text |
| `gold-ink` | `#16181c` | text on gold |
| `gold-wash` | `#211e16` | active evidence card background |
| `gold-mark` | `#3a3320` | highlighted cited sentence background |
| `gold-off` | `#4a4332` | disabled primary button background |
| `gold-off-fg` | `#cbbd97` | disabled primary button text |
| `notice` | `#2a2416` | quota notice background |
| `notice-line` | `#5c4a1f` | quota notice border |
| `notice-fg` | `#d9cfb6` | quota notice body |
| `teal` | `#9fd3c7` | rulings, "sources cited" chip text |
| `teal-wash` | `#182224` | ruling card background |
| `teal-line` | `#2f4a47` | ruling card border |
| `teal-chip` | `#24323a` | "sources cited" chip background |
| `teal-mark` | `#1f3331` | highlighted sentence for a ruling citation |
| `teal-mark-fg` | `#bfe6dc` | text in that highlight |
| `paused` | `#b9c9e6` | site-paused icon |
| `paused-bg` | `#1b2230` | site-paused notice background |
| `paused-line` | `#34425c` | site-paused notice border |
| `paused-fg` | `#d6e1f5` | site-paused heading |
| `caution` | `#f0c28a` | uncited-answer icon and chip text |
| `caution-bg` | `#221c14` | uncited notice background |
| `caution-line` | `#4d3a22` | uncited notice border |
| `caution-chip` | `#3a2a18` | "No sources cited" chip background |
| `caution-fg` | `#e3cfae` | uncited notice body |
| `danger` | `#f0a3a3` | errors |
| `danger-line` | `#8a3a3a` | input border on error |
| `scrim` | `rgb(6 7 9 / 0.62)` | behind the phone bottom sheet |

- **Type:** `--font-sans: "IBM Plex Sans", system-ui, sans-serif` and
  `--font-mono: "JetBrains Mono", ui-monospace, monospace`.
- **Breakpoints:** Tailwind's `sm` (640px) marks phone vs tablet. A custom
  `desk` breakpoint at 1100px switches to the two-column desktop layout.
- **Radii:** 4px (chips), 6–7px (pills, buttons), 10px (cards, inputs),
  18px (the top corners of the bottom sheet).

Base styles in `app.css`: `body` is `bg-page text-fg font-sans`; links are
`text-gold` with `hover:text-gold-hover`; a visible focus ring
(`outline: 2px solid var(--color-gold); outline-offset: 2px`) on
`:focus-visible`; `color-scheme: dark`.

## App header

`src/lib/AppHeader.svelte` (Svelte 5, runes):

- The `mtg/rules` wordmark on the left, in mono 14px/500 gold, linking to `/`.
- An optional `center` snippet between the wordmark and the nav. The
  search page (PR 3) puts its search form there. Other pages pass nothing.
- Nav on the right, 14px:
  - Everyone sees **Rules**. Until PR 4 adds `/rules`, it links to
    `/rules/100`.
  - Admins also see **History**, **Usage** and **Log out**.
- A bottom border in `line`, 18px × 32px padding on desktop, 16px side
  padding on phone.
- On phone the nav wraps below the wordmark. The designed phone menu
  button comes in PR 4.

Each page renders `<AppHeader />` itself. A page can't pass a snippet up
to its layout, and the search page needs to put its form in the header.
`+layout.svelte` keeps the admin check and imports `app.css`. The current
admin-only nav bar goes away; `AppHeader` takes over its links.

## Porting existing pages

Each page keeps its structure and behaviour and swaps its inline styles
and `<style>` colours for token utilities:

- `/` (search): input and button styled as in the design (a `field`
  input, a gold "Search" button). Notices use the `notice` colours.
  Errors use `danger`.
- `CitedAnswer`, `SourcesList`: dark popover (`card` background, `line`
  border). Markers in mono.
- `/rules/[id]`, `/history`, `/login`, `/admin/usage`: tables use `line`
  borders and `fg-muted` headers, inputs match the search input, and
  the pages sit in a max-width content column.

## Tooling and CI

- `npm run check` runs `svelte-kit sync && svelte-check --tsconfig ./tsconfig.json`.
- `npm test` runs `vitest run`. The first test covers the existing
  `segments.ts`, so the runner is proven before PR 3 adds its logic.
- `.github/workflows/frontend-tests.yml` runs `npm run check` and
  `npm test` before the build.
- The Dockerfile keeps `node:22-slim`. Tailwind v4's native binary works
  there.

## Out of scope

- New layouts, the search-page redesign and its states (PR 3).
- API changes (PR 2).
- Designing `/rules`, `/history`, `/login`, `/admin/usage` and the phone
  menu (PR 4).

## Acceptance

- `npm run check`, `npm test`, `npm run build` and `docker build .` pass
  in `mtg-web`.
- No `.svelte` file uses `export let`, `$:`, `on:` directives, `<slot>`
  or `svelte/store`.
- Every page renders dark, in IBM Plex Sans and JetBrains Mono, loaded
  from the site's own origin. Check the network tab: no request to
  `fonts.googleapis.com`.
- Search, citations, popovers, rule pages, admin login, history and usage
  all work as they did before.
