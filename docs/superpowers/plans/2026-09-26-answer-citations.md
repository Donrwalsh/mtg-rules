# Answer Citations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generated answers cite numbered context sources inline. The server validates those citations and any raw rule numbers, and the frontend renders them as links with hover previews, a Sources list, and a `/rules/[id]` page.

**Architecture:**
- `build_context` numbers every retrieved result and returns the number → `QueryResult` mapping.
- The LLM is prompted to cite `[n]`.
- `citations.py` parses and validates markers in the plain-text answer. It is provider-agnostic.
- A shared in-memory `RulesIndex` validates raw rule references and serves `GET /api/v1/rules/{id}`.
- Ingestion carries `scryfall_uri`. The embed stage carries `published_at` and `scryfall_uri` in payloads, with a `payload_hash` so metadata-only changes update payloads without re-embedding.

**Tech Stack:** FastAPI, Pydantic v2, SQLAlchemy 2 + Alembic, qdrant-client, Typer; SvelteKit 2 / Svelte 4 with adapter-static behind nginx.

**Spec:** `docs/superpowers/specs/2026-09-26-answer-citations-design.md`

## Global Constraints

- Nothing depends on Groq-specific features. Answerers keep `generate(query: str, context: str) -> str`.
- Model output is never rendered with `{@html}`.
- Rule links point to our own `/rules/{rule_id}` route. Card and ruling links use `scryfall_uri`.
- Existing `QueryResponse` fields (`query`, `results`, `answer`) stay unchanged.
- Run each test suite from its own package directory (`cd mtg-api && python -m pytest`). Running them together collides on module names.
- Use conventional commit messages (`feat(mtg-api): ...`) ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Line length is 100 (ruff).

---

### Task 1: Ingestion keeps `scryfall_uri`

**Files:**
- Modify: `mtg-worker/mtg-ingestion/src/mtg_ingestion/models.py` (Card)
- Modify: `mtg-worker/mtg-ingestion/src/mtg_ingestion/parse/cards.py`
- Test: `mtg-worker/mtg-ingestion/tests/test_parse_cards_rulings.py`

**Interfaces:**
- Produces: the parsed card row gains `scryfall_uri: str | None`, with no query string. It is excluded from `content_hash`.

- [ ] **Step 1: Failing tests.** Add `"scryfall_uri": "https://scryfall.com/card/m10/155/llanowar-elves?utm_source=api"` to the `abc-123` raw card, then add:

```python
def test_parse_cards_keeps_scryfall_uri_without_query_string(tmp_path: Path) -> None:
    raw = tmp_path / "oracle_cards.jsonl.gz"
    with gzip.open(raw, "wt", encoding="utf-8") as f:
        for row in RAW_CARDS:
            f.write(json.dumps(row) + "\n")
    cards = {c.oracle_id: c for c in parse_cards_file(raw)}
    assert cards["abc-123"].scryfall_uri == "https://scryfall.com/card/m10/155/llanowar-elves"
    assert cards["def-456"].scryfall_uri is None


def test_scryfall_uri_is_not_part_of_content_hash() -> None:
    base = dict(oracle_id="o", name="N", oracle_text="t", type_line="x", mana_cost=None)
    assert Card(**base).content_hash == Card(**base, scryfall_uri="https://a").content_hash
```

- [ ] **Step 2:** Run `cd mtg-worker/mtg-ingestion && python -m pytest -q`. Expected: FAIL (`Card` has no `scryfall_uri`).
- [ ] **Step 3: Implement.** In `Card`, add `scryfall_uri: str | None = None`, leaving `model_post_init` unchanged. In `parse/cards.py`:

```python
def _strip_query(uri: str | None) -> str | None:
    # Scryfall appends ?utm_source=api to every API-served link.
    return uri.split("?", 1)[0] if uri else None
```

Pass `scryfall_uri=_strip_query(raw.get("scryfall_uri"))` to `Card(...)`.
- [ ] **Step 4:** Tests pass.
- [ ] **Step 5:** Commit `feat(mtg-ingestion): keep each card's scryfall_uri`.

### Task 2: Embed payloads carry `scryfall_uri` and `published_at`

**Files:**
- Modify: `mtg-worker/mtg-embed/src/mtg_embed/sources/cards.py`
- Modify: `mtg-worker/mtg-embed/src/mtg_embed/sources/rulings.py`
- Test: `tests/test_sources_cards.py`, `tests/test_sources_rulings.py`

**Interfaces:**
- Produces payload keys: oracle `scryfall_uri`; ruling `published_at`, `scryfall_uri`. The value is `None` when absent.

- [ ] **Step 1: Failing tests.** Add `"scryfall_uri": "https://scryfall.com/card/lea/161/lightning-bolt"` to Lightning Bolt in both fixture files, then add:

```python
# test_sources_cards.py
def test_card_payload_carries_scryfall_uri(tmp_path):
    chunks = {c.payload["card_name"]: c for c in load_card_chunks(_write_rows(tmp_path))}
    assert chunks["Lightning Bolt"].payload["scryfall_uri"] == "https://scryfall.com/card/lea/161/lightning-bolt"
    assert chunks["Static Orb"].payload["scryfall_uri"] is None

# test_sources_rulings.py
def test_ruling_payload_carries_published_at_and_card_scryfall_uri(tmp_path):
    chunks, _ = load_ruling_chunks(
        _write(tmp_path, "rulings.jsonl", RULING_ROWS), _write(tmp_path, "cards.jsonl", CARD_ROWS)
    )
    assert chunks[0].payload["published_at"] == "2020-01-01"
    assert chunks[1].payload["published_at"] == "2020-01-02"
    assert chunks[0].payload["scryfall_uri"] == "https://scryfall.com/card/lea/161/lightning-bolt"
```

- [ ] **Step 2:** Run `cd mtg-worker/mtg-embed && python -m pytest -q`. Expected: FAIL with KeyError.
- [ ] **Step 3: Implement.** Add `"scryfall_uri": row.get("scryfall_uri")` to the cards payload. Add `"published_at": row.get("published_at")` and `"scryfall_uri": card.get("scryfall_uri")` to the rulings payload.
- [ ] **Step 4:** Tests pass. **Step 5:** Commit `feat(mtg-embed): add scryfall_uri and ruling published_at to payloads`.

