# Judge's Desk search page

Status: design agreed, awaiting implementation
Date: 2026-09-29
Branch: `feature/judge-desk-search` (cut from `main` after PRs 1 and 2 merge)
Part 3 of 4 of the Judge's Desk redesign. The others:
[foundation](2026-09-29-frontend-foundation-design.md),
[API data](2026-09-29-redesign-api-data-design.md),
[secondary pages](2026-09-29-redesign-secondary-pages-design.md).

Depends on PR 1 (Svelte 5, Tailwind tokens, `AppHeader`) and PR 2
(`card`, `heading`, `/api/v1/meta`).

## Purpose

Rebuild `/`, the search page, as the Judge's Desk: the answer reads like
a ruling, and every claim sits next to its evidence. Every board on the
design canvas's "answered" and "States" rows is in scope:

| Board | Covered by |
|---|---|
| Desktop — answered (1280) | two-column layout |
| Tablet — answered (834) | stacked layout |
| Phone — answered (390) | stacked layout, compact evidence list |
| Phone — card citation tapped | source sheet (card) |
| Phone — ruling citation tapped | source sheet (ruling) |
| First visit / Phone — first visit | empty state |
| Loading | loading state |
| Daily AI answers used / Phone — daily answers used | degraded: `ip_quota`, none left |
| Site-wide budget reached | degraded: `global_budget` |
| Request failed | error state |
| Answer with no citations | uncited answer |
| Status messages | running-low chip, breather notice, 429 alert, character counter, admin cached chip, rule-link preview |

## Layout

Two breakpoints from PR 1: `sm` at 640px and `desk` at 1100px.

- **Desktop (≥1100px):**
  - The header holds the search form (the `center` snippet) between the
    wordmark and the nav.
  - Below it, a grid of `minmax(0,1fr) 460px`: the answer on the left
    (padding 36px × 48px, a `line` border on the right) and the evidence
    panel on the right (`panel` background).
  - The panel scrolls on its own (`position: sticky`, top below the
    header, `max-height: calc(100vh - header)`, `overflow-y: auto`).
- **Tablet (640–1099px):**
  - The header stacks: wordmark and nav on one row, the full-width form
    below.
  - Answer section, then the evidence section. Evidence cards sit in a
    2-column grid, and the ruling card spans both columns.
- **Phone (<640px):**
  - Header: the wordmark, then a compact form below it (no "Q" label,
    button at least 44px).
  - Answer, then evidence as a single column of compact rows: art at
    52×72, name, one-line summary, chevron.
  - Every rule pill, marker and row is at least 44px tall to tap.

## States

`+page.svelte` holds a single view state:

```
idle → loading → answered | degraded | failed
```

- **Idle (first visit)** (board "First visit"):
  - The header has no form. A centred hero reads "Ask a rules question"
    with a sub-line, a large form (a textarea on phone), three example
    questions as buttons, and a meta line.
  - Meta line: "Comprehensive Rules · as of {rules_as_of}" ·
    "Scryfall oracle text & rulings" · "{answers_per_day} AI answers a
    day". The last part is left out when `answers_per_day` is `null`.
  - Clicking an example fills the input and submits it.
  - Examples:
    - "Does trample plus deathtouch only need 1 damage on each blocker?"
    - "What happens when two replacement effects apply to the same event?"
    - "Can I cast an instant during my opponent's cleanup step?"
    - Phone shows the first and third only.
- **Loading** (board "Loading"):
  - The submit button reads "Searching…" and is disabled.
  - A status line with a spinner: "Searching rules, cards and rulings,
    then writing an answer…".
  - Skeleton bars (`skeleton`) where the answer goes, and skeleton
    cards in the evidence panel, whose tabs read "Cited" and "Also
    retrieved" with no counts.
  - "This usually takes a few seconds."
  - The region has `aria-busy="true"`.
  - A previous answer is replaced by the skeleton, not dimmed.
