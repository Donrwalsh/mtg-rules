# Judge's Desk Secondary Pages Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give `/rules`, `/rules/[id]`, `/history`, `/login`, `/admin/usage` and the phone menu their full Judge's Desk designs, as approved on the design canvas.

**Architecture:** Frontend only, with no API changes. Small pure modules (`rules.ts`, `ruleText.ts`, `usageChart.ts`, plus a date helper) hold the logic and are unit-tested. The pages are Svelte 5 route components styled with PR 1's tokens, reusing PR 3's `AnswerBody`, `EvidenceCard` and `format.ts`. `AppHeader` gains a current-page marker and a phone dropdown menu.

**Tech Stack:** SvelteKit 2 + Svelte 5 (runes, `$app/state`, `svelte/reactivity` `MediaQuery`), Tailwind v4 tokens, Vitest.

**Spec:** [docs/superpowers/specs/2026-09-29-redesign-secondary-pages-design.md](../specs/2026-09-29-redesign-secondary-pages-design.md)

**Design canvas:** https://claude.ai/artifact/XzoXtd9SdWQsQvPr2cF6G6, section "Other pages" (version 11).

## Global Constraints

- Branch `feature/redesign-secondary-pages`, stacked on `feature/judge-desk-search` with `docs/frontend-redesign-plans` merged in, until PRs 1–3 and #10 merge.
- Colours only via tokens; no hex in `.svelte` files. Breakpoints: `sm` 640px, `desk` 1100px.
- Tap targets ≥ 44px on phone. Real `<a>` and `<button>` elements; `aria-expanded` on disclosure buttons; `aria-current` for current page or rule.
- Rule text is rendered as text parts, never `{@html}`.
- Admin pages stay admin-only (the API enforces it; the UI hides links).
- Commits: conventional prefixes, ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

---

## Phase 1: Boards (done)

- [x] **Task 1:** Drew 12 boards on the canvas: rules contents (desktop and phone), rule detail (desktop and phone), rule not found, history, log in (default, wrong password, phone), usage, and the phone menu (public and admin). Published as version 11.
- [x] **Task 2:** User approved the boards on 2026-09-29. The spec's "Decisions" section and this plan's Phase 2 were written from them.

---

## Phase 2: Build

## File Structure

- `mtg-web/src/lib/rules.ts` (+ test): `CR_SECTIONS`, `normalizeRuleId`, `entryIdOf`, `isTopLevel`, `sectionOf`, `neighbours`, `windowAround`.
- `mtg-web/src/lib/ruleText.ts` (+ test): splits rule text into text and rule-reference parts.
- `mtg-web/src/lib/usageChart.ts` (+ test): `chartScale`, `barHeight`.
- `mtg-web/src/lib/format.ts` (+ test): gains `historyTime`.
- `mtg-web/src/lib/AppHeader.svelte`: current-page marker and phone menu.
- `mtg-web/src/lib/pages/GoToRule.svelte`, `mtg-web/src/lib/pages/RuleText.svelte`, `mtg-web/src/lib/pages/SpendChart.svelte`.
- Routes: `src/routes/rules/+page.svelte` (new), `src/routes/rules/[id]/+page.svelte`, `src/routes/history/+page.svelte`, `src/routes/login/+page.svelte`, `src/routes/admin/usage/+page.svelte`.
- Delete: `src/lib/CitedAnswer.svelte`, `src/lib/SourcesList.svelte`.

### Task 3: Logic modules

**Files:** Create `rules.ts`, `rules.test.ts`, `ruleText.ts`, `ruleText.test.ts`, `usageChart.ts`, `usageChart.test.ts`. Modify `format.ts` and `format.test.ts`.

**Interfaces (produces):**