### Task 3: `payload_hash` and a payload-only update path

**Files:**
- Modify: `mtg-worker/mtg-embed/src/mtg_embed/models.py`
- Modify: `mtg-worker/mtg-embed/src/mtg_embed/qdrant_store.py`
- Modify: `mtg-worker/mtg-embed/src/mtg_embed/pipeline.py`
- Modify: `mtg-worker/mtg-embed/src/mtg_embed/cli.py`
- Test: `tests/test_qdrant_store.py`, `tests/test_pipeline.py`, `tests/test_cli.py`

**Interfaces:**
- Produces:
  - `models.payload_hash(payload: dict) -> str`
  - `EmbeddableChunk.stored_payload -> dict` (payload plus `payload_hash`)
  - `qdrant_store.StoredHashes(content_hash: str, payload_hash: str | None)`
  - `QdrantStore.existing_hashes(ids) -> dict[str, StoredHashes]`
  - `QdrantStore.overwrite_payloads(chunks) -> None`
  - `RunSummary.payload_updated: int = 0`

- [ ] **Step 1: Failing tests.**
  - `test_qdrant_store.py`: change the two round-trip assertions to `== {chunk.point_id: StoredHashes("hash-1", payload_hash(chunk.payload))}`. Change the missing-key test to `result == {chunk_with_hash.point_id: StoredHashes("hash-1", payload_hash(chunk_with_hash.payload))}`.
  - Add a test that a point written without `payload_hash` returns `payload_hash=None`.
  - Add a test that `overwrite_payloads` replaces the payload and leaves vectors untouched. Retrieve `with_vectors=True` and compare the dense vector.
  - `test_pipeline.py`: extend `_chunk(point_id, content_hash, extra=None)` so that `extra` is merged into the payload, then add:

```python
def test_changed_payload_with_unchanged_content_hash_updates_payload_without_embedding():
    store = _fresh_store()
    model = FakeModel()
    embedder = Embedder(model, batch_size=32)
    sparse_embedder = SparseEmbedder(FakeSparseModel())
    point_id = "11111111-1111-1111-1111-111111111111"

    embed_and_store([_chunk(point_id, "h1")], store, embedder, sparse_embedder)
    summary = embed_and_store(
        [_chunk(point_id, "h1", {"scryfall_uri": "https://x"})], store, embedder, sparse_embedder
    )

    assert model.encode_calls == 1
    assert summary.embedded == 0
    assert summary.payload_updated == 1
    assert summary.skipped_unchanged == 0
    stored = store._client.retrieve("pipeline_test", ids=[point_id], with_payload=True)[0].payload
    assert stored["scryfall_uri"] == "https://x"


def test_point_stored_without_payload_hash_gets_payload_rewritten_once():
    # Simulates points embedded before payload_hash existed.
    store = _fresh_store()
    point_id = "11111111-1111-1111-1111-111111111111"
    chunk = _chunk(point_id, "h1")
    store._client.upsert(
        "pipeline_test",
        points=[qmodels.PointStruct(
            id=point_id,
            vector={"dense": [1.0, 0, 0, 0], "sparse": qmodels.SparseVector(indices=[0], values=[1.0])},
            payload=chunk.payload,
        )],
    )
    embedder = Embedder(FakeModel(), batch_size=32)
    sparse_embedder = SparseEmbedder(FakeSparseModel())
    first = embed_and_store([chunk], store, embedder, sparse_embedder)
    second = embed_and_store([chunk], store, embedder, sparse_embedder)
    assert (first.embedded, first.payload_updated) == (0, 1)
    assert (second.payload_updated, second.skipped_unchanged) == (0, 1)
```

  - `test_cli.py`: assert that `"payload_updated=0"` is in the formatted line.
- [ ] **Step 2:** Tests FAIL.
- [ ] **Step 3: Implement.**

```python
# models.py
def payload_hash(payload: dict[str, Any]) -> str:
    """Hash of everything stored alongside the vectors. Lets the pipeline
    notice metadata-only changes (a new payload field, a changed link)
    that content_hash -- which covers only embedded text -- can't see."""
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()

# EmbeddableChunk
    @property
    def stored_payload(self) -> dict[str, Any]:
        return {**self.payload, "payload_hash": payload_hash(self.payload)}
```

```python
# qdrant_store.py
@dataclass(frozen=True)
class StoredHashes:
    content_hash: str
    payload_hash: str | None

    def existing_hashes(self, point_ids):   # with_payload=["content_hash", "payload_hash"]
        return {str(p.id): StoredHashes(p.payload["content_hash"], p.payload.get("payload_hash"))
                for p in points if p.payload and "content_hash" in p.payload}

    # upsert: payload=chunk.stored_payload

    def overwrite_payloads(self, chunks: list[EmbeddableChunk]) -> None:
        if not chunks:
            return
        self._client.batch_update_points(
            collection_name=self._collection_name,
            update_operations=[
                qmodels.OverwritePayloadOperation(
                    overwrite_payload=qmodels.OverwritePayload(
                        payload=c.stored_payload, points=[c.point_id]
                    )
                )
                for c in chunks
            ],
        )
```

```python
# pipeline.py loop body
        existing = store.existing_hashes([c.point_id for c in batch])
        to_embed, to_repayload = [], []
        for c in batch:
            stored = existing.get(c.point_id)
            if stored is None or stored.content_hash != c.content_hash:
                to_embed.append(c)
            elif stored.payload_hash != payload_hash(c.payload):
                to_repayload.append(c)
        skipped += len(batch) - len(to_embed) - len(to_repayload)
        ...embed as before...
        store.overwrite_payloads(to_repayload)
        payload_updated += len(to_repayload)
```

The CLI summary line gains ` payload_updated={summary.payload_updated}`, and the TOTAL line sums it.
- [ ] **Step 4:** Tests pass. **Step 5:** Commit `feat(mtg-embed): update stale payloads without re-embedding via payload_hash`.