- **Answered** (the three "answered" boards):
  - A chip row, then the answer, then "Rules referenced", with evidence
    beside or below.
  - Chip row:
    - A teal chip: "{n} sources cited" ("{n} cited" on phone).
    - For a cached answer, a neutral chip: "cached · {Mon D}".
    - Plain text "{k} answers left today" ("{k} left today" on phone)
      when `answers_remaining` isn't null.
    - When `answers_remaining` ≤ 3, "left today" becomes a
      `notice`-coloured chip reading "{k} AI answer(s) left today" (the
      running-low row on the "Status messages" board).
    - For admins, the cached chip reads "cached · first generated
      {Mon D}" and has a "Get a fresh answer" button beside it.
  - **Summary line:**
    - The answer's first sentence, in 26px/500 (24px tablet, 21px
      phone). The rest follows as body paragraphs (17px/1.75, 16px on
      phone, `fg-body`), split on blank lines.
    - Only used when the first sentence is at most 140 characters and
      more text follows. Otherwise the whole answer is body text.
    - Citation markers and rule links inside the summary still work.
  - **Citation markers:** the number in mono inside a `line-muted`
    outlined pill (1px × 5px padding on desktop, 3px × 8px on phone),
    and a link to `#source-{n}`. Hover or focus shows the existing
    preview popover, restyled (see "Rule-link preview" below).
  - **Rule references in prose:** mono gold links to `/rules/{id}`.
    Hover or focus shows a preview.
  - **Rules referenced:**
    - Desktop: a box (`well`, `line` border) holding a
      "Rules referenced" label and mono pills (`chip`), one per
      `rule_references` entry.
    - Tablet and phone: an inline row of the same pills.
    - Hidden when there are none.
- **Uncited answer** (board "Answer with no citations"):
  - The chip row shows a `caution-chip` chip reading "No sources cited"
    instead of the teal one.
  - The answer is rendered normally.
  - A `caution` notice below it: "This answer didn't point to any
    sources, so treat it with care. Check it against the passages
    {on the right | below}, which were retrieved for your question."
  - Evidence: the "Cited · 0" tab is disabled, and "Retrieved · {n}" is
    selected.
- **Degraded** (`degraded` is set, so there is no answer):
  - The submit button reads "Search" instead of "Ask".
  - A status notice sits at the top of the answer column:
    - `global_budget` (`paused` colours, pause icon): "AI answers are
      paused for today". Body: "The site has reached its daily limit for
      everyone, not just you. Answers resume at {reset} (midnight UTC).
      Here are the matching rules, cards and rulings."
    - `ip_quota` with `answers_remaining` = 0 or null (`notice` colours,
      clock icon): "You've used today's {answers_per_day} AI answers".
      Body: "They reset at {reset} (midnight UTC). Search still works:
      here are the rules, cards and rulings that match your question."
      The count is left out when `answers_per_day` is null.
    - `ip_quota` with `answers_remaining` > 0 (the breather row on the
      "Status messages" board): "**Taking a short breather.** AI answers
      pause for a few minutes when you ask quickly. You still have {k}
      today. Matching sources are below."
  - Below the notice: "Matching sources · {n}" and "best match first".
    - Desktop and tablet: the results grouped into RULES, CARDS and
      RULINGS sections, three columns at `desk` and one column below it.
    - Phone: filter chips "All · n / Rules · n / Cards · n / Rulings · n"
      over a single list.
  - There is no evidence panel in this state; the matching sources
    replace it.
- **Failed** (board "Request failed"):
  - Any error other than 429 replaces the content with a centred
    `role="alert"` block: a warning icon, "Search didn't go through",
    "The server didn't answer. Your question is still in the box, so you
    can try again in a moment.", a gold "Try again" button that
    re-submits, and a `<details>` "Details" holding the error string.
  - A **429** is not a failed state. The content stays as it was and a
    `danger` line appears under the form: "Too many requests. Wait a few
    seconds and try again." The input gets a `danger-line` border and
    `aria-describedby` pointing at the message.
- **Character counter:** at 400 characters or more, "{len} / 500" shows
  under the form, right-aligned, in mono `fg-muted`. The limit comes
  from `/api/v1/meta` and falls back to `MAX_QUERY_CHARS`. The input
  also has `maxlength`.

