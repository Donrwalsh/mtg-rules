# Keyword-ability rule matcher

Status: approved, ready for implementation planning
Date: 2026-09-26

## Purpose

"Can I target my own creature that has hexproof with Lightning Bolt?"
gets a wrong or hedged answer because hybrid search never surfaces rule
702.11 (Hexproof): the exact card match for Lightning Bolt, its rulings,
and semantically-near rulings crowd the rule out of the top-k. We
already solved the equivalent problem for cards with `CardMatcher` +
`fetch_card_rulings` (exact match, not similarity). This spec applies the
same pattern to keyword abilities: when a query names a keyword, that
keyword's Comprehensive Rules text is always in the LLM context.

## Source data

The latest `rules_*.jsonl` in `settings.parsed_dir`. One JSON object per
line with fields `rule_id`, `text`, `parent_id`, `content_hash`
(inspected, not assumed). In section 702:

- `702.1` is the section's introductory prose, not a keyword. Skipped.
- Every other `702.N` row is a keyword heading: `text` is the keyword
  name, e.g. `{"rule_id": "702.11", "text": "Hexproof", "parent_id": "702"}`.
  194 headings in the 2026-08-25 file.
- Every subrule (`702.11a`, `702.11b`, ...) has `parent_id` equal to its
  heading's `rule_id`. There are no deeper levels in 702.

## Matching

**`keyword_matcher.py`** — `KeywordMatcher(rules: list[dict])`, same
shape as `CardMatcher`: an Aho-Corasick automaton over lowercased
surface forms, word-boundary check on each hit (neighbouring characters
must be non-alphanumeric).

Surface forms per heading (all map back to the same heading):

- The heading text lowercased, with a trailing `!` stripped
  (`For Mirrodin!` → `for mirrodin`, since queries rarely include it).
- `X and Y` headings also register `X` and `Y` individually
  (`Daybound and Nightbound`).
- A trailing parenthetical registers both parts (`∞ (Infinity)` →
  `∞`, `infinity`).
- Cheap inflections of the final word: `+s`, `+es`, `+ed`, `+ing`, and
  for words ending in `e`, `+d` and `e`→`ing` (`trample` → `tramples`,
  `trampled`, `trampling`). No consonant doubling, no irregulars. If an
  inflected form collides with another keyword's base form, the base
  form wins.

`find_matches(query) -> list[dict]` returns one entry per matched
keyword, in order of first appearance, each
`{"keyword": "Hexproof", "rule_id": "702.11", "rules": [heading, 702.11a, ...]}`
with rules in file order. Overlapping hits resolve to the longest span
(greedy: longest first, then earliest start); a keyword matched more
than once is returned once.

**Known tradeoff — common English words.** Many keyword names are
ordinary words: reach, flash, ward, fear, storm, echo, plot, crew,
equip, partner, protection, visit, gift, escape, training, and more.
"Can I reach the stack in time?" will pull in 702.17 (Reach). We match
them anyway in this version. The cost is extra, harmless-but-noisy
context rows for the LLM (a few hundred tokens each); the benefit is
that a real keyword question is never missed. Revisit with a heuristic
(e.g. require a card match or rules vocabulary nearby) only if noise
measurably hurts answers.

Also not handled: landwalk variants (`islandwalk`, `swampwalk`), and
parameterised forms like `hexproof from white` still match `hexproof`
(which is correct).

## Endpoint changes (`main.py`)

- `get_keyword_matcher()` — `lru_cache(maxsize=1)`, loads the latest
  `rules_*.jsonl` via `load_keyword_matcher(path)`. Warmed in
  `lifespan` alongside the card matcher. No Qdrant call.
- For each matched keyword, each of its rules becomes a
  `QueryResult(source="rule", title=rule_id, text=text, score=1.0,
  match_type="keyword_rule_match")`. `source`/`title` mirror how rule
  vector hits are rendered today.
- Ordering: `card_results + card_ruling_results + keyword_results +
  vector_results`.
- Any vector hit whose payload `rule_id` is already in
  `keyword_results` is dropped (same as the existing oracle_id dedupe).

No model, config, or frontend changes. `match_type` is a free string;
the web UI does not branch on it.

## Testing

- `tests/test_keyword_matcher.py`, mirroring `test_card_matcher.py`:
  single/multi-word, case-insensitive, word boundary, longest-match on
  overlap, inflections, subrule collection and ordering, `702.1`
  exclusion, `!`/`and`/parenthetical handling, empty inputs.
- `tests/test_query.py`: keyword results present with the right
  `match_type`/score; ordered after card-ruling results and before
  vector hits; duplicate vector hit dropped.
- `tests/test_lifespan.py`: keyword matcher warmed.
- Manual: the hexproof question returns 702.11 + 702.11a–h, and the
  answer says yes, you can target your own hexproof creature.