### Task 4: Shared `RulesIndex`

**Files:**
- Create: `mtg-api/src/mtg_api/rules_index.py`
- Modify: `mtg-api/src/mtg_api/keyword_matcher.py` (remove `load_keyword_matcher`)
- Modify: `mtg-api/src/mtg_api/main.py`
- Test: create `mtg-api/tests/test_rules_index.py`; modify `test_keyword_matcher.py` and `test_lifespan.py`

**Interfaces:**
- Produces:
  - `RulesIndex(rules: list[dict], ingested_at: str | None = None)` with `.rules`, `.ingested_at`, `.get(id) -> dict | None`, `__contains__`, `.children(id) -> list[dict]`, `.ancestors(id) -> list[dict]` (top-level first)
  - `load_rules_index(path) -> RulesIndex`
  - `main.get_rules_index()`

- [ ] **Step 1: Failing tests.**

```python
import json
from mtg_api.rules_index import RulesIndex, load_rules_index

RULES = [
    {"rule_id": "702", "text": "Keyword Abilities", "parent_id": None},
    {"rule_id": "702.11", "text": "Hexproof", "parent_id": "702"},
    {"rule_id": "702.11a", "text": "Hexproof is a static ability.", "parent_id": "702.11"},
    {"rule_id": "702.11b", "text": "Can't be targeted by opponents.", "parent_id": "702.11"},
    {"rule_id": "704.5b", "text": "Empty library loses.", "parent_id": "704.5"},  # orphan parent
]

def test_get_and_contains():
    index = RulesIndex(RULES)
    assert index.get("702.11b")["text"] == "Can't be targeted by opponents."
    assert "702.11" in index and "999.9" not in index and index.get("999.9") is None

def test_children_are_direct_only_in_file_order():
    index = RulesIndex(RULES)
    assert [r["rule_id"] for r in index.children("702.11")] == ["702.11a", "702.11b"]
    assert [r["rule_id"] for r in index.children("702")] == ["702.11"]
    assert index.children("702.11b") == []

def test_ancestors_top_level_first_and_stop_at_missing_parent():
    index = RulesIndex(RULES)
    assert [r["rule_id"] for r in index.ancestors("702.11b")] == ["702", "702.11"]
    assert index.ancestors("702") == [] and index.ancestors("704.5b") == []

def test_load_reads_jsonl_and_date_from_file_name(tmp_path):
    path = tmp_path / "rules_2026-08-25.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in RULES) + "\n", encoding="utf-8")
    index = load_rules_index(path)
    assert index.ingested_at == "2026-08-25" and "702.11a" in index
```

  In `test_keyword_matcher.py`, replace `test_load_keyword_matcher_reads_jsonl` with an equivalent built via `KeywordMatcher(load_rules_index(path).rules)`. In `test_lifespan.py`, add `get_rules_index` to the monkeypatched set.
- [ ] **Step 2:** Tests FAIL (import error).
- [ ] **Step 3: Implement.**

```python
from __future__ import annotations

import json
import re
from pathlib import Path

_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


class RulesIndex:
    """In-memory Comprehensive Rules, keyed by rule_id. Shared by the
    keyword matcher, rule-reference validation, and the rules endpoint so
    the rules file is read once."""

    def __init__(self, rules: list[dict], ingested_at: str | None = None):
        self.rules = rules
        self.ingested_at = ingested_at
        self._by_id = {r["rule_id"]: r for r in rules}
        self._children: dict[str, list[dict]] = {}
        for rule in rules:
            if rule.get("parent_id"):
                self._children.setdefault(rule["parent_id"], []).append(rule)

    def get(self, rule_id: str) -> dict | None:
        return self._by_id.get(rule_id)

    def __contains__(self, rule_id: object) -> bool:
        return rule_id in self._by_id

    def children(self, rule_id: str) -> list[dict]:
        return list(self._children.get(rule_id, []))

    def ancestors(self, rule_id: str) -> list[dict]:
        chain: list[dict] = []
        rule = self._by_id.get(rule_id)
        parent_id = rule.get("parent_id") if rule else None
        while parent_id in self._by_id:
            parent = self._by_id[parent_id]
            chain.append(parent)
            parent_id = parent.get("parent_id")
        chain.reverse()
        return chain


def load_rules_index(rules_path: Path) -> RulesIndex:
    with rules_path.open("r", encoding="utf-8") as f:
        rules = [json.loads(line) for line in f if line.strip()]
    match = _DATE_RE.search(rules_path.name)
    return RulesIndex(rules, match.group(0) if match else None)
```

  In `main.py`:
  - Add `get_rules_index()` (`lru_cache`, `_latest(settings.parsed_dir, "rules_*.jsonl")`).
  - Make `get_keyword_matcher()` return `KeywordMatcher(get_rules_index().rules)`.
  - Call `get_rules_index()` in `lifespan`.
  - Delete `load_keyword_matcher` along with its now-unused `json`/`Path` imports.
- [ ] **Step 4:** Tests pass. **Step 5:** Commit `refactor(mtg-api): share one in-memory RulesIndex across features`.

### Task 5: Numbered context and citation prompt

**Files:**
- Modify: `mtg-api/src/mtg_api/models.py`, `mtg-api/src/mtg_api/llm.py`, `mtg-api/src/mtg_api/main.py`
- Test: `mtg-api/tests/test_llm.py`, `mtg-api/tests/test_query.py`

**Interfaces:**
- Produces:
  - `QueryResult` gains `rule_id`, `card_name`, `published_at`, `scryfall_uri` (all `str | None = None`) and `cited: bool = False`
  - `llm.source_label(result) -> str`
  - `llm.build_context(results) -> tuple[str, dict[int, QueryResult]]`

- [ ] **Step 1: Failing tests.** Replace the two `build_context` tests in `test_llm.py`:

