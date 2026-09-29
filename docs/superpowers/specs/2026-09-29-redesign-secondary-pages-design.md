# Judge's Desk secondary pages

Status: **blocked on board review**. The boards below are drawn first
and reviewed; this spec is then finished from them.
Date: 2026-09-29
Branch: `feature/redesign-secondary-pages` (cut from `main` after PR 3 merges)
Part 4 of 4 of the Judge's Desk redesign. The others:
[foundation](2026-09-29-frontend-foundation-design.md),
[API data](2026-09-29-redesign-api-data-design.md),
[search page](2026-09-29-judge-desk-search-design.md).

## Purpose

Give the pages the original design didn't cover a proper Judge's Desk
design, not just PR 1's restyle. The pages are:

| Page | Today | Needs |
|---|---|---|
| `/rules` (new) | none; "Rules" links to `/rules/100` | table of contents from `GET /api/v1/rules` (PR 2) |
| `/rules/[id]` | breadcrumbs, text, subrules | reading layout for one rule and its subrules; not-found and loading states |
| `/history` (admin) | raw table with expandable rows | review past questions: answer, citations, cached flag, error, model |
| `/login` (admin) | bare password field | small centred form; wrong-password and rate-limited states |
| `/admin/usage` (admin) | tables of spend and outcomes | today against the budget, 7-day trend, top IP buckets |
| Phone menu | none | the ☰ button from the phone boards: Rules, plus History, Usage and Log out for admins |

## Step 1: boards (before any code)

Claude adds these artboards to the existing canvas
(https://claude.ai/artifact/XzoXtd9SdWQsQvPr2cF6G6), in a new row, using
the same tokens and type as the existing boards:

- Rules — contents (desktop 1280, phone 390)
- Rule — detail (desktop 1280, phone 390), rule not found (desktop)
- History — list and expanded row (desktop 1280)
- Log in — default and error (desktop 1280, phone 390)
- Usage (desktop 1280)
- Phone menu — open, as a public visitor and as an admin (390)

The user reviews them on the canvas. Changes are made there until they
are approved.

## Step 2: finish this spec

Once the boards are approved, replace this section with a decision list
and per-page layout like the search spec's. Settle these then:

- Should `/rules/[id]` show the whole section it belongs to (e.g. all of
  702.2 with 702.2c highlighted), or only the rule and its direct
  subrules as today?
- Is the phone menu a dropdown panel or a full-screen sheet?
- `/history`: keep offset paging (20 per page), or switch to "Load more"?
- `/admin/usage`: should the 7-day trend be a chart (dataviz rules apply)
  or stay a table?

## Known constraints

- `/history` and `/admin/usage` are admin-only. The API enforces it, and
  the UI hides the links.
- `/rules/[id]` is served by the SPA fallback (`prerender = false`).
  `/rules` can be prerendered as an empty shell that fetches on mount.
- Reuse PR 3's pieces where they fit: the evidence cards (history
  expanded rows), `CitedAnswer` (history), the rule preview, and the
  `notice` and `danger` styles.