```ts
// rules.ts
export const CR_SECTIONS: Record<number, string>;
export function normalizeRuleId(input: string): string;   // " 702.19B. " -> "702.19b"
export function entryIdOf(ruleId: string): string;         // 702.2c -> 702.2; 702.2 -> 702.2; 702 -> 702
export function isTopLevel(ruleId: string): boolean;        // /^\d{3}$/
export function sectionOf(ruleId: string): number | null;  // 702.2c -> 7
export function neighbours<T extends { rule_id: string }>(list: T[], id: string): { prev: T | null; next: T | null };
export function windowAround<T extends { rule_id: string }>(list: T[], id: string, size?: number): T[];
// ruleText.ts
export type TextPart = { kind: 'text'; text: string } | { kind: 'rule'; text: string; ruleId: string };
export function splitRuleText(text: string): TextPart[];
// usageChart.ts
export function chartScale(values: number[], budget: number): { max: number; ticks: number[] };
export function barHeight(value: number, max: number, height: number): number;
// format.ts
export function historyTime(iso: string, locale?: string, timeZone?: string): string; // "Sep 29 · 14:02"
```

Rule-reference rules for `splitRuleText`:
- Dotted ids (`702.19b`) always link, with the same guard against prices, dotted dates and versions as `segments.ts`.
- A bare three-digit number links only when it follows "rule " or "rules " ("See rule 704."), so "100 cards" stays text.

- [ ] **Step 1: Write the failing tests** (the cases above plus: neighbours at both ends, a window clamped at the start and end, the scale when every day is under budget and when one day is over it, `historyTime` in UTC).
- [ ] **Step 2:** `npm test`, and confirm they fail.
- [ ] **Step 3: Implement.** Key regex for `ruleText.ts`: `/(?<![\d.$€£])(\d{3}\.\d+[a-z]?)(?![a-z\d]|\.\d)|(?<=\brules?\s)(\d{3})(?![\d.])/g`.
- [ ] **Step 4:** `npm test && npm run check`, then commit `feat: rule, rule-text and usage-chart helpers`.

### Task 4: Header current page and phone menu

**Files:** Modify `src/lib/AppHeader.svelte`.

- **sm and up:** the nav as today, with Rules pointing to `/rules`. The link for `page.url.pathname` gets `aria-current="page"` and the class `border-b-2 border-gold pb-1 text-fg`.
  - Rules is current for `/rules` and `/rules/*`.
- **Below sm:** a ☰/✕ button (44×44, `aria-expanded`, `aria-controls="site-menu"`, `aria-label` "Menu" or "Close menu").
  - The panel is `<nav id="site-menu" aria-label="Main">`, absolutely positioned under the header, full width, with a `panel` background, `rounded-b-2xl` corners, a `line-strong` bottom border and `z-30`.
  - Behind it, a `fixed inset-0 top-[header] bg-scrim` button (`aria-label` "Close menu", `tabindex` -1) closes it on tap.
  - Items: Search (`/`), Rules (`/rules`); for admins an "ADMIN" label, History, Usage and a Log out button. Rows are 52px, and the current page has a `bg-chip` background.
  - Closes on Escape (focus back to the button), on scrim tap, and after navigation (`afterNavigate`).
- **Check:** at 390px the menu opens and closes, focus returns, and admin items appear only for admins. At 1280px the underline marks the current page.
- Commit `feat: phone menu and current-page marker in the header`.

### Task 5: `/rules` contents and `GoToRule`

**Files:** Create `src/lib/pages/GoToRule.svelte` and `src/routes/rules/+page.svelte`.

- **`GoToRule`:** props `{ width?: string }`. A form with a visible "Go to" label, a mono input (placeholder "e.g. 702.19b") and a `chip` "Go" button. On submit it runs `goto('/rules/' + normalizeRuleId(value))`, ignoring an empty value.
- **Page:**
  - Fetch `fetchRulesIndex()` on mount, with states for loading, error (`role="alert"`) and loaded.
  - Heading block as the spec describes.
  - `desk` and up: three columns (sections 1–3, 4–6, 7–9). `sm` up to `desk`: two columns split at 1–4 and 5–9. Below `sm`: accordions (section 1 open).
  - Rule rows are links to `/rules/{id}`.
- Commit `feat: rules table of contents`.

### Task 6: `/rules/[id]` detail and not-found

**Files:** Modify `src/routes/rules/[id]/+page.svelte`. Create `src/lib/pages/RuleText.svelte`, which renders `splitRuleText` parts, with rule parts as mono links.