```python
def _r(source, title, text, **kw):
    return QueryResult(source=source, title=title, text=text, score=1.0, match_type="x", **kw)

def test_source_label_per_source_type():
    assert source_label(_r("rule", "702.11b", "t", rule_id="702.11b")) == "Rule 702.11b"
    assert source_label(_r("card", "Lightning Bolt", "t", card_name="Lightning Bolt")) == "Card — Lightning Bolt"
    assert source_label(_r("oracle", "Shock", "t")) == "Card — Shock"
    assert (source_label(_r("ruling", "Homing Lightning", "t", published_at="2018-01-19"))
            == "Ruling — Homing Lightning (2018-01-19)")
    assert source_label(_r("ruling", "Homing Lightning", "t")) == "Ruling — Homing Lightning"

def test_build_context_numbers_blocks_in_order_and_returns_mapping():
    results = [_r("rule", "702.11b", "Hexproof text.", rule_id="702.11b"),
               _r("card", "Lightning Bolt", "Deals 3.", card_name="Lightning Bolt")]
    context, sources = build_context(results)
    assert context == "[1] Rule 702.11b: Hexproof text.\n\n[2] Card — Lightning Bolt: Deals 3."
    assert sources == {1: results[0], 2: results[1]}
    assert sources[1] is results[0]

def test_build_context_empty_list():
    assert build_context([]) == ("", {})

def test_system_prompt_demands_numbered_citations():
    assert "[1][3]" in _SYSTEM_PROMPT and "only numbers that appear" in _SYSTEM_PROMPT
```

  In `test_query.py`, assert that the new fields are filled:
  - A card-match result carries `card_name` and `scryfall_uri` from the card row.
  - A vector rule hit carries `rule_id`.
  - A card-ruling hit carries `published_at` and `scryfall_uri` from the payload.
- [ ] **Step 2:** Tests FAIL.
- [ ] **Step 3: Implement** `source_label` and `build_context` as in the spec. Write the system prompt as:

```python
_SYSTEM_PROMPT = """You are a Magic: The Gathering rules assistant. Answer the user's question \
using only the numbered context sources below (Comprehensive Rules excerpts, official \
rulings, and card text).

Citation rules:
- Cite every factual claim with the bracketed number of the source that supports it, like [2].
- Use only numbers that appear in the context. Never cite a number that is not listed, \
and never quote rule numbers from memory.
- Several sources may support one claim: write [1][3] or [1, 3].
- When explaining why something works, prefer rules and rulings over card text.
- If the context does not cover the question, say so plainly, with no citations, \
instead of guessing.

Example (format only -- these sources are not part of your context):
Context:
[1] Rule 702.19b: The controller of an attacking creature with trample first assigns \
damage to the creature(s) blocking it. Once all those blocking creatures are assigned \
lethal damage, any excess damage is assigned as its controller chooses among those \
blocking creatures and the player, planeswalker, or battle the creature is attacking.
[2] Card — Colossal Dreadmaw: Trample
Question: My Colossal Dreadmaw is blocked by a 1/1. Does any damage get through?
Answer: Yes. Colossal Dreadmaw has trample [2], so you assign lethal damage to the \
blocker and the rest to the defending player [1]."""
```

  In `main.query`, fill the new fields at all four `QueryResult` construction sites:
  - Card match: `card_name=card["name"]`, `scryfall_uri=card.get("scryfall_uri")`.
  - Card ruling: `card_name`, `published_at`, `scryfall_uri` from the payload.
  - Keyword rule: `rule_id=rule["rule_id"]`.
  - Vector hit: `rule_id`, `card_name`, `published_at`, `scryfall_uri` via `payload.get`.

  Change the call to `context, sources = build_context(all_results)`.
- [ ] **Step 4:** Tests pass. **Step 5:** Commit `feat(mtg-api): number context sources and prompt for bracketed citations`.

### Task 6: `citations.py` (parse, validate, build)

**Files:**
- Create: `mtg-api/src/mtg_api/citations.py`
- Modify: `mtg-api/src/mtg_api/models.py` (add `Citation`, `CitationStats`)
- Test: create `mtg-api/tests/test_citations.py`

**Interfaces:**
- Consumes: `source_label`, `RulesIndex`, `QueryResult`.
- Produces:
  - `parse_citations(answer, valid_numbers: set[int]) -> CitationParse(answer, cited_numbers, invalid_count)`
  - `find_rule_references(text, rules_index) -> list[str]`
  - `build_citations(cited_numbers, sources) -> list[Citation]`
  - `cite_answer(answer, sources, rules_index) -> CitedAnswer(answer, citations, rule_references, stats)`, which also sets `sources[n].cited = True`

- [ ] **Step 1: Failing tests** (`caplog` checks the warnings):

