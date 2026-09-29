# Judge's Desk secondary pages

Status: boards approved 2026-09-29; design agreed, in implementation
Date: 2026-09-29
Branch: `feature/redesign-secondary-pages` (stacked on PR 3's
`feature/judge-desk-search` until PRs 1–3 merge)
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

The user approved the boards on 2026-09-29 (canvas version 11, section
"Other pages").

## Decisions

- **`/rules/[id]` shows the whole entry.** The entry is the `NNN.N`
  rule. A rule deeper than that (`702.2c`) opens its entry (`702.2`)
  with itself highlighted and scrolled into view. A top-level rule
  (`702`) shows its own entries as a link list.
- **Phone menu is a dropdown panel** under the header, over a scrim.
  It is not a full-screen sheet.
- **`/history` keeps offset paging**, 20 per page, as "← Newer / Older →".
- **`/admin/usage` has a 7-day spend bar chart** with the budget as a
  dashed line, and the tables stay underneath. The chart follows the
  dataviz rules: one series, no legend, a direct label on today's bar,
  and a text alternative.
- **A "Go to rule" box** on `/rules` and the not-found page. It accepts
  any rule id and goes to `/rules/{id}`, using the same normalisation
  as the API (trim, lower-case, drop a trailing `.`).

## Header and phone menu (`AppHeader`)

- **Desktop and tablet (sm and up):**
  - Nav links are Rules (`/rules`), then History, Usage and Log out for
    admins.
  - The current page gets `aria-current="page"`, `fg` text and a 2px
    gold underline.
- **Phone (below 640px):**
  - The nav is replaced by a 44×44 ☰ button (`aria-expanded`,
    `aria-controls="site-menu"`, label "Menu"). When the menu is open
    the button shows ✕ and reads "Close menu".
  - The panel sits under the header and is full width, with a `panel`
    background and 16px bottom corners. Behind it is a `scrim` over the
    page.
  - Items are 52px rows: Search (`/`) and Rules (`/rules`). Admins also
    get an "ADMIN" divider, History, Usage and Log out. The current page
    uses a `chip` background.
  - Escape, a tap on the scrim, or following a link closes the panel,
    and focus returns to the ☰ button.
- **Search form on phone:** on `/` the header's search form (the
  `center` snippet) still renders below the header row.

## `/rules`: contents (boards "Rules — contents", "Phone — rules contents")

- Data comes from `fetchRulesIndex()` on mount, showing loading text
  until it arrives and a `danger` alert on error.
- **Heading:** "Comprehensive Rules", with "{n} rules in 9 sections · as
  of {rules_as_of}" below. The "Go to rule" box sits on the right on
  desktop and below the heading on phone.
- **Desktop and tablet:** three columns. Sections are split between them
  in order: 1–3, 4–6, 7–9. On tablet, below 1100px, there are two
  columns in the same order.
  - Each section has a mono uppercase header: "{n} · {TITLE}" with the
    count on the right.
  - Each rule row is `<a href="/rules/{id}">`, with the mono gold id
    followed by the title.
- **Phone:** accordions, one per section. Each header is a 52px
  `<button aria-expanded>` showing the number, title, count and a
  chevron. Section 1 starts open. An open section lists all of its
  rules as 40px rows.

## `/rules/[id]`: rule detail (boards "Rule — detail", "Phone — rule detail", "Rule — not found")

- **Loading the entry:**
  - Normalise the id, then find the entry: `702.2c` → `702.2`; `702.2`
    and `702` are entries themselves.
  - Fetch the entry. When the entry isn't a top-level rule, also fetch
    its parent (`702`) for the sibling list and previous/next.
  - Navigating to another rule reloads; a stale response is ignored, as
    today.
- **Layout:**
  - Breadcrumbs (mono 13px): "Rules › {section n} {section title} ›
    {ancestor id} {ancestor title}". These come from `ancestors`; the
    section title comes from `CR_SECTIONS`, which the frontend mirrors
    as a constant.
  - h1: the mono gold id, then the entry's heading or text.
  - One card per subrule, with a mono id linking to `#r{id}` and the
    text below. The requested rule's card has a `gold` border, a
    `gold-wash` background, `aria-current="true"` and a "LINKED RULE"
    tag, and is scrolled into view on load.
  - Rule references inside the text (`rule 704`, `702.19b`, `rules
    702.19b–c`) become links. `ruleText.ts` splits the text; nothing
    uses `{@html}`.
  - Previous/Next cards link to the neighbouring entries under the same
    parent. They are hidden at either end, and for top-level rules.
  - Last line: "Comprehensive Rules as of {date}."