`{reset}` is the next UTC midnight, shown in local time
(`toLocaleTimeString([], {hour: 'numeric', minute: '2-digit'})`).

## Evidence panel

A tablist with two tabs: "Cited · {citations.length}" and "Also retrieved
· {uncited results}". It follows the ARIA tabs pattern: `role="tablist"`,
`role="tab"` with `aria-selected` and `aria-controls`, a `role="tabpanel"`
per tab, and arrow keys to move between tabs. Tabs are at least 44px tall.
"Cited" is selected by default. It is disabled when there are no citations,
and "Also retrieved" is selected instead.

Evidence cards, keyed by `source_type`:

- **Rule:**
  - `card` background, `line` border.
  - Header row: "{n} · RULE" on the left and the rule id as a gold link
    to `/rules/{id}` on the right.
  - A heading line "{heading}" when there is one.
  - The text, clamped to 3 lines on desktop and 2 on phone.
- **Card:**
  - Art at 96×134: the `card.image_small` `<img>`, with `alt="{name}"`,
    `loading="lazy"`, `decoding="async"` and 6px radius. With no image,
    the `art` placeholder reads "card art".
  - Then:
    - "{n} · CARD", with the name and mana cost (mono) on one line.
    - The type line, with " · {power}/{toughness}" or " · Loyalty {l}"
      when present.
    - The oracle text.
    - "Scryfall ↗", opening in a new tab.
- **Ruling:**
  - `teal-wash` background, `teal-line` border.
  - "{n} · RULING" in teal, with the published date on the right.
  - "Ruling on {card name} · Scryfall ↗".
  - A blockquote with a teal left rule, italic.
- **"Also retrieved"** cards look the same without the number, and the
  label reads the source type.

Clicking a citation marker (desktop and tablet), or tapping it in the
answer, **selects** that citation:

- The marker fills gold (`gold` background, `gold-ink` text).
- The matching evidence card gets a `gold` border and `gold-wash`
  background; for a ruling, teal instead.
- The panel scrolls the card into view (`scrollIntoView({block:
  'nearest', behavior: 'smooth'})`, with no smoothing under
  `prefers-reduced-motion`).
- The **sentence just before the marker** is highlighted with
  `gold-mark` background and `gold-hover` text, or `teal-mark` /
  `teal-mark-fg` for a ruling.
- Clicking the same marker again, or pressing Escape, clears the
  selection. Selecting another citation moves it.
- The "Cited" tab is activated if needed.

The sentence before a marker runs from the end of the previous sentence
(the last `.`, `!` or `?` followed by whitespace, or a line break) up to
the marker. A marker placed just after a full stop ("…excess. [5]")
belongs to the sentence before it. When one sentence has several markers,
all of them highlight the same sentence.

## Phone source sheet