```python
import logging
from mtg_api.citations import build_citations, cite_answer, find_rule_references, parse_citations
from mtg_api.models import QueryResult
from mtg_api.rules_index import RulesIndex

VALID = {1, 2, 3}

def test_single_marker():
    p = parse_citations("Bolt deals 3 [1].", VALID)
    assert (p.answer, p.cited_numbers, p.invalid_count) == ("Bolt deals 3 [1].", [1], 0)

def test_adjacent_markers():
    assert parse_citations("x [1][3].", VALID).cited_numbers == [1, 3]

def test_comma_separated_marker_is_kept_and_normalized():
    p = parse_citations("x [3,1].", VALID)
    assert p.cited_numbers == [3, 1] and p.answer == "x [3, 1]."

def test_repeated_citation_counted_once():
    assert parse_citations("a [2]. b [2].", VALID).cited_numbers == [2]

def test_out_of_range_removed_logged_and_counted(caplog):
    with caplog.at_level(logging.WARNING, logger="mtg_api.citations"):
        p = parse_citations("a [1][9]. b [0]. c [2, 7].", VALID)
    assert p.answer == "a [1]. b. c [2]."
    assert p.cited_numbers == [1, 2] and p.invalid_count == 3
    assert "9" in caplog.text

def test_malformed_markers_removed_and_counted():
    p = parse_citations("a [1-3]. b [^2]. c [1;2].", VALID)
    assert p.answer == "a. b. c." and p.cited_numbers == [] and p.invalid_count == 3

def test_no_citations():
    p = parse_citations("No idea.", VALID)
    assert (p.answer, p.cited_numbers, p.invalid_count) == ("No idea.", [], 0)

def test_non_citation_brackets_untouched():
    text = "See [Rule 702.11b] and pay {2}{R}. [ ] [a]"
    assert parse_citations(text, VALID).answer == text

def test_removal_keeps_line_breaks():
    assert parse_citations("a.\n[9] b", VALID).answer == "a.\n b"

INDEX = RulesIndex([{"rule_id": "702.11b", "text": "t", "parent_id": "702.11"},
                    {"rule_id": "704.5b", "text": "t", "parent_id": "704.5"},
                    {"rule_id": "100.5", "text": "t", "parent_id": "100"}])

def test_rule_references_valid_in_order_distinct():
    assert find_rule_references("Per 704.5b and 702.11b, and again 704.5b.", INDEX) == ["704.5b", "702.11b"]

def test_rule_references_invalid_logged_not_returned(caplog):
    with caplog.at_level(logging.WARNING, logger="mtg_api.citations"):
        assert find_rule_references("See rule 999.9z.", INDEX) == []
    assert "999.9z" in caplog.text

def test_rule_references_ignore_prices_dates_versions():
    text = "It costs $100.50, printed 2018.01.19, v100.5.2, 100.5x, 12100.5"
    assert find_rule_references(text, INDEX) == []

def _r(source, **kw):
    return QueryResult(source=source, title=kw.pop("title", "T"), text="body", score=1.0,
                       match_type="x", **kw)

def test_build_citations_fields_per_type():
    sources = {
        1: _r("rule", title="702.11b", rule_id="702.11b"),
        2: _r("oracle", title="Shock", card_name="Shock", oracle_id="o1",
              scryfall_uri="https://scryfall.com/card/x/1/shock"),
        3: _r("ruling", title="Shock", card_name="Shock", oracle_id="o1",
              published_at="2020-01-01", scryfall_uri="https://scryfall.com/card/x/1/shock"),
        4: _r("card", title="Bolt", card_name="Bolt"),
    }
    by_n = {c.number: c for c in build_citations([3, 1, 2, 4], sources)}
    assert [c.number for c in build_citations([3, 1], sources)] == [1, 3]
    assert (by_n[1].source_type, by_n[1].url, by_n[1].title) == ("rule", "/rules/702.11b", "Rule 702.11b")
    assert (by_n[2].source_type, by_n[2].url) == ("card", "https://scryfall.com/card/x/1/shock")
    assert by_n[3].published_at == "2020-01-01" and by_n[3].title == "Ruling — Shock (2020-01-01)"
    assert by_n[4].url is None and by_n[1].card_name is None and by_n[2].rule_id is None

def test_cite_answer_sets_flags_and_stats():
    sources = {1: _r("rule", title="702.11b", rule_id="702.11b"), 2: _r("card", title="Bolt")}
    result = cite_answer("Yes [1][5], see 702.11b.", sources, INDEX)
    assert result.answer == "Yes [1], see 702.11b."
    assert sources[1].cited and not sources[2].cited
    assert result.rule_references == ["702.11b"]
    assert result.stats.model_dump() == {"cited_count": 1, "invalid_count": 1, "uncited_answer": False}

def test_cite_answer_uncited():
    assert cite_answer("Not covered.", {1: _r("rule", title="1")}, INDEX).stats.uncited_answer is True
```

- [ ] **Step 2:** Tests FAIL.
- [ ] **Step 3: Implement.** Add these to `models.py`:

```python
class Citation(BaseModel):
    number: int
    source_type: str
    title: str
    rule_id: str | None = None
    card_name: str | None = None
    oracle_id: str | None = None
    text: str
    url: str | None = None
    published_at: str | None = None


class CitationStats(BaseModel):
    cited_count: int = 0
    invalid_count: int = 0
    uncited_answer: bool = False
```

Then `citations.py`:

```python
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from mtg_api.llm import source_label
from mtg_api.models import Citation, CitationStats, QueryResult
from mtg_api.rules_index import RulesIndex

logger = logging.getLogger(__name__)

# Any bracket group made only of digits and citation-ish punctuation, with at
# least one digit, plus the horizontal whitespace before it (so a dropped
# marker doesn't leave a double space). Newlines are never consumed.
_MARKER_RE = re.compile(r"([ \t]*)(\[[\d\s,;^\-–]*\d[\d\s,;^\-–]*\])")
_WELL_FORMED_RE = re.compile(r"\[\s*\d+(?:\s*,\s*\d+)*\s*\]")
# \b\d{3}\.\d+[a-z]?\b, hardened against prices ($100.50), dotted dates
# (2018.01.19), versions (100.5.2), and longer numbers (12100.5).
_RULE_REF_RE = re.compile(r"(?<![\d.$€£])\d{3}\.\d+[a-z]?(?![a-z\d]|\.\d)")


@dataclass
class CitationParse:
    answer: str
    cited_numbers: list[int]
    invalid_count: int


@dataclass
class CitedAnswer:
    answer: str
    citations: list[Citation]
    rule_references: list[str]
    stats: CitationStats


def parse_citations(answer: str, valid_numbers: set[int]) -> CitationParse:
    cited: list[int] = []
    invalid = 0

    def replace(match: re.Match) -> str:
        nonlocal invalid
        leading, marker = match.group(1), match.group(2)
        if not _WELL_FORMED_RE.fullmatch(marker):
            logger.warning("Dropping malformed citation marker %r", marker)
            invalid += 1
            return ""
        kept: list[int] = []
        for number in (int(n) for n in re.findall(r"\d+", marker)):
            if number not in valid_numbers:
                logger.warning("Dropping out-of-range citation [%d]", number)
                invalid += 1
                continue
            if number not in kept:
                kept.append(number)
            if number not in cited:
                cited.append(number)
        if not kept:
            return ""
        return f"{leading}[{', '.join(str(n) for n in kept)}]"

    cleaned = _MARKER_RE.sub(replace, answer)
    return CitationParse(answer=cleaned, cited_numbers=cited, invalid_count=invalid)


def find_rule_references(text: str, rules_index: RulesIndex) -> list[str]:
    found: list[str] = []
    for match in _RULE_REF_RE.finditer(text):
        rule_id = match.group(0)
        if rule_id not in rules_index:
            logger.warning("Answer mentions unknown rule %r", rule_id)
        elif rule_id not in found:
            found.append(rule_id)
    return found


def _citation(number: int, result: QueryResult) -> Citation:
    if result.source == "rule":
        rule_id = result.rule_id or result.title
        return Citation(number=number, source_type="rule", title=source_label(result),
                        rule_id=rule_id, text=result.text, url=f"/rules/{rule_id}")
    source_type = "card" if result.source == "oracle" else result.source
    return Citation(
        number=number, source_type=source_type, title=source_label(result),
        card_name=result.card_name or result.title, oracle_id=result.oracle_id,
        text=result.text, url=result.scryfall_uri,
        published_at=result.published_at if source_type == "ruling" else None,
    )


def build_citations(cited_numbers: list[int], sources: dict[int, QueryResult]) -> list[Citation]:
    return [_citation(n, sources[n]) for n in sorted(cited_numbers)]


def cite_answer(answer: str, sources: dict[int, QueryResult], rules_index: RulesIndex) -> CitedAnswer:
    parsed = parse_citations(answer, set(sources))
    for number in parsed.cited_numbers:
        sources[number].cited = True
    return CitedAnswer(
        answer=parsed.answer,
        citations=build_citations(parsed.cited_numbers, sources),
        rule_references=find_rule_references(parsed.answer, rules_index),
        stats=CitationStats(
            cited_count=len(parsed.cited_numbers),
            invalid_count=parsed.invalid_count,
            uncited_answer=not parsed.cited_numbers,
        ),
    )
```

