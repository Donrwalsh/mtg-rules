# Answer citations

Status: draft, awaiting review
Date: 2026-09-26
Branch: `feature/answer-citations` (cut from `feature/keyword-rule-matcher`)

## Purpose

For a rules tool, "why" matters as much as "what". Every claim in a
generated answer should be traceable to a specific rule, ruling, or card
text that was actually in the LLM's context, and checkable in one click.

Core decision: the LLM cites **numbered context blocks** (`[1]`, `[2]`),
never rule IDs or card names. The server owns the number → source
mapping, so a hallucinated rule number from pretraining can't pose as a
citation, and every citation is checkable. Nothing here depends on the
answer provider: no tool calling, no JSON mode. `GroqAnswerer` and
`OllamaAnswerer` both keep their `generate(query, context) -> str`
interface, and all citation logic runs on the returned plain text.

## Investigation findings

1. **Branch state.** `feature/keyword-rule-matcher` is **not merged**. It
   is 21 commits ahead of `main` and carries the Groq/history work plus
   the local-Ollama switch (`d72eb6d`). This branch is cut from its tip.
   There is no shared rules index: `KeywordMatcher` loads
   `rules_*.jsonl` itself and keeps only 702.N headings and their
   children. → New `rules_index.py` (below). `KeywordMatcher` is built
   from its rows, so the rules file is read once.

2. **Payload fields.**

   | source_type | Qdrant payload today | parsed JSONL row |
   |---|---|---|
   | `rule` | `source_type, content_hash, text, rule_id, section_id, section_title` | `rule_id, text, parent_id, content_hash` |
   | `oracle` | `source_type, content_hash, text, card_name, oracle_id` | `oracle_id, name, oracle_text, type_line, mana_cost, content_hash` |
   | `ruling` | `source_type, content_hash, text, card_name, oracle_id` | `oracle_id, published_at, comment, content_hash` |

   - `scryfall_uri` is **not kept**. The raw Scryfall bulk file has it
     (e.g. `https://scryfall.com/card/drc/13/nissa-worldsoul-speaker?utm_source=api`),
     but `parse_cards_file` drops it.
   - A ruling's `published_at` **is** in the parsed JSONL, but is **not**
     in the Qdrant payload. Card-matched rulings come from Qdrant
     (`fetch_card_rulings`), so it has to be added to the payload.

3. **content_hash skip.** Ingestion computes `content_hash` from content
   fields only (card: `oracle_id, name, oracle_text, type_line,
   mana_cost`; ruling: `oracle_id, published_at, comment`).
   `embed_and_store` re-embeds a point only when the stored payload's
   `content_hash` differs. Adding payload fields would therefore **skip
   all 120,883 existing points and leave their payloads stale**.
   Folding payload fields into `content_hash` would fix that, but would
   force a full re-embed (hours on CPU) for a metadata-only change. →
   Separate `payload_hash`, and a payload-only update path (below).

4. **Client-side routes.** `nginx.conf` already has
   `try_files $uri $uri/ /index.html`, and `adapter-static` writes an
   `index.html` fallback with `ssr = false`, which is how `/history`
   survives a hard refresh today. `/rules/702.11b` falls through to
   `index.html` the same way; the dot in the path is harmless to
   `try_files`. The root layout sets `prerender = true`, and a
   parameterized route can't be enumerated at build time, so
   `rules/[id]/+page.js` sets `prerender = false`. A hard refresh is
   verified with `curl` against the compose frontend.