- **Desktop sidebar** (320px): "In {parent id} {parent title}", listing
  up to 14 sibling entries centred on the current one, with the current
  entry highlighted, and "All {n} entries in {parent} →" linking to the
  parent. There is no sidebar on phone or tablet.
- **Top-level rule** (e.g. `/rules/702`): the heading, then the entries
  as a link list, laid out like one `/rules` section.
- **Not found:** the mono id, "There's no rule {id}", an explanation with
  the rules date, a gold button "Go to {parent} · {parent heading}" when
  the parent exists, the outline button "Browse all rules", and the
  "Go to rule" box. The parent's existence and heading come from one
  extra `fetchRule`.

## `/history` (board "History")

- **Heading:** "Query history", with "Every question asked, newest
  first. 20 per page." below.
- **Rows** (`<ol>` of cards): each is a full-width `<button
  aria-expanded>` with three columns:
  - Local time as "Sep 29 · 14:02".
  - The question (15px/500), with the answer's first line below it,
    truncated. When there is no answer this line reads "(no answer)".
  - Chips: `error` in `danger`, "No sources" in `caution` for
    `uncited_answer`, "cached", and "{n} cited" in teal.
- **Expanded row** (one at a time):
  - `AnswerBody` (read-only: clicking a marker highlights within the
    row).
  - A 3-column grid of compact cited-source cards (`EvidenceCard`,
    compact off).
  - A footer: "Also retrieved · n", "Model {model}", and a "Raw results"
    `<details>` with the JSON.
  - Rows saved before citations existed show the answer as plain
    paragraphs.
- **Paging:** "← Newer" (disabled on page 1) and "Older →" (disabled when
  fewer than 20 rows come back). "Page n" at the top right.
- `CitedAnswer.svelte` and `SourcesList.svelte` are deleted, since
  nothing uses them any more.

## `/login` (boards "Log in", "Log in — wrong password", "Phone — log in")

- A centred 400px card (full width on phone): "Admin log in", "Query
  history and usage are for the site's admin.", a Password label and
  input, and a full-width gold "Log in" button.
- **Wrong password:** a `danger-line` input border, `aria-invalid`, and a
  `role="alert"` "Incorrect password." linked with `aria-describedby`.
  A rate-limit error shows the same way with its own message.

## `/admin/usage` (board "Usage")

- **Heading:** "Usage", with "UTC days · daily budget ${budget} · limits
  reset at midnight UTC" below.
- **Four tiles:**
  - "Today's spend": ${spend}, a meter against the budget, and "{pct}% of
    the ${budget} budget".
  - "AI answers today": `generated`, with "plus {cached} served from
    cache".
  - "Cache hit rate": {pct}%, or "n/a".
  - "Limited today": `degraded_ip + degraded_global`.
- **Chart "Spend, last 7 days":**
  - Bars in `gold` on the `card` surface with 4px top radius, and a
    dashed `fg-muted` budget line.
  - The y-axis runs $0 to max(budget, highest day) with 3 gridlines.
    Dates go on the x-axis.
  - Only today's bar is labelled. Each bar has a `title` tooltip giving
    its date and value.
  - The chart has `role="img"` and an `aria-label` listing every value.
    The table below is the table view.
- **Tables:**
  - "Outcomes by day": Date, Spend, Answered, Cached, Visitor limit,
    Site budget, Error.
  - "Busiest visitors today": IP bucket, Requests, Answers, Spend.
  - With no requests yet: "No requests yet today."

## Known constraints

- `/history` and `/admin/usage` are admin-only. The API enforces it, and
  the UI hides the links.
- `/rules/[id]` is served by the SPA fallback (`prerender = false`).
  `/rules` can be prerendered as an empty shell that fetches on mount.
- Reuse PR 3's pieces where they fit: `AnswerBody` and `EvidenceCard`
  (history), `format.ts`, and the `notice` and `danger` styles.
- No API changes. Everything comes from `/api/v1/rules`,
  `/api/v1/rules/{id}`, `/api/v1/queries` and `/api/v1/admin/usage`.

## Acceptance

- `npm run check`, `npm test` and `npm run build` pass. There are unit
  tests for `ruleText.ts`, the entry and neighbour logic in `rules.ts`,
  and the usage chart scale.
- Each page matches its board at 1280, 834 and 390 wide, with no
  horizontal overflow and no page errors. The phone menu works by touch
  and keyboard: Escape closes it and focus returns to the button.