- [ ] **Step 4:** Tests pass (fix expected strings if whitespace handling differs, keeping the intent). **Step 5:** Commit `feat(mtg-api): parse and validate answer citations and raw rule references`.

### Task 7: Persist citations (migration 0002 + history)

**Files:**
- Create: `mtg-api/alembic/versions/0002_add_query_history_citations.py`
- Modify: `mtg-api/src/mtg_api/history.py`
- Test: `mtg-api/tests/test_history.py`; create `mtg-api/tests/test_migrations.py`

**Interfaces:**
- Produces: `save_history(..., citations: list[dict] | None = None, citation_stats: dict | None = None, rule_references: list[str] | None = None)`. `list_history` rows include those keys.

- [ ] **Step 1: Failing tests.** In `test_history.py`, one test round-trips all three new fields, and one checks that they default to `None`. Create `test_migrations.py`:

```python
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from mtg_api.config import settings

API_ROOT = Path(__file__).resolve().parents[1]
NEW_COLUMNS = {"citations", "citation_stats", "rule_references"}


def _config() -> Config:
    # No ini file on purpose: env.py only calls fileConfig() when one is
    # given, and fileConfig would disable loggers other tests assert on.
    cfg = Config()
    cfg.set_main_option("script_location", str(API_ROOT / "alembic"))
    return cfg


def _columns(dsn: str) -> set[str]:
    return {c["name"] for c in inspect(create_engine(dsn)).get_columns("query_history")}


def test_0002_adds_and_removes_citation_columns(tmp_path, monkeypatch):
    dsn = f"sqlite:///{(tmp_path / 'history.db').as_posix()}"
    monkeypatch.setattr(settings, "postgres_dsn", dsn)
    cfg = _config()

    command.upgrade(cfg, "head")
    assert NEW_COLUMNS <= _columns(dsn)

    command.downgrade(cfg, "0001")
    assert not NEW_COLUMNS & _columns(dsn)
```

- [ ] **Step 2:** Run `pip install alembic` if it's missing locally (it's a declared dependency). Tests FAIL.
- [ ] **Step 3: Implement.**
  - `history.py`: add three `Column(..., JSON, nullable=True)` and pass them through in `save_history`.
  - Migration:

```python
"""add citation columns to query_history

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-26

"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

_COLUMNS = ("citations", "citation_stats", "rule_references")


def upgrade() -> None:
    for name in _COLUMNS:
        op.add_column("query_history", sa.Column(name, sa.JSON(), nullable=True))


def downgrade() -> None:
    # batch mode so the downgrade also works on SQLite (tests).
    with op.batch_alter_table("query_history") as batch:
        for name in _COLUMNS:
            batch.drop_column(name)
```

- [ ] **Step 4:** Tests pass. **Step 5:** Commit `feat(mtg-api): add query_history citation columns (migration 0002)`.

### Task 8: `/api/v1/query` returns and saves citations

**Files:**
- Modify: `mtg-api/src/mtg_api/models.py` (QueryResponse), `mtg-api/src/mtg_api/main.py`
- Test: `mtg-api/tests/test_query.py`, `mtg-api/tests/test_queries_endpoint.py`

**Interfaces:**
- Consumes: `cite_answer`, `get_rules_index`, and `save_history` with the new keyword arguments.
- Produces: `QueryResponse.citations: list[Citation]`, `.rule_references: list[str]`, `.citation_stats: CitationStats` (all with default factories).

- [ ] **Step 1: Failing tests.** `_override` also sets `get_rules_index` to `RulesIndex(rules or [])`.