Also noted: the rules raw text states an effective date ("effective as of
August 7, 2026"), but the API only mounts `data/parsed`, and parsed rows
don't carry it. The rule page shows the **ingest date** taken from the
parsed file name (`rules_2026-08-25.jsonl`). Carrying the effective date
through is deferred.

## Shared rules index (`mtg_api/rules_index.py`)

```python
class RulesIndex:
    def __init__(self, rules: list[dict], ingested_at: str | None = None): ...
    rules: list[dict]                      # file order
    ingested_at: str | None                # "2026-08-25", from the file name
    def get(self, rule_id: str) -> dict | None
    def __contains__(self, rule_id: str) -> bool
    def children(self, rule_id: str) -> list[dict]   # direct children, file order
    def ancestors(self, rule_id: str) -> list[dict]  # top-level first, excludes self

def load_rules_index(rules_path: Path) -> RulesIndex
```

`main.get_rules_index()` is `lru_cache`d and warmed in `lifespan`.
`get_keyword_matcher()` becomes `KeywordMatcher(get_rules_index().rules)`.
`KeywordMatcher`'s constructor is unchanged; `load_keyword_matcher` is
removed since nothing else calls it.

## Ingestion and embedding

**mtg-ingestion.** `Card` gains `scryfall_uri: str | None = None`. The
`?utm_source=api` query string is stripped at parse time. `scryfall_uri`
is **excluded** from `content_hash`: a link change is not a content
change. Cards are re-parsed from the existing raw file (`parse-cards`),
producing `cards_2026-09-26.jsonl`. The old tracked cards file is
replaced in the same commit.

**mtg-embed payloads.**
- `oracle`: + `scryfall_uri`
- `ruling`: + `published_at`, + `scryfall_uri` (from the joined card
  row, which the loader already has)
- `rule`: unchanged

**Payload staleness.** Every stored payload also carries
`payload_hash = sha256(json.dumps(payload_without_payload_hash,
sort_keys=True))`. `QdrantStore.existing_hashes` returns
`{point_id: (content_hash, payload_hash | None)}`. For each chunk:

| stored content_hash | stored payload_hash | action |
|---|---|---|
| differs / missing | — | embed + upsert (as today) |
| equal | differs / missing | `overwrite_payload` only, no embedding |
| equal | equal | skip |

Payload overwrites go through one `batch_update_points` call per
retrieve batch. `RunSummary` gains `payload_updated`, printed in the CLI
summary. The first embed run after deploy rewrites every existing
payload, since none has a `payload_hash`, but embeds nothing. This is
documented in the README.

## Backend (mtg-api)

### Result model

`QueryResult` gains optional fields, and all existing fields stay:
`rule_id`, `card_name`, `published_at`, `scryfall_uri`, and
`cited: bool = False`. They are filled at the four construction sites in
`main.query` from the card row or the Qdrant payload.

### Context building (`llm.py`)

```python
def source_label(result: QueryResult) -> str
    # "Rule 702.11b" | "Card — Lightning Bolt" | "Ruling — Homing Lightning (2018-01-19)"
    # (date omitted when published_at is missing; "card" and "oracle" both label as Card)

def build_context(results: list[QueryResult]) -> tuple[str, dict[int, QueryResult]]
    # "[1] Rule 702.11b: <text>\n\n[2] Card — Lightning Bolt: <text>\n\n..."
    # numbered 1..N in all_results order; the mapping is the only numbering
```

`source_label` is reused for citation titles, so a context block and its
citation always agree.

### Prompt

The system prompt is rewritten to say:
- Cite every factual claim with the bracketed number of its source.
- Use only numbers that appear in the context. Never cite or invent rule
  numbers from memory.
- Multiple sources are fine: `[1][3]` or `[1, 3]`.
- Prefer rules and rulings over card text when explaining why something
  works.
- If the context doesn't cover the question, say so, with no citations.

It also includes one short worked example: a two-block example context, a
question, and an answer using `[2]` and `[1]`, clearly fenced as an
example. If the model copies the example's numbers, validation catches
it.

### Citation processing (`mtg_api/citations.py`)

```python
@dataclass
class CitationParse:
    answer: str               # invalid markers removed
    cited_numbers: list[int]  # distinct, first-appearance order
    invalid_count: int

def parse_citations(answer: str, valid_numbers: set[int]) -> CitationParse
def find_rule_references(text: str, rules_index: RulesIndex) -> list[str]
def build_citations(cited_numbers, sources: dict[int, QueryResult]) -> list[Citation]
```

**Markers.**
- A well-formed marker is `\[\s*\d+(\s*,\s*\d+)*\s*\]`. This covers
  `[1]`, `[1, 3]`, and `[1,3]`. Adjacent markers like `[1][2]` are simply
  two matches.
- A malformed marker is any other bracket group that is citation-shaped:
  digits plus `-`, `–`, `;`, `^`, or whitespace, such as `[1-3]`, `[^2]`,
  or `[1;2]`. It is removed and counts as one invalid citation. Ranges
  are not expanded.
- Out-of-range numbers (`0`, or greater than N) are removed from their
  marker. `[1, 99]` becomes `[1]`, and `[99]` disappears along with the
  whitespace before it. Each invalid number adds one to `invalid_count`.
- All invalid numbers and malformed markers are logged at `WARNING`.
- Repeated citations of one source count once in `cited_numbers`.
- Bracketed non-numeric text (`[Rule 702.11b]`, `{R}` mana symbols) is
  left alone.

**Raw rule references.** Candidates match
`(?<![\d.$€£])\d{3}\.\d+[a-z]?(?![a-z\d]|\.\d)`, which is the requested
`\b\d{3}\.\d+[a-z]?\b` hardened against prices (`$100.50`) and dotted
dates or versions (`2018.01.19`, `100.1.2`). Each candidate is looked up
in `RulesIndex`. Valid IDs go to `rule_references` (distinct, in order).
Invalid ones stay as plain text and are logged at `WARNING`. The
references come from the cleaned answer.

**Citation model.**

```python
class Citation(BaseModel):
    number: int
    source_type: str          # "rule" | "card" | "ruling"  ("oracle" normalized to "card")
    title: str                # source_label(result)
    rule_id: str | None
    card_name: str | None
    oracle_id: str | None
    text: str
    url: str | None           # rule -> "/rules/{rule_id}"; card/ruling -> scryfall_uri (None if absent)
    published_at: str | None  # rulings only
```

Rule URLs are frontend-relative paths, and the frontend owns the origin.
No third-party rules sites are used.

### API response

`QueryResponse` keeps `query`, `results`, and `answer`, and adds:
- `citations: list[Citation]`: cited sources only, ordered by number.
- `rule_references: list[str]`
- `citation_stats: CitationStats {cited_count, invalid_count, uncited_answer}`

`results[i].cited` is true when `i + 1` was cited. `uncited_answer` is
`answer is not None and cited_count == 0`. When generation fails,
`answer` is `None`, the lists are empty, and the stats are zero/false.

### Rules endpoint

`GET /api/v1/rules/{rule_id}` normalizes the ID (trim, lowercase, strip
a trailing `.`) and returns:

```json
{
  "rule_id": "702.11b",
  "text": "...",
  "ancestors": [{"rule_id": "702", "text": "Keyword Abilities"},
                {"rule_id": "702.11", "text": "Hexproof"}],
  "subrules":  [{"rule_id": "...", "text": "..."}],
  "rules_ingested_at": "2026-08-25"
}
```

`subrules` lists direct children only. Each of those links to its own
page, which keeps the response small for top-level rules like `702`. An
unknown ID returns `404 {"detail": "Rule not found"}`. CORS already
allows `GET`.

### Persistence

Migration `0002_add_query_history_citations` adds three nullable `JSON`
columns to `query_history`: `citations`, `citation_stats`, and
`rule_references`. The third isn't in the brief, but the history page
can't link rule references without it. `save_history` gains matching
keyword arguments that default to `None`. `/api/v1/query` saves them,
and the stored `results` include the `cited` flags. `GET /api/v1/queries`
returns them, `null` for rows older than the migration.

## Frontend (mtg-web)

Model output is never rendered with `{@html}`.

- **`$lib/api.ts`**: types for `Citation`, `CitationStats`, the extended
  `QueryResult` / `QueryResponse` / `QueryHistoryRow`, and
  `fetchRule(id)`.
- **`$lib/segments.ts`**: `segmentAnswer(answer, citationNumbers,
  ruleRefs)` returns `{kind: 'text' | 'cite' | 'rule', ...}[]`. The
  server has already stripped invalid markers, so this only needs the
  well-formed marker regex plus membership checks. Rule-reference
  candidates not in `ruleRefs` stay text.
- **`$lib/CitedAnswer.svelte`**: renders the segments.
  - Each citation number is a `<sup><a>`: an internal link for rules, or
    Scryfall with `target="_blank" rel="noopener"`.
  - A marker shows a popover (`role="tooltip"`, linked via
    `aria-describedby`) with the label and text. It opens on
    mouseenter/focus and closes on mouseleave/blur/Escape. The popover
    holds no focusable content, so it can't trap focus.
  - Rule references render as `<a href="/rules/{id}">`.
- **`$lib/SourcesList.svelte`**: a numbered "Sources" `<ol>` of cited
  sources, then a collapsed `<details><summary>Also retrieved (N)`
  listing the uncited results.
- **`/` page**: uses both components and shows a subtle "No sources
  cited" note when `uncited_answer`.
- **`/rules/[id]`**: `+page.js` sets `prerender = false`. The page shows
  an ancestor breadcrumb, the rule text, and subrules as links to their
  own pages. It shows "Comprehensive Rules as ingested 2026-08-25" and
  re-fetches when `id` changes. On a 404 it shows a friendly "No rule
  702.99z" message with links back to search and to the parent rule if
  the ID has one. Other errors show plainly.
- **`/history`**: the expanded row renders the answer through
  `CitedAnswer` and `SourcesList`. Old rows without citations fall back
  to plain text.

Styling is minimal and matches the existing pages: a small scoped
`<style>` per component, no CSS framework.

## Testing

Tests follow existing patterns: fakes over mocks, in-memory SQLite, and
`TestClient` with `dependency_overrides`.

- **mtg-api `test_rules_index.py`**: get, children, ancestors,
  ingested_at from the file name, unknown ID.
- **`test_llm.py`** (updated): numbering, labels per source type
  (card/oracle/ruling with and without date/rule), the mapping, and an
  empty list returning `("", {})`.
- **`test_citations.py`**: single, adjacent, comma-separated, repeated,
  out-of-range (including a mixed `[1, 99]`), malformed (`[1-3]`,
  `[^2]`), no citations, and non-citation brackets left alone. Rule
  references: valid, invalid (logged, kept as text), prices, dates,
  versions. Also checks that `build_citations` URLs and fields are
  correct per type.
- **`test_query.py`** (extended): a fake answerer with fixed markers
  (`"... [2] ... [1][99] ... rule 702.11b ... 999.9z"`) checks
  `citations`, `cited` flags, `citation_stats`, `rule_references`, the
  cleaned answer, and history persistence of the new columns. A failed
  generation gives empty citations.
- **`test_rules_endpoint.py`**: found, subrules and ancestors included,
  normalization, 404.
- **`test_migrations.py`**: `alembic upgrade head` then `downgrade 0001`
  on a temp-file SQLite DB, checking the columns. Needs `alembic`
  installed locally; it's already a declared dependency.
- **`test_history.py`**: citations/stats/refs round-trip, with `None`
  defaults.
- **mtg-ingestion**: `scryfall_uri` kept, `utm` stripped, and absent from
  the hash.
- **mtg-embed**: payloads carry the new fields. The pipeline test for the
  three-way decision uses a fake store where a changed payload with an
  unchanged content hash gets `overwrite_payload` and no embed call. The
  store test covers `existing_hashes` returning both hashes.

## Rollout / verification

1. `pytest` per package, run separately; running them together hits
   test-module name collisions.
2. Re-parse cards and restart the worker, then run embed. This should
   show `embedded≈0, payload_updated≈120k`.
3. Rebuild backend and frontend (`docker compose up -d --build`).
4. Acceptance queries:
   - Hexproof/Lightning Bolt: cites the block holding 702.11b,
     `citations` contains it with url `/rules/702.11b`, and
     `invalid_count == 0`.
   - Empty library: cites a 704.5b state-based-action block.
   - Best Standard deck: no citations and `uncited_answer == true`.
5. `curl localhost:3000/rules/702.11b` returns the app shell, and the
   page renders in a browser on a hard refresh.

Answer quality depends on the model (`phi4` in compose today). If `phi4`
doesn't follow the citation format reliably, compare against
`qwen2.5:14b` (already pulled locally) and report. The compose default
won't change without asking.

## Out of scope / deferred

- The rules effective date ("as of August 7, 2026") needs a parsed
  metadata file. Only the ingest date is shown.
- Claim-level verification (does source [n] actually support the
  sentence?) isn't done. Only existence and range are validated.
- A raw rule reference that is valid but wasn't in the context (the model
  quoting a real rule from memory) is linked, not flagged.
- No frontend unit-test harness exists. `segments.ts` is kept small and
  covered by manual verification.