- **Loading:**
  - `id = normalizeRuleId(page.params.id)`, `entry = entryIdOf(id)`.
  - Load `fetchRule(entry)`. When not top-level, also load `fetchRule(parentRule(entry))` for siblings.
  - On `NotFoundError`: when `parentRule(id)` exists, fetch it, and on success show the "Go to {parent} · {heading}" button.
  - Ignore stale responses, as today.
- **Render** per the spec:
  - Breadcrumbs from `ancestors`, with the section name from `CR_SECTIONS[sectionOf(id)]`.
  - The h1.
  - Subrule cards with `id="r{rule_id}"`. The card for `id` is highlighted and scrolled into view once loaded (`scrollIntoView({block: 'center'})`, instant under reduced motion).
  - A previous/next grid from `neighbours(parent.subrules, entry)`.
  - The desktop-only sidebar from `windowAround(parent.subrules, entry, 14)`.
  - Top-level rules list their entries instead of cards.
- **Check:** `/rules/702.2c`, `/rules/702.2`, `/rules/702`, `/rules/702.99z` and `/rules/100` at 1280 and 390, against the "Rule — detail", "Phone — rule detail" and "Rule — not found" boards.
- Commit `feat: rule detail with whole entry, neighbours and not-found`.

### Task 7: `/login`

**Files:** Modify `src/routes/login/+page.svelte`.

- The card per the spec. Keep the current submit logic, and use `aria-invalid` plus a `danger-line` border on error.
- Commit `feat: login card`.

### Task 8: `/history`

**Files:** Modify `src/routes/history/+page.svelte`. Delete `src/lib/CitedAnswer.svelte` and `src/lib/SourcesList.svelte`.

- **Rows:** a card list per the spec.
  - `historyTime(row.created_at)`.
  - The first line of the answer (`row.answer?.split('\n')[0]`), or "(no answer)".
  - Chips from `row.error`, `row.citation_stats?.uncited_answer`, `row.cached` and `row.citations?.length`.
- **Expanded row:**
  - `AnswerBody` with local `selection` state, `canHover` from `MediaQuery('hover: hover')`, and `ruleReferences` from `row.rule_references ?? []`.
  - A grid of `EvidenceCard`s from `(row.citations ?? []).map(fromCitation)`.
  - A footer with "Also retrieved · {uncitedItems(row.results).length}", the model, and a `<details>` "Raw results".
- **Paging** as today, with "Page n".
- **Check:** `grep -r "CitedAnswer\|SourcesList" src` returns nothing.
- Commit `feat: history list with expandable answers and sources`.

### Task 9: `/admin/usage` and `SpendChart`

**Files:** Create `src/lib/pages/SpendChart.svelte`. Modify `src/routes/admin/usage/+page.svelte`.

- **`SpendChart`:** props `{ days: UsageDay[]; budget: number }`.
  - A plain-HTML chart: absolutely positioned bars (`bg-gold rounded-t`, 2px gap between neighbours), gridlines from `chartScale().ticks` (the budget line dashed), mono y labels and date x labels.
  - Only today's bar has a value label. Each bar has a `title` tooltip.
  - The wrapper has `role="img"` with an `aria-label` listing every day's spend.
  - Its width fills the container: bars are placed by percentage, not fixed pixels.
- **Page:** tiles, chart and tables per the spec. Tables sit in `overflow-x-auto` wrappers so the page never scrolls sideways at 390px.
- Commit `feat: usage dashboard with spend chart`.

### Task 10: Visual check, README and PR

- [ ] `npm run check && npm test && npm run build`.
- [ ] With the dev stack running, drive every page at 1280, 834 and 390 wide in Chrome (playwright-core with the installed Chrome) and compare against its board. Also check: no overflow, no page errors, the phone menu by keyboard and touch, "Go to rule", accordion toggling, rule highlight and scroll, and history expand and paging.
- [ ] README: describe the `/rules` contents, rule pages, history, usage and the phone menu in the `mtg-web/` row.
- [ ] Clean `npm ci` and `docker build .`, then push and open the PR (stacked; list PR 4's own commits).