```python
def test_query_returns_validated_citations_and_persists_them():
    cards = [{"oracle_id": "oid-1", "name": "Lightning Bolt", "oracle_text": "Deals 3 damage.",
              "scryfall_uri": "https://scryfall.com/card/lea/161/lightning-bolt"}]
    dense_points = [_FakeHit("p1", 0.9, {"source_type": "rule", "rule_id": "115.1", "text": "Targets."})]
    engine = memory_engine()
    answer = "Bolt deals 3 [1]. Hexproof only stops opponents [4][99], see 702.11b and 999.9z."
    _override(cards=cards, rules=HEXPROOF_RULES, dense_points=dense_points,
              answerer=_FakeAnswerer(answer=answer), engine=engine)
    try:
        resp = TestClient(app).post(
            "/api/v1/query", json={"query": "can Lightning Bolt target my hexproof creature"})
    finally:
        app.dependency_overrides.clear()

    body = resp.json()
    # results: 1 Bolt, 2 702.11, 3 702.11a, 4 702.11b, 5 115.1
    assert body["answer"] == "Bolt deals 3 [1]. Hexproof only stops opponents [4], see 702.11b and 999.9z."
    assert [c["number"] for c in body["citations"]] == [1, 4]
    bolt, rule = body["citations"]
    assert (bolt["source_type"], bolt["title"], bolt["url"]) == (
        "card", "Card — Lightning Bolt", "https://scryfall.com/card/lea/161/lightning-bolt")
    assert (rule["source_type"], rule["rule_id"], rule["url"]) == ("rule", "702.11b", "/rules/702.11b")
    assert [r["cited"] for r in body["results"]] == [True, False, False, True, False]
    assert body["citation_stats"] == {"cited_count": 2, "invalid_count": 1, "uncited_answer": False}
    assert body["rule_references"] == ["702.11b"]

    row = list_history(engine)[0]
    assert [c["number"] for c in row["citations"]] == [1, 4]
    assert row["citation_stats"]["invalid_count"] == 1
    assert row["rule_references"] == ["702.11b"]
    assert [r["cited"] for r in row["results"]] == [True, False, False, True, False]


def test_query_flags_uncited_answer():
    _override(answerer=_FakeAnswerer(answer="The context doesn't cover that."))
    try:
        body = TestClient(app).post("/api/v1/query", json={"query": "best standard deck"}).json()
    finally:
        app.dependency_overrides.clear()
    assert body["citations"] == [] and body["citation_stats"]["uncited_answer"] is True


def test_query_failed_generation_has_empty_citations():
    _override(answerer=_FakeAnswerer(raises=RuntimeError("down")))
    try:
        body = TestClient(app).post("/api/v1/query", json={"query": "trample"}).json()
    finally:
        app.dependency_overrides.clear()
    assert body["citations"] == [] and body["rule_references"] == []
    assert body["citation_stats"] == {"cited_count": 0, "invalid_count": 0, "uncited_answer": False}
```

  In `test_queries_endpoint.py`, check that saved citation fields come back from `GET /api/v1/queries`.
- [ ] **Step 2:** Tests FAIL.
- [ ] **Step 3: Implement.** In `main.query`, add a `rules_index: RulesIndex = Depends(get_rules_index)` parameter. After generation:

```python
    cited = None
    if answer is not None:
        cited = cite_answer(answer, sources, rules_index)
        answer = cited.answer
    citations = cited.citations if cited else []
    rule_references = cited.rule_references if cited else []
    citation_stats = cited.stats if cited else CitationStats()
```

  `cite_answer` must run before `save_history`, since `results` is dumped after the `cited` flags are set. Pass `citations=[c.model_dump() for c in citations]`, `citation_stats=citation_stats.model_dump()`, and `rule_references=rule_references` to `save_history`, and put the same values on `QueryResponse`.
- [ ] **Step 4:** Tests pass. **Step 5:** Commit `feat(mtg-api): return and persist validated citations from /api/v1/query`.

### Task 9: `GET /api/v1/rules/{rule_id}`

**Files:**
- Modify: `mtg-api/src/mtg_api/main.py`
- Test: create `mtg-api/tests/test_rules_endpoint.py`

- [ ] **Step 1: Failing tests.**

```python
from fastapi.testclient import TestClient
from mtg_api.main import app, get_rules_index
from mtg_api.rules_index import RulesIndex

RULES = [
    {"rule_id": "702", "text": "Keyword Abilities", "parent_id": None},
    {"rule_id": "702.11", "text": "Hexproof", "parent_id": "702"},
    {"rule_id": "702.11a", "text": "Hexproof is a static ability.", "parent_id": "702.11"},
    {"rule_id": "702.11b", "text": "Can't be targeted by opponents.", "parent_id": "702.11"},
]

def _get(path):
    app.dependency_overrides[get_rules_index] = lambda: RulesIndex(RULES, "2026-08-25")
    try:
        return TestClient(app).get(path)
    finally:
        app.dependency_overrides.clear()

def test_rule_found_with_ancestors_and_ingest_date():
    body = _get("/api/v1/rules/702.11b").json()
    assert body["rule_id"] == "702.11b" and body["text"] == "Can't be targeted by opponents."
    assert body["ancestors"] == [{"rule_id": "702", "text": "Keyword Abilities"},
                                 {"rule_id": "702.11", "text": "Hexproof"}]
    assert body["subrules"] == [] and body["rules_ingested_at"] == "2026-08-25"

def test_rule_includes_direct_subrules():
    body = _get("/api/v1/rules/702.11").json()
    assert [s["rule_id"] for s in body["subrules"]] == ["702.11a", "702.11b"]

def test_rule_id_is_normalized():
    assert _get("/api/v1/rules/702.11B.").json()["rule_id"] == "702.11b"

def test_unknown_rule_is_404():
    resp = _get("/api/v1/rules/702.99z")
    assert resp.status_code == 404 and resp.json() == {"detail": "Rule not found"}
```

- [ ] **Step 2:** Tests FAIL (404 from routing with a different detail, or 405).
- [ ] **Step 3: Implement.**

```python
def _rule_summary(rule: dict) -> dict:
    return {"rule_id": rule["rule_id"], "text": rule["text"]}


@app.get("/api/v1/rules/{rule_id}")
def get_rule(rule_id: str, rules_index: RulesIndex = Depends(get_rules_index)) -> dict:
    normalized = rule_id.strip().rstrip(".").lower()
    rule = rules_index.get(normalized)
    if rule is None:
        raise HTTPException(status_code=404, detail="Rule not found")
    return {
        **_rule_summary(rule),
        "ancestors": [_rule_summary(r) for r in rules_index.ancestors(normalized)],
        "subrules": [_rule_summary(r) for r in rules_index.children(normalized)],
        "rules_ingested_at": rules_index.ingested_at,
    }
```