On phone (below 640px), tapping a marker or an evidence row opens a
bottom sheet instead of selecting in place (boards "Phone — card citation
tapped" and "Phone — ruling citation tapped").

- A `<dialog>` opened with `showModal()`, `aria-label="Source {n}:
  {title}"`, with its backdrop set to `scrim`. The sheet is anchored to
  the bottom, has 18px top corners, a `card` background, a grabber bar,
  and takes at most 85% of the viewport height with scrolling inside.
- Top row:
  - Previous and next buttons (44×44, `aria-label`) around "Source {i}
    of {n}".
  - A close button (44×44, round, `chip`).
  - Previous is disabled at 1 and next at n.
  - Escape and a tap on the backdrop close the sheet.
- The highlight for the matching marker and sentence follows the sheet's
  current source.
- **Card sheet:**
  - Image at 150×209, from `image_normal`, falling back to the
    placeholder.
  - "CARD", the name, the mana cost, the type line and P/T.
  - The oracle text in a `panel` well.
  - Two outline buttons: "Rulings" (the card's Scryfall page, with
    `#rulings` appended) and "Scryfall ↗".
- **Ruling sheet:**
  - "RULING" and "Published {date}", then "Ruling on {card}" in 19px/600.
  - The full quote in a `panel` well.
  - A "From the card" row with a 40×56 thumbnail. It links to Scryfall.
  - Two outline buttons: "All rulings (card)" and "Scryfall ↗".
- **Rule sheet** (no board; built from the same pieces):
  - "RULE {id}", the heading, and the full text in a well.
  - Two outline buttons: "Open rule {id}" (`/rules/{id}`) and "Parent
    rule" when the rule has one.
- Focus returns to the element that opened the sheet when it closes.

## Rule-link preview

For both rule links and citation markers on hover or focus
(`role="tooltip"`, `aria-describedby`, Escape closes it):

- A `card` popover with a `line-strong` border, a shadow, and at most
  28rem wide.
- First line in mono: "{rule_id} · {heading}", or the citation's title.
- Then the text, clamped to 4 lines.

Citation previews use the citation's own data. Rule-link previews fetch
`/api/v1/rules/{id}` on first hover and keep the result in a module-level
`Map`.

Hover previews are skipped when the device doesn't hover
(`(hover: none)`); there, a tap follows the phone sheet or select
behaviour.

## Data

- `$lib/meta.svelte.ts` fetches `/api/v1/meta` once, on first use. If it
  fails, it falls back to `{answers_per_day: null, max_query_chars: 500,
  rules_as_of: null}`.
- `submitQuery` is unchanged.
- **Dev fixtures:** in dev only (`import.meta.env.DEV`), `?mock=<name>`
  on `/` makes `submitQuery` resolve with a fixture from
  `$lib/fixtures/`. The names are `answered`, `uncited`, `quota`,
  `breather`, `paused`, `error`, `slow` (which never resolves) and
  `ratelimited`. This lets every board be checked without spending quota
  or setting up gating locally. Vite drops the branch from production
  builds.

## Logic modules (unit-tested with Vitest)

- `$lib/answer/layout.ts`:
  - `assignSentences(segments)`: tags each text piece, marker and rule
    link with its sentence.
  - `layoutAnswer(pieces)`: splits the answer into the summary line and
    paragraphs.
- `$lib/evidence.ts`:
  - `EvidenceItem`, one shape for citations and results, with
    `fromCitation` and `fromResult`.
  - `sourceKind(source)`: `oracle` becomes `card`.
  - `uncitedItems`, `groupByKind`.
  - `statLine(card)` ("Creature — Dinosaur · 6/6").
  - `cardSummary(item)`: the one-line phone summary.
  - `parentRule(id)`.
- `$lib/status.ts`:
  - `noticeFor(response)` returns
    `'paused' | 'quota_used' | 'breather' | 'no_answer' | null`.
    `no_answer` covers a failed generation that still returns results.
  - `isRunningLow(remaining)`, `answersLeftLabel(n)`.
- `$lib/format.ts`:
  - `shortDate(iso)` ("Sep 27").
  - `calendarDate(date)` ("Jun 16, 2023", read as a UTC calendar day).
  - `resetTime(now)`.

## Accessibility

- Real `<button>` and `<a href>` elements, with labels on icon-only
  buttons.
- Notices use `role="status"`; the error block and the 429 message use
  `role="alert"`.
- The phone input has a visually hidden `<label>`. Desktop has a visible
  "Q" label.
- Colour is never the only signal: the selected marker also has
  `aria-current="true"`, and the selected card is announced through its
  number.
- Answer text is still rendered as text segments; nothing uses `{@html}`.

## Out of scope

- The phone menu button (PR 4 designs it). Until then, the phone header
  shows the wordmark and nav links on one row.
- Keeping the query in the URL (`?q=`) or restoring it on back or
  forward.
- Light mode.

## Acceptance

- `npm run check`, `npm test` and `npm run build` pass.
- In `npm run dev`, every `?mock=` state matches its board at 1280,
  834 and 390px wide. Selecting a citation highlights the marker, the
  card and the sentence. The phone sheet's previous/next, close, Escape
  and focus return all work.
- A real query against the dev stack shows card images and rule headings.
- Keyboard only: Tab reaches the form, markers, rule links, tabs and
  cards. Arrow keys switch tabs. Escape closes the preview, the sheet
  and the selection.
