# API data for the Judge's Desk redesign

Status: design agreed, awaiting implementation
Date: 2026-09-29
Branch: `feature/redesign-api-data` (cut from `main`)
Part 2 of 4 of the Judge's Desk redesign. The others:
[foundation](2026-09-29-frontend-foundation-design.md),
[search page](2026-09-29-judge-desk-search-design.md),
[secondary pages](2026-09-29-redesign-secondary-pages-design.md).

## Purpose

The design shows data the API doesn't return:

- Each card's image, type line, mana cost and power/toughness.
- A heading on every rule, e.g. "702.2c · Deathtouch".
- The daily answer limit ("20 AI answers a day") and how current the
  rules are.
- A listing of every rule, for the "Rules" nav link.

This PR adds all of it without changing retrieval, the LLM context or any
answer. It can merge and deploy before PR 1, or after it.

## Investigation findings

1. **The API already holds every card in memory.** `get_card_matcher()`
   loads the whole latest `cards_*.jsonl` into `CardMatcher` for
   card-name matching. So a lookup by `oracle_id` is one more dict of
   references to rows already loaded. It adds no Qdrant payload fields
   and needs no re-embed or payload refresh.
2. **Ingestion drops the fields the design needs.**
   `parse_cards_file` keeps `type_line` and `mana_cost` but drops
   `power`, `toughness`, `loyalty` and `image_uris`. Double-faced cards
   keep `image_uris` on `card_faces[0]`, not on the card itself.
3. **Scryfall image URLs follow one pattern:**
   `https://cards.scryfall.io/<size>/front/a/4/<id>.jpg?<ts>`. Storing
   only the `normal` URL is enough, because the `small` URL is the same
   string with `/normal/` swapped for `/small/`. That keeps the extra API
   memory to about 33k × 110 bytes.
4. **Rules have headings, just not on the rule itself.** The top-level
   rules are titles ("510" → "Combat Damage Step"), and so are keyword
   and other entries at the `xxx.N` level ("702.2" → "Deathtouch").
   Their subrules are full sentences.
5. **The Comprehensive Rules have nine sections** ("1. Game Concepts" …
   "9. Casual Variants"). Their names appear only in the raw text, not
   in the parsed rules.
6. **Cached answers are stored without the new fields.** Answer-cache
   rows and history rows keep the `QueryResult` and `Citation` dumps from
   when they were written. Loading a new cards file changes
   `data_version` and so invalidates the answer cache, but history keeps
   old rows.

## Changes

### Ingestion (`mtg-worker/mtg-ingestion`)

`Card` gains fields that are left out of `content_hash`, so the embedded
text and every stored vector stay unchanged:

- `power: str | None`, `toughness: str | None`, `loyalty: str | None`.
  Taken from the card, or from `card_faces[0]` when the card has no
  value of its own.
- `image_uri: str | None`. Scryfall's `image_uris.normal` from the card,
  else from `card_faces[0]`, else `None`. The query string is kept,
  since Scryfall uses it to bust caches.

Re-run `parse-cards` from the committed raw file and commit the new
`cards_2026-09-28.jsonl`. Rules and rulings are unchanged.

### API (`mtg-api`)

**Card details.** A new `CardDetails` model:

```
name, type_line, mana_cost, power, toughness, loyalty, image_small, image_normal
```

It is built from a parsed card row by `card_details(row)` in a new
`card_details.py`. `image_small` is derived from `image_normal`.

**Rule heading.** `RulesIndex.heading(rule_id) -> str | None` returns the
text of the nearest title-like rule, starting from the rule itself and
walking up through its parents. A rule counts as a title when its text is
at most 60 characters and doesn't end in `.`, `:` or `)`. So 702.2c →
"Deathtouch", 510.1c → "Combat Damage Step", 100.1 → "General".

**Enrichment.** `QueryResult` and `Citation` each gain
`card: CardDetails | None = None` and `heading: str | None = None`.
`enrich.py` fills them in:

- `enrich_results(results, cards, rules_index)`:
  - Card, oracle and ruling results get `card`, looked up by `oracle_id`.
    A ruling gets the card it rules on.
  - Rule results get `heading`.
- `enrich_citations(citations, cards, rules_index)`: the same, for
  citations.

`cards` is `CardMatcher.by_oracle_id(oracle_id) -> dict | None`, backed
by a dict built from the rows the matcher already loads.

Both run on every `/api/v1/query` response:

- **Fresh path:** `enrich_results(all_results)` runs right after the
  results are put together. That is after `build_context`, which only
  reads `.text`, so the LLM context and `context_hash` can't change.
  `enrich_citations` runs after `cite_answer`. History therefore stores
  enriched rows.
- **Cache-hit path:** both run on the rebuilt `cached_response` before it
  is returned, so rows written before this change get the fields too.

**`GET /api/v1/meta`** (public, no auth):

```json
{"answers_per_day": 20, "max_query_chars": 500, "rules_as_of": "2026-09-28"}
```

`answers_per_day` is `ip_daily_llm_limit` when `gating_enabled`, else
`null` (unlimited, as in dev). `rules_as_of` is `RulesIndex.ingested_at`,
the date the rules file was parsed. The page labels it "as of"; it is not
the date the CR took effect (see Out of scope).

**`GET /api/v1/rules`** (public). The table of contents:

```json
{"sections": [{"number": 1, "title": "Game Concepts",
               "rules": [{"rule_id": "100", "text": "General"}, ...]}, ...],
 "rules_as_of": "2026-09-28"}
```

The section titles are a constant, `CR_SECTIONS` in `rules_index.py`,
since they have been stable for decades. Each rule is filed under
`int(rule_id[0])`. Only the three-digit rules are listed.

**`GET /api/v1/rules/{rule_id}`** also gains `heading`, which the search
page's rule-link preview shows.

### Frontend types (`mtg-web/src/lib/api.ts`)

- `CardDetails` interface, plus `card?` and `heading?` on `QueryResult`
  and `Citation`.
- `fetchMeta()` and `fetchRulesIndex()`, with their types.
- No UI changes. PR 3 and PR 4 use these.

### Docs

The README's API section documents the new fields and both endpoints.

## Rollout

1. Merge. The backend image rebuilds on deploy. The committed
   `cards_*.jsonl` now carries the new fields.
2. Run `make sync-prod` to copy the parsed files to production. That also
   writes a new `data_version`, which invalidates cached answers.
3. Qdrant needs no change: card payloads are built from an explicit
   field list and are unchanged.

Until step 2 runs, production serves the old cards file: `card` still
gets filled in, but its image and power/toughness fields are `null`. The
frontend (PR 3) has to render that gracefully in any case.

## Memory

Production shares a 4 GB VM with other apps. Extra API memory is one `oracle_id` dict over rows that are already
loaded, plus the new strings (about 33k image URLs and short P/T values).
That's about 10 MB. Nothing new runs at request time besides dict
lookups.

## Out of scope

- The CR's own "effective as of" date. That would mean passing a new
  metadata file through ingestion and `sync_data.py`. Ingest date is
  close enough for now.
- Changing the LLM prompt (the summary line is done in the frontend, in
  PR 3).
- Images for rulings of cards that have no image (they render the
  placeholder).

## Acceptance

- `pytest` and `ruff` pass in `mtg-api` and `mtg-ingestion`.
- A query naming a card returns `results[].card.image_small`, and a
  citation of a rule returns `heading`.
- `context_hash` for a fixed eval query is identical before and after
  this change (check with the eval harness in retrieval mode).
- `/api/v1/meta` and `/api/v1/rules` respond without auth.