- [ ] **Step 4:** Tests pass. Also run the full mtg-api suite. **Step 5:** Commit `feat(mtg-api): add GET /api/v1/rules/{rule_id}`.

### Task 10: Frontend citations on the search page

**Files:**
- Modify: `mtg-web/src/lib/api.ts`, `mtg-web/src/routes/+page.svelte`
- Create: `mtg-web/src/lib/segments.ts`, `mtg-web/src/lib/CitedAnswer.svelte`, `mtg-web/src/lib/SourcesList.svelte`

**Interfaces:**
- Produces:
  - Types `Citation`, `CitationStats`, `RuleSummary`, `RuleDetail`
  - `fetchRule(id): Promise<RuleDetail>`, which throws `NotFoundError` on 404
  - `segmentAnswer(answer, citationNumbers: Set<number>, ruleRefs: Set<string>): Segment[]`
  - `<CitedAnswer answer citations ruleReferences idPrefix>` and `<SourcesList citations results idPrefix expanded>`

- [ ] **Step 1:** Extend `api.ts` with the types from the spec and `fetchRule`. `QueryHistoryRow` gets nullable `citations`, `citation_stats`, and `rule_references`.
- [ ] **Step 2:** Write `segments.ts`. A single global regex matches either a well-formed marker (`\[(\s*\d+(?:\s*,\s*\d+)*\s*)\]`) or a rule-reference candidate using the same lookarounds as the server. A marker keeps only numbers in `citationNumbers`. A rule candidate becomes a link only if it's in `ruleRefs`. Everything else stays text, and adjacent text segments are merged.
- [ ] **Step 3:** Write `CitedAnswer.svelte`. It uses a `<p>` with `white-space: pre-wrap` and loops over the segments:
  - Text renders as-is.
  - A rule becomes `<a href="/rules/{id}">`.
  - A cite renders each number as `<span class="cite"><sup><a …>[n]</a></sup><span role="tooltip" id=… hidden={open !== n}>title + text</span></span>`. The `<a>` handles `mouseenter`/`mouseleave`/`focus`/`blur`/`keydown` (Escape closes) and sets `aria-describedby`.
  - The link target is `url`, with `target="_blank" rel="noopener noreferrer"` for external `http(s)` links. With no url it falls back to `#{idPrefix}-source-{n}`.
- [ ] **Step 4:** Write `SourcesList.svelte`: an "Sources" `<ol>` with `<li value={n} id=…>` holding a title link and the text, then `<details open={expanded}><summary>Also retrieved (N)</summary>` listing results where `!cited`.
- [ ] **Step 5:** Update `+page.svelte`:
  - Hold the whole `QueryResponse` and add a `loading` flag that disables the button.
  - Render `CitedAnswer`, plus `<p class="note">No sources cited</p>` when `uncited_answer`.
  - Render `SourcesList` with `expanded={!resp.answer}`.
- [ ] **Step 6:** Run `cd mtg-web && npm run build`. Expected: the build succeeds with no Svelte a11y warnings.
- [ ] **Step 7:** Commit `feat(mtg-web): render answer citations with popovers and a Sources list`.

### Task 11: `/rules/[id]` page and history citations

**Files:**
- Create: `mtg-web/src/routes/rules/[id]/+page.js` (`export const prerender = false;`) and `mtg-web/src/routes/rules/[id]/+page.svelte`
- Modify: `mtg-web/src/routes/history/+page.svelte`

- [ ] **Step 1:** Rules page: `$: load($page.params.id)` with stale-response guards.
  - States: loading, not found (heading "No rule {id}" plus links to search and the parent guess: `702.99z` → `702.99`, `702.99` → `702`), error, and found.
  - The found state shows a breadcrumb `<nav aria-label="Rule hierarchy">` of ancestors, `<h1>Rule {id}</h1>`, the text, subrule links, and "Comprehensive Rules as ingested {date}".
- [ ] **Step 2:** In the history page's expanded row, render `CitedAnswer` (with `idPrefix="h{row.id}"`) and `SourcesList`, falling back to plain text when `citations` is null. Keep the raw JSON inside `<details><summary>Raw results`.
- [ ] **Step 3:** `npm run build` succeeds.
- [ ] **Step 4:** Commit `feat(mtg-web): add /rules/[id] page and show citations in history`.

### Task 12: Data regeneration, docs, end-to-end verification

**Files:**
- Modify: `mtg-worker/mtg-ingestion/data/parsed/cards_*.jsonl` (regenerated), `README.md`, `.env.example` (only if needed)

- [ ] **Step 1:** Run `cd mtg-worker/mtg-ingestion && MTG_INGEST_DATA_DIR=data python -m mtg_ingestion.cli parse-cards`. Check the env var name in `config.py` first. Confirm the new file has `scryfall_uri`, `git rm` the old cards file, and commit `chore(data): re-parse cards with scryfall_uri`.
- [ ] **Step 2:** Update the README:
  - The API table gets the `/api/v1/rules/{id}` row and the extended `/api/v1/query` response.
  - Data flow covers numbered context → citations → validation.
  - Seeding notes that the first embed after upgrade reports `payload_updated`, not `embedded`.
  - Known limitations get the deferred items.
  
  Commit `docs: describe answer citations and the rules endpoint`.
- [ ] **Step 3:** Run `docker compose up -d --build worker backend frontend`. Trigger `POST /api/v1/embed` and poll until done. Expected: `embedded≈0`, `payload_updated≈120k`.
- [ ] **Step 4:** Run the three acceptance queries with `curl`. Check the citations, `/rules/702.11b`, `invalid_count == 0`, and `uncited_answer`.
- [ ] **Step 5:** `curl -s localhost:3000/rules/702.11b` returns HTML containing the app shell, and `curl localhost:8000/api/v1/rules/702.11b` returns the rule.
- [ ] **Step 6:** Re-run all three test suites and report the results.
