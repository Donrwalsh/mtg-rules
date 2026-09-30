# Judge's Desk Secondary Pages Implementation Plan

> **Status: blocked on board review.** Only Phase 1 can run now. Phase 2 is filled in (replacing the outline below) once the user approves the boards and the spec's "Step 2" questions are settled.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give `/rules`, `/rules/[id]`, `/history`, `/login`, `/admin/usage` and the phone menu full Judge's Desk designs, then build them.

**Architecture:** Design first, on the existing canvas, and wait for approval. Then build each page from PR 1's tokens and PR 3's components (evidence cards, previews, notices), fed by PR 2's `GET /api/v1/rules`.

**Tech Stack:** Claude Design canvas (Artifact tool); SvelteKit 2 + Svelte 5, Tailwind v4 tokens, Vitest.

**Spec:** [docs/superpowers/specs/2026-09-29-redesign-secondary-pages-design.md](../specs/2026-09-29-redesign-secondary-pages-design.md)

## Global Constraints

- Starts after PR 3 merges; branch `feature/redesign-secondary-pages` from `main`.
- Boards use exactly the tokens and type of the existing boards (IBM Plex Sans, JetBrains Mono, the PR 1 palette); desktop boards 1280 wide, phone 390.
- No code until the user approves the boards.
- Admin pages stay admin-only; the API enforces it and the UI hides the links.
- Commits: conventional prefixes, ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

---

## Phase 1: Boards (can run now)

### Task 1: Draw the secondary-page boards

**Canvas:** https://claude.ai/artifact/XzoXtd9SdWQsQvPr2cF6G6 (Design type; follow the type's instructions when editing: read `project/canvas.json`, add artboard files plus `boards` / `order` entries, publish with `url`, `root` and `files`).

- [ ] **Step 1:** Read `project/canvas.json` and one existing board (e.g. `project/StateEmpty.dc.html`) to reuse its header markup and styles exactly.
- [ ] **Step 2:** Add a third row, starting at y ≈ 3900, under a `title1` note "Other pages", with these artboards:
  - `RulesContents.dc.html` (1280) and `RulesContentsPhone.dc.html` (390): nine sections, each listing its three-digit rules as links; "as of {date}" line.
  - `RuleDetail.dc.html` (1280) and `RuleDetailPhone.dc.html` (390): breadcrumbs, rule id and heading, text, subrules; show 702.2c.
  - `RuleNotFound.dc.html` (1280).
  - `History.dc.html` (1280): list of past questions (query, answer preview, cached/error chips, model, time) with one row expanded to show the answer with markers and its evidence cards.
  - `Login.dc.html` (1280), `LoginPhone.dc.html` (390), `LoginError.dc.html` (1280).
  - `Usage.dc.html` (1280): today against the budget, the 7-day trend, outcome counts, top IP buckets.
  - `PhoneMenu.dc.html` and `PhoneMenuAdmin.dc.html` (390): the ☰ menu open.
- [ ] **Step 3:** Publish once (Artifact tool). Tell the user which boards were added and what was assumed. Do not verify by rendering unless asked.
- [ ] **Step 4:** Collect the user's feedback on the canvas and revise until they approve.

### Task 2: Finish the spec and this plan

- [ ] **Step 1:** In the spec, replace "Step 2: finish this spec" with the per-page decisions, layout and states from the approved boards, and answer its four open questions with the user.
- [ ] **Step 2:** Replace Phase 2 below with full tasks (files, interfaces, test code, commands), in the same form as the search-page plan.
- [ ] **Step 3:** Commit both docs on `feature/redesign-secondary-pages`.

---

## Phase 2: Build (outline, to be expanded in Task 2)

Expected tasks, in order. Each ends with `npm run check && npm test && npm run build` and a commit:

1. **Phone menu** in `AppHeader.svelte`: the ☰ button (44×44, `aria-expanded`, `aria-controls`), the panel from the approved board, Escape and outside click close it, focus returns to the button; point "Rules" at `/rules`.
2. **`/rules`**: prerendered shell that loads `fetchRulesIndex()`; sections and links; loading and error states.
3. **`/rules/[id]`**: the approved reading layout (reusing `RuleLink` previews for cross-references if the board shows them); not-found and loading states.
4. **`/login`**: the approved form, errors and the rate-limited message.
5. **`/history`**: the approved list and expanded row, reusing `AnswerBody` (read-only selection) and `EvidenceCard`; then remove `CitedAnswer.svelte` and `SourcesList.svelte` if nothing else uses them.
6. **`/admin/usage`**: the approved layout; if it includes a chart, load the `dataviz` skill before writing it.
7. **README and PR**.
