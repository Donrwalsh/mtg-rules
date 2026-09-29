# Redesign API Data Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give query results and citations card details (image, type line, mana cost, P/T) and rule headings, and add public `GET /api/v1/meta` and `GET /api/v1/rules`, without changing retrieval or the LLM context.

**Architecture:** Ingestion keeps a few more Scryfall fields per card (left out of `content_hash`, so vectors are untouched). The API already loads every parsed card row into `CardMatcher`. It gets an `oracle_id` lookup, and a new `enrich.py` fills `card` and `heading` on every outgoing result and citation, on both the fresh path and the cache-hit path. Rule headings come from `RulesIndex`, which also serves the table of contents.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, pytest, ruff; Scryfall bulk data; TypeScript types in `mtg-web`.

**Spec:** [docs/superpowers/specs/2026-09-29-redesign-api-data-design.md](../specs/2026-09-29-redesign-api-data-design.md)

## Global Constraints

- `Card.content_hash` must not change for any card: the new fields are display-only.
- Qdrant payloads, `build_context` output and `context_hash` must not change.
- New endpoints are public (no admin dependency). Nothing new is written to the database.
- Store only Scryfall's `normal` image URL; derive `small` by replacing `/normal/` with `/small/`.
- Title-like rule: text ≤ 60 characters and not ending in `.`, `:` or `)`.
- `CR_SECTIONS` titles, verbatim: 1 Game Concepts, 2 Parts of a Card, 3 Card Types, 4 Zones, 5 Turn Structure, 6 Spells, Abilities, and Effects, 7 Additional Rules, 8 Multiplayer Rules, 9 Casual Variants.
- No new Python or npm dependencies.
- Python: ruff line length 100; `ruff check .` and `ruff format --check .` pass in `mtg-api`; `ruff check src tests mtg-ingestion mtg-embed` and `ruff format --check …` pass in `mtg-worker`.
- Commits: conventional prefixes (`feat:`, `fix:`, `docs:`, `test:`, `chore:`), ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Branch: `feature/redesign-api-data`, cut from `main`.

## File Structure

**Ingestion (`mtg-worker/mtg-ingestion/`)**
- `src/mtg_ingestion/models.py`: `Card` gains `power`, `toughness`, `loyalty`, `image_uri`.
- `src/mtg_ingestion/parse/cards.py`: fills them, falling back to the front face.
- `data/parsed/cards_<date>.jsonl`: regenerated; the old one is removed.
- `tests/test_parse_cards_rulings.py`: new tests.

**API (`mtg-api/src/mtg_api/`)**
- `rules_index.py`: `heading()`, `CR_SECTIONS`, `table_of_contents()`.
- `card_matcher.py`: `by_oracle_id()`.
- `card_details.py` (new): `CardDetails` model and `card_details(row)`.
- `models.py`: `card` and `heading` on `QueryResult` and `Citation`.
- `enrich.py` (new): `enrich_results()` and `enrich_citations()`.
- `main.py`: calls enrichment on both return paths; adds `/api/v1/meta` and `/api/v1/rules`.
- Tests: `tests/test_rules_index.py`, `tests/test_card_matcher.py`, `tests/test_card_details.py` (new), `tests/test_enrich.py` (new), `tests/test_query.py`, `tests/test_gating_flow.py`, `tests/test_meta_endpoint.py` (new), `tests/test_rules_endpoint.py`.

**Frontend (`mtg-web/src/lib/api.ts`)**: types and fetchers only.

**Docs**: `README.md` API section.

---

### Task 1: Ingestion keeps P/T, loyalty and the card image

**Files:**
- Modify: `mtg-worker/mtg-ingestion/src/mtg_ingestion/models.py` (class `Card`)
- Modify: `mtg-worker/mtg-ingestion/src/mtg_ingestion/parse/cards.py`
- Test: `mtg-worker/mtg-ingestion/tests/test_parse_cards_rulings.py`

**Interfaces:**
- Produces: `Card.power: str | None`, `Card.toughness: str | None`, `Card.loyalty: str | None`, `Card.image_uri: str | None` (Scryfall `image_uris.normal`, query string kept). Parsed JSONL rows carry keys `power`, `toughness`, `loyalty`, `image_uri`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_parse_cards_rulings.py`)

```python
NORMAL = "https://cards.scryfall.io/normal/front/a/b/{}.jpg?1"


def test_parse_cards_keeps_display_fields(tmp_path: Path) -> None:
    raw = [
        {
            "oracle_id": "bears",
            "name": "Grizzly Bears",
            "oracle_text": "",
            "type_line": "Creature — Bear",
            "mana_cost": "{1}{G}",
            "power": "2",
            "toughness": "2",
            "image_uris": {"small": "ignored", "normal": NORMAL.format("bears")},
        },
        {
            # Double-faced: P/T and images live on the faces; use the front.
            "oracle_id": "delver",
            "name": "Delver of Secrets // Insectile Aberration",
            "type_line": "Creature — Human Wizard // Creature — Human Insect",
            "mana_cost": "{U}",
            "card_faces": [
                {
                    "oracle_text": "Look at the top card.",
                    "power": "1",
                    "toughness": "1",
                    "image_uris": {"normal": NORMAL.format("delver")},
                },
                {"oracle_text": "Flying", "power": "3", "toughness": "2"},
            ],
        },
        {
            "oracle_id": "jace",
            "name": "Jace Beleren",
            "oracle_text": "+2: Each player draws a card.",
            "type_line": "Legendary Planeswalker — Jace",
            "mana_cost": "{1}{U}{U}",
            "loyalty": "3",
        },
    ]
    raw_path = tmp_path / "oracle_cards.json"
    raw_path.write_text(json.dumps(raw))

    cards = {c.oracle_id: c for c in parse_cards_file(raw_path)}

    bears = cards["bears"]
    assert (bears.power, bears.toughness, bears.loyalty) == ("2", "2", None)
    assert bears.image_uri == NORMAL.format("bears")
    delver = cards["delver"]
    assert (delver.power, delver.toughness) == ("1", "1")
    assert delver.image_uri == NORMAL.format("delver")
    jace = cards["jace"]
    assert (jace.power, jace.loyalty, jace.image_uri) == (None, "3", None)


def test_display_fields_do_not_change_content_hash() -> None:
    plain = Card(oracle_id="a", name="N", oracle_text="t", type_line="T", mana_cost="{1}")
    dressed = Card(
        oracle_id="a",
        name="N",
        oracle_text="t",
        type_line="T",
        mana_cost="{1}",
        power="2",
        toughness="2",
        loyalty="4",
        image_uri=NORMAL.format("a"),
    )
    assert plain.content_hash == dressed.content_hash
```

- [ ] **Step 2: Run to verify failure**

Run (in `mtg-worker/mtg-ingestion/`): `pytest tests/test_parse_cards_rulings.py -v`
Expected: FAIL (`AttributeError: 'Card' object has no attribute 'power'` / validation error on unknown fields).

- [ ] **Step 3: Implement**

In `models.py`, in `Card`, after `scryfall_uri`:

```python
    # Display-only, like scryfall_uri: left out of content_hash so they
    # never trigger a re-embed. Taken from the front face on multi-face cards.
    power: str | None = None
    toughness: str | None = None
    loyalty: str | None = None
    # Scryfall's "normal" image URL (query string kept: it's their cache buster).
    image_uri: str | None = None
```

In `parse/cards.py`, add below `_strip_query`:

```python
def _front(raw: dict, key: str):
    """A card-level field, falling back to the front face: multi-face cards
    keep P/T, loyalty and images on card_faces instead."""
    if raw.get(key) is not None:
        return raw[key]
    faces = raw.get("card_faces") or []
    return faces[0].get(key) if faces else None
```

and extend the `Card(...)` call:

```python
        cards.append(
            Card(
                oracle_id=oracle_id,
                name=raw["name"],
                oracle_text=oracle_text,
                type_line=raw.get("type_line", ""),
                mana_cost=raw.get("mana_cost") or None,
                scryfall_uri=_strip_query(raw.get("scryfall_uri")),
                power=_front(raw, "power"),
                toughness=_front(raw, "toughness"),
                loyalty=_front(raw, "loyalty"),
                image_uri=(_front(raw, "image_uris") or {}).get("normal"),
            )
        )
```

- [ ] **Step 4: Run tests and lint**

Run (in `mtg-worker/mtg-ingestion/`): `pytest -v`
Run (in `mtg-worker/`): `ruff check src tests mtg-ingestion mtg-embed && ruff format --check src tests mtg-ingestion mtg-embed`
Expected: all PASS.

- [ ] **Step 5: Regenerate the parsed cards file**

Run (repo root): `docker compose run --rm ingestion parse-cards`
Expected: `Parsed N cards -> /app/data/parsed/cards_<today>.jsonl` (N ≈ 33k, same count as the existing file's `wc -l`).

Then remove the old file so only one cards file is committed:

```bash
git rm mtg-worker/mtg-ingestion/data/parsed/cards_2026-09-28.jsonl
head -c 400 mtg-worker/mtg-ingestion/data/parsed/cards_*.jsonl   # shows "image_uri" and "power"
```

Check that `content_hash` is unchanged for every card (the embed pipeline must see nothing to re-embed):

```bash
git show HEAD:mtg-worker/mtg-ingestion/data/parsed/cards_2026-09-28.jsonl \
  | python -c "import sys,json; print(len({json.loads(l)['content_hash'] for l in sys.stdin}))"
python -c "import sys,json,glob; f=glob.glob('mtg-worker/mtg-ingestion/data/parsed/cards_*.jsonl')[0]; print(len({json.loads(l)['content_hash'] for l in open(f,encoding='utf-8')}))"
```

Expected: the same count, and a set comparison (below) prints `True`.

```bash
python - <<'EOF'
import glob, json, subprocess
old = subprocess.run(["git","show","HEAD:mtg-worker/mtg-ingestion/data/parsed/cards_2026-09-28.jsonl"],capture_output=True,text=True,encoding="utf-8").stdout
new = open(glob.glob("mtg-worker/mtg-ingestion/data/parsed/cards_*.jsonl")[0], encoding="utf-8").read()
h = lambda s: {json.loads(l)["content_hash"] for l in s.splitlines() if l.strip()}
print(h(old) == h(new))
EOF
```

If Scryfall data differs because the raw file changed, stop and ask. The raw file is committed and must be the one parsed.

- [ ] **Step 6: Commit**

```bash
git add mtg-worker/mtg-ingestion/src mtg-worker/mtg-ingestion/tests mtg-worker/mtg-ingestion/data/parsed
git commit -m "feat: keep card P/T, loyalty and image URL at ingest"
```

---

### Task 2: Rule headings and the table of contents

**Files:**
- Modify: `mtg-api/src/mtg_api/rules_index.py`
- Test: `mtg-api/tests/test_rules_index.py`

**Interfaces:**
- Produces: `RulesIndex.heading(rule_id: str) -> str | None`; `CR_SECTIONS: dict[int, str]`; `RulesIndex.table_of_contents() -> list[dict]` shaped `[{"number": int, "title": str, "rules": [{"rule_id": str, "text": str}]}]`, sections in number order, only sections with rules.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_rules_index.py`)

```python
from mtg_api.rules_index import CR_SECTIONS, RulesIndex

HEADING_RULES = [
    {"rule_id": "100", "text": "General", "parent_id": None},
    {"rule_id": "100.1", "text": "These Magic rules apply to any Magic game.", "parent_id": "100"},
    {"rule_id": "510", "text": "Combat Damage Step", "parent_id": None},
    {"rule_id": "510.1", "text": "First, the active player announces:", "parent_id": "510"},
    {"rule_id": "510.1c", "text": "A blocked creature assigns damage.", "parent_id": "510.1"},
    {"rule_id": "702", "text": "Keyword Abilities", "parent_id": None},
    {"rule_id": "702.2", "text": "Deathtouch", "parent_id": "702"},
    {"rule_id": "702.2c", "text": "Any nonzero damage is lethal.", "parent_id": "702.2"},
]


def test_heading_is_nearest_title_like_rule():
    index = RulesIndex(HEADING_RULES)
    assert index.heading("702.2c") == "Deathtouch"
    assert index.heading("702.2") == "Deathtouch"
    assert index.heading("510.1c") == "Combat Damage Step"
    assert index.heading("100.1") == "General"
    assert index.heading("100") == "General"


def test_heading_of_unknown_rule_is_none():
    assert RulesIndex(HEADING_RULES).heading("999.9z") is None


def test_long_or_punctuated_text_is_not_a_heading():
    rules = [{"rule_id": "800.1", "text": "x" * 61, "parent_id": None}]
    assert RulesIndex(rules).heading("800.1") is None


def test_table_of_contents_groups_top_level_rules_by_section():
    toc = RulesIndex(HEADING_RULES).table_of_contents()
    assert toc == [
        {"number": 1, "title": "Game Concepts", "rules": [{"rule_id": "100", "text": "General"}]},
        {
            "number": 5,
            "title": "Turn Structure",
            "rules": [{"rule_id": "510", "text": "Combat Damage Step"}],
        },
        {
            "number": 7,
            "title": "Additional Rules",
            "rules": [{"rule_id": "702", "text": "Keyword Abilities"}],
        },
    ]


def test_cr_sections_are_the_nine_sections():
    assert CR_SECTIONS[6] == "Spells, Abilities, and Effects"
    assert sorted(CR_SECTIONS) == list(range(1, 10))
```

- [ ] **Step 2: Run to verify failure**

Run (in `mtg-api/`): `pytest tests/test_rules_index.py -v`
Expected: FAIL (`ImportError: cannot import name 'CR_SECTIONS'`).

- [ ] **Step 3: Implement** (in `rules_index.py`)

Module level, below `_DATE_RE`:

```python
# The Comprehensive Rules' nine sections. Only the raw text names them, and
# they haven't changed in decades, so they live here rather than in ingestion.
CR_SECTIONS: dict[int, str] = {
    1: "Game Concepts",
    2: "Parts of a Card",
    3: "Card Types",
    4: "Zones",
    5: "Turn Structure",
    6: "Spells, Abilities, and Effects",
    7: "Additional Rules",
    8: "Multiplayer Rules",
    9: "Casual Variants",
}
_TOP_LEVEL_RE = re.compile(r"\d{3}")
_HEADING_MAX_CHARS = 60


def _is_title(text: str) -> bool:
    # "Deathtouch", "Combat Damage Step" -- not rule prose.
    return len(text) <= _HEADING_MAX_CHARS and not text.rstrip().endswith((".", ":", ")"))
```

Methods on `RulesIndex`:

```python
    def heading(self, rule_id: str) -> str | None:
        """The nearest title-like text at or above this rule: 702.2c ->
        "Deathtouch", 510.1c -> "Combat Damage Step"."""
        rule = self._by_id.get(rule_id)
        if rule is None:
            return None
        for candidate in [rule, *reversed(self.ancestors(rule_id))]:
            if _is_title(candidate["text"]):
                return candidate["text"]
        return None

    def table_of_contents(self) -> list[dict]:
        sections: dict[int, list[dict]] = {}
        for rule in self.rules:
            if _TOP_LEVEL_RE.fullmatch(rule["rule_id"]):
                sections.setdefault(int(rule["rule_id"][0]), []).append(
                    {"rule_id": rule["rule_id"], "text": rule["text"]}
                )
        return [
            {"number": n, "title": CR_SECTIONS.get(n, f"Section {n}"), "rules": sections[n]}
            for n in sorted(sections)
        ]
```

- [ ] **Step 4: Run tests and lint**

Run (in `mtg-api/`): `pytest tests/test_rules_index.py -v && ruff check . && ruff format --check .`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mtg-api/src/mtg_api/rules_index.py mtg-api/tests/test_rules_index.py
git commit -m "feat: rule headings and a table of contents from the rules index"
```

---

### Task 3: Card details and lookup by oracle_id

**Files:**
- Create: `mtg-api/src/mtg_api/card_details.py`
- Modify: `mtg-api/src/mtg_api/card_matcher.py` (`CardMatcher.__init__`, new method)
- Test: `mtg-api/tests/test_card_details.py` (new), `mtg-api/tests/test_card_matcher.py`

**Interfaces:**
- Produces: `class CardDetails(BaseModel)` with `name: str`, `type_line: str = ""`, `mana_cost: str | None = None`, `power: str | None = None`, `toughness: str | None = None`, `loyalty: str | None = None`, `image_small: str | None = None`, `image_normal: str | None = None`. `card_details(row: dict) -> CardDetails`. `CardMatcher.by_oracle_id(oracle_id: str | None) -> dict | None`.

- [ ] **Step 1: Write the failing tests**

`tests/test_card_details.py`:

```python
from mtg_api.card_details import card_details

NORMAL = "https://cards.scryfall.io/normal/front/a/4/abc.jpg?1783907750"


def test_card_details_from_a_parsed_row():
    details = card_details(
        {
            "oracle_id": "oid",
            "name": "Colossal Dreadmaw",
            "type_line": "Creature — Dinosaur",
            "mana_cost": "{4}{G}{G}",
            "power": "6",
            "toughness": "6",
            "image_uri": NORMAL,
        }
    )
    assert details.name == "Colossal Dreadmaw"
    assert details.mana_cost == "{4}{G}{G}"
    assert (details.power, details.toughness, details.loyalty) == ("6", "6", None)
    assert details.image_normal == NORMAL
    assert details.image_small == "https://cards.scryfall.io/small/front/a/4/abc.jpg?1783907750"


def test_card_details_tolerates_rows_from_before_the_new_fields():
    details = card_details({"oracle_id": "oid", "name": "Counterspell"})
    assert details.type_line == ""
    assert details.image_small is None and details.image_normal is None
```

Append to `tests/test_card_matcher.py`:

```python
def test_by_oracle_id_returns_the_loaded_row():
    bolt = {"oracle_id": "oid-bolt", "name": "Lightning Bolt"}
    matcher = CardMatcher([bolt, {"oracle_id": "oid-x", "name": "Counterspell"}])
    assert matcher.by_oracle_id("oid-bolt") is bolt
    assert matcher.by_oracle_id("missing") is None
    assert matcher.by_oracle_id(None) is None
```

(If `CardMatcher` isn't already imported in that file, add `from mtg_api.card_matcher import CardMatcher`.)

- [ ] **Step 2: Run to verify failure**

Run (in `mtg-api/`): `pytest tests/test_card_details.py tests/test_card_matcher.py -v`
Expected: FAIL (`ModuleNotFoundError: mtg_api.card_details`, `AttributeError: by_oracle_id`).

- [ ] **Step 3: Implement**

`src/mtg_api/card_details.py`:

```python
from __future__ import annotations

from pydantic import BaseModel


class CardDetails(BaseModel):
    """What the UI shows for a card: its face, not its rules text (that's
    the result's `text`)."""

    name: str
    type_line: str = ""
    mana_cost: str | None = None
    power: str | None = None
    toughness: str | None = None
    loyalty: str | None = None
    image_small: str | None = None
    image_normal: str | None = None


def card_details(row: dict) -> CardDetails:
    """From a parsed cards_*.jsonl row. Only the "normal" image URL is
    stored; Scryfall serves the other sizes at the same path."""
    normal = row.get("image_uri")
    return CardDetails(
        name=row["name"],
        type_line=row.get("type_line") or "",
        mana_cost=row.get("mana_cost"),
        power=row.get("power"),
        toughness=row.get("toughness"),
        loyalty=row.get("loyalty"),
        image_small=normal.replace("/normal/", "/small/", 1) if normal else None,
        image_normal=normal,
    )
```

In `card_matcher.py`, in `CardMatcher.__init__`, build the lookup alongside `_cards_by_key` (same loop; references, not copies):

```python
        self._cards_by_oracle_id: dict[str, dict] = {}
        for card in cards:
            key = card["name"].lower()
            self._cards_by_key[key] = card
            if card.get("oracle_id"):
                self._cards_by_oracle_id[card["oracle_id"]] = card
            self._automaton.add_word(key, key)
```

and add the method:

```python
    def by_oracle_id(self, oracle_id: str | None) -> dict | None:
        return self._cards_by_oracle_id.get(oracle_id) if oracle_id else None
```

- [ ] **Step 4: Run tests and lint**

Run (in `mtg-api/`): `pytest tests/test_card_details.py tests/test_card_matcher.py -v && ruff check . && ruff format --check .`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mtg-api/src/mtg_api/card_details.py mtg-api/src/mtg_api/card_matcher.py mtg-api/tests/test_card_details.py mtg-api/tests/test_card_matcher.py
git commit -m "feat: card details from the loaded card rows"
```

---

### Task 4: Enrich results and citations on every query response

**Files:**
- Modify: `mtg-api/src/mtg_api/models.py` (`QueryResult`, `Citation`)
- Create: `mtg-api/src/mtg_api/enrich.py`
- Modify: `mtg-api/src/mtg_api/main.py` (`query()`: cache-hit block and after `cite_answer`)
- Test: `mtg-api/tests/test_enrich.py` (new), `mtg-api/tests/test_query.py`, `mtg-api/tests/test_gating_flow.py`

**Interfaces:**
- Consumes: `CardDetails`, `card_details(row)` (Task 3); `CardMatcher.by_oracle_id` (Task 3); `RulesIndex.heading` (Task 2).
- Produces: `QueryResult.card: CardDetails | None = None`, `QueryResult.heading: str | None = None`, `Citation.card: CardDetails | None = None`, `Citation.heading: str | None = None`. `enrich_results(results: list[QueryResult], cards: CardMatcher, rules_index: RulesIndex) -> None` and `enrich_citations(citations: list[Citation], cards: CardMatcher, rules_index: RulesIndex) -> None` (both mutate in place).

- [ ] **Step 1: Write the failing unit tests** (`tests/test_enrich.py`)

```python
from mtg_api.card_matcher import CardMatcher
from mtg_api.enrich import enrich_citations, enrich_results
from mtg_api.models import Citation, QueryResult
from mtg_api.rules_index import RulesIndex

CARDS = CardMatcher(
    [
        {
            "oracle_id": "oid-collar",
            "name": "Basilisk Collar",
            "type_line": "Artifact — Equipment",
            "mana_cost": "{1}",
            "image_uri": "https://cards.scryfall.io/normal/front/c/c/collar.jpg?1",
        }
    ]
)
RULES = RulesIndex(
    [
        {"rule_id": "702", "text": "Keyword Abilities", "parent_id": None},
        {"rule_id": "702.2", "text": "Deathtouch", "parent_id": "702"},
        {"rule_id": "702.2c", "text": "Any nonzero damage is lethal.", "parent_id": "702.2"},
    ]
)


def _result(source, **kw):
    return QueryResult(source=source, title=kw.pop("title", "t"), text="x", score=1.0,
                       match_type="m", **kw)


def test_card_oracle_and_ruling_results_get_their_card():
    results = [
        _result("card", oracle_id="oid-collar"),
        _result("oracle", oracle_id="oid-collar"),
        _result("ruling", oracle_id="oid-collar"),
        _result("oracle", oracle_id="unknown"),
    ]
    enrich_results(results, CARDS, RULES)
    assert [r.card.name if r.card else None for r in results] == [
        "Basilisk Collar",
        "Basilisk Collar",
        "Basilisk Collar",
        None,
    ]
    assert results[0].card.image_small == "https://cards.scryfall.io/small/front/c/c/collar.jpg?1"


def test_rule_results_get_a_heading_and_no_card():
    (rule,) = results = [_result("rule", rule_id="702.2c", title="702.2c")]
    enrich_results(results, CARDS, RULES)
    assert rule.heading == "Deathtouch"
    assert rule.card is None


def test_citations_are_enriched_the_same_way():
    citations = [
        Citation(number=1, source_type="rule", title="Rule 702.2c", rule_id="702.2c", text="x"),
        Citation(number=2, source_type="card", title="Card — Basilisk Collar",
                 oracle_id="oid-collar", text="x"),
        Citation(number=3, source_type="ruling", title="Ruling — Basilisk Collar",
                 oracle_id="oid-collar", text="x"),
    ]
    enrich_citations(citations, CARDS, RULES)
    assert citations[0].heading == "Deathtouch"
    assert citations[1].card.mana_cost == "{1}"
    assert citations[2].card.name == "Basilisk Collar"
```

- [ ] **Step 2: Run to verify failure**

Run (in `mtg-api/`): `pytest tests/test_enrich.py -v`
Expected: FAIL (`ModuleNotFoundError: mtg_api.enrich`).

- [ ] **Step 3: Implement the models and `enrich.py`**

In `models.py`, add `from mtg_api.card_details import CardDetails` and, in both `QueryResult` (after `cited`) and `Citation` (after `published_at`):

```python
    # Display data filled in just before responding (see mtg_api.enrich);
    # never part of the LLM context.
    card: CardDetails | None = None
    heading: str | None = None
```

`src/mtg_api/enrich.py`:

```python
from __future__ import annotations

from mtg_api.card_details import card_details
from mtg_api.card_matcher import CardMatcher
from mtg_api.models import Citation, QueryResult
from mtg_api.rules_index import RulesIndex

_CARD_SOURCES = {"card", "oracle", "ruling"}


def _fill(item: QueryResult | Citation, kind: str, cards: CardMatcher, rules: RulesIndex) -> None:
    if kind == "rule":
        rule_id = item.rule_id or item.title
        item.heading = rules.heading(rule_id)
    elif kind in _CARD_SOURCES:
        row = cards.by_oracle_id(item.oracle_id)
        item.card = card_details(row) if row else None


def enrich_results(results: list[QueryResult], cards: CardMatcher, rules: RulesIndex) -> None:
    """Attach card faces and rule headings for the UI. Runs after
    build_context, so it can never change what the LLM saw."""
    for result in results:
        _fill(result, result.source, cards, rules)


def enrich_citations(citations: list[Citation], cards: CardMatcher, rules: RulesIndex) -> None:
    for citation in citations:
        _fill(citation, citation.source_type, cards, rules)
```

(`Citation.rule_id` exists and `Citation.title` is "Rule 702.2c" for rules; `_citation` always sets `rule_id` for rules, so `item.rule_id or item.title` only falls back for `QueryResult`.)

- [ ] **Step 4: Run unit tests**

Run (in `mtg-api/`): `pytest tests/test_enrich.py -v`
Expected: PASS.

- [ ] **Step 5: Write the failing endpoint tests**

Append to `tests/test_query.py`:

```python
def test_query_results_and_citations_carry_card_details_and_headings():
    cards = [
        {
            "oracle_id": "oid-1",
            "name": "Lightning Bolt",
            "oracle_text": "Deals 3 damage.",
            "type_line": "Instant",
            "mana_cost": "{R}",
            "image_uri": "https://cards.scryfall.io/normal/front/b/o/bolt.jpg?1",
        }
    ]
    rules = [{"rule_id": "702", "text": "Keyword Abilities", "parent_id": None}, *HEXPROOF_RULES]
    _override(cards=cards, rules=rules, answerer=_FakeAnswerer("Bolt [1] vs hexproof [2]."))
    try:
        body = TestClient(app).post(
            "/api/v1/query", json={"query": "can Lightning Bolt target my hexproof creature"}
        ).json()
    finally:
        app.dependency_overrides.clear()

    card, keyword = body["results"][0], body["results"][1]
    assert card["card"]["type_line"] == "Instant"
    assert card["card"]["image_small"] == "https://cards.scryfall.io/small/front/b/o/bolt.jpg?1"
    assert keyword["heading"] == "Hexproof"
    by_number = {c["number"]: c for c in body["citations"]}
    assert by_number[1]["card"]["name"] == "Lightning Bolt"
    assert by_number[2]["heading"] == "Hexproof"


def test_enrichment_does_not_change_the_llm_context(monkeypatch):
    seen = []

    class _Recording(_FakeAnswerer):
        def generate(self, query, context):
            seen.append(context)
            return super().generate(query, context)

    cards = [{"oracle_id": "oid-1", "name": "Lightning Bolt", "oracle_text": "Deals 3 damage.",
              "image_uri": "https://cards.scryfall.io/normal/front/b/o/bolt.jpg?1"}]
    _override(cards=cards, rules=HEXPROOF_RULES, answerer=_Recording())
    try:
        TestClient(app).post("/api/v1/query", json={"query": "Lightning Bolt vs hexproof"})
    finally:
        app.dependency_overrides.clear()
    assert "scryfall.io" not in seen[0]
    assert "Hexproof" in seen[0]  # rule text itself is still there, unchanged
```

Before writing the keyword/citation assertion, confirm the result order with `test_query_orders_keyword_rules_after_card_rulings_and_before_vector_hits` (card matches, then card rulings, then keyword rules). With no scroll points, index 1 is the first keyword rule (`702.11`, heading "Hexproof"), and it is source `[2]` in the context.

Append to `tests/test_gating_flow.py` (cache rows written before this change have no `card`/`heading`):

```python
def test_cache_hit_rows_from_before_enrichment_are_enriched(gated):
    engine, answerer = _setup()
    app.dependency_overrides[main.get_rules_index] = lambda: main.RulesIndex(
        [
            {"rule_id": "702", "text": "Keyword Abilities", "parent_id": None},
            {"rule_id": "702.19", "text": "Trample", "parent_id": "702"},
            {"rule_id": "702.19b", "text": "T.", "parent_id": "702.19"},
        ]
    )
    first = _post({"query": "How does trample work?"}).json()
    # Simulate an old cache row: strip the enrichment from what was stored.
    with engine.begin() as conn:
        row = conn.execute(select(answer_cache)).one()
        stored = dict(row.response)
        for r in stored["results"]:
            r.pop("heading", None)
            r.pop("card", None)
        for c in stored["citations"]:
            c.pop("heading", None)
        conn.execute(answer_cache.update().values(response=stored))

    second = _post({"query": "how does trample work"}).json()
    assert second["cached_at"] is not None
    assert second["results"][0]["heading"] == "Trample"
    assert second["citations"][0]["heading"] == first["citations"][0]["heading"] == "Trample"
```

(`main.RulesIndex` is importable because `main.py` imports it. If `answer_cache.c.response` is a JSON column stored as text in SQLite, `row.response` is already a dict; check `answer_cache.py` and adjust the read/write if it is a string.)

- [ ] **Step 6: Run to verify failure**

Run (in `mtg-api/`): `pytest tests/test_query.py tests/test_gating_flow.py -k "card_details or enrich" -v`
Expected: FAIL (`KeyError: 'card'` / `heading` is `None`).

- [ ] **Step 7: Wire enrichment into `query()`**

In `main.py`, import: `from mtg_api.enrich import enrich_citations, enrich_results`.

In the cache-hit `else:` branch, before `_record(engine, outcome="cached", **record)`:

```python
            enrich_results(cached_response.results, matcher, rules_index)
            enrich_citations(cached_response.citations, matcher, rules_index)
```

After `context, sources = build_context(all_results)`:

```python
    # After build_context: display data must never reach the LLM.
    enrich_results(all_results, matcher, rules_index)
```

After `citation_stats = cited.stats if cited else CitationStats()`:

```python
    enrich_citations(citations, matcher, rules_index)
```

- [ ] **Step 8: Run the full API suite and lint**

Run (in `mtg-api/`): `pytest -v && ruff check . && ruff format --check .`
Expected: all PASS. (Existing equality assertions on whole result dicts, if any, may now see `card`/`heading` keys. Update those expectations to include `"card": None, "heading": None` rather than weakening them.)

- [ ] **Step 9: Commit**

```bash
git add mtg-api/src/mtg_api/models.py mtg-api/src/mtg_api/enrich.py mtg-api/src/mtg_api/main.py mtg-api/tests
git commit -m "feat: card details and rule headings on query results and citations"
```

---

### Task 5: `GET /api/v1/meta` and `GET /api/v1/rules`

**Files:**
- Modify: `mtg-api/src/mtg_api/main.py` (two routes; `/api/v1/rules` must be declared before or alongside `/api/v1/rules/{rule_id}`. FastAPI matches exact paths, so order doesn't matter, but keep them together)
- Test: `mtg-api/tests/test_meta_endpoint.py` (new), `mtg-api/tests/test_rules_endpoint.py`

**Interfaces:**
- Consumes: `RulesIndex.table_of_contents()`, `RulesIndex.ingested_at` (Task 2).
- Produces: `GET /api/v1/meta` → `{"answers_per_day": int | null, "max_query_chars": int, "rules_as_of": str | null}`; `GET /api/v1/rules` → `{"sections": [...], "rules_as_of": str | null}`; `GET /api/v1/rules/{rule_id}` gains `"heading": str | null` (used by the search page's rule-link preview).

- [ ] **Step 1: Write the failing tests**

`tests/test_meta_endpoint.py`:

```python
from fastapi.testclient import TestClient

from mtg_api import main
from mtg_api.main import app, get_rules_index
from mtg_api.rules_index import RulesIndex


def _get(monkeypatch, **settings):
    for name, value in settings.items():
        monkeypatch.setattr(main.settings, name, value)
    app.dependency_overrides[get_rules_index] = lambda: RulesIndex([], "2026-09-28")
    try:
        return TestClient(app).get("/api/v1/meta")
    finally:
        app.dependency_overrides.clear()


def test_meta_reports_the_daily_limit_when_gated(monkeypatch):
    resp = _get(monkeypatch, gating_enabled=True, ip_daily_llm_limit=20, max_query_chars=500)
    assert resp.status_code == 200
    assert resp.json() == {"answers_per_day": 20, "max_query_chars": 500, "rules_as_of": "2026-09-28"}


def test_meta_reports_unlimited_when_not_gated(monkeypatch):
    assert _get(monkeypatch, gating_enabled=False).json()["answers_per_day"] is None
```

Append to `tests/test_rules_endpoint.py`:

```python
def test_rules_table_of_contents():
    resp = _get("/api/v1/rules")
    assert resp.status_code == 200
    assert resp.json() == {
        "sections": [
            {
                "number": 7,
                "title": "Additional Rules",
                "rules": [{"rule_id": "702", "text": "Keyword Abilities"}],
            }
        ],
        "rules_as_of": "2026-08-25",
    }


def test_rule_detail_carries_its_heading():
    assert _get("/api/v1/rules/702.11b").json()["heading"] == "Hexproof"
```

In the existing `test_rule_found_with_ancestors_and_ingest_date`, add `"heading": "Hexproof",` to the expected dict (it compares the whole body).

- [ ] **Step 2: Run to verify failure**

Run (in `mtg-api/`): `pytest tests/test_meta_endpoint.py tests/test_rules_endpoint.py -v`
Expected: FAIL (404 on both new paths; `KeyError: 'heading'`).

- [ ] **Step 3: Implement** (in `main.py`, next to `get_rule`)

```python
@app.get("/api/v1/meta")
def get_meta(rules_index: RulesIndex = Depends(get_rules_index)) -> dict:
    """Public facts the UI states: the daily answer limit (null when not
    gated) and how current the rules are."""
    return {
        "answers_per_day": settings.ip_daily_llm_limit if settings.gating_enabled else None,
        "max_query_chars": settings.max_query_chars,
        "rules_as_of": rules_index.ingested_at,
    }


@app.get("/api/v1/rules")
def list_rules(rules_index: RulesIndex = Depends(get_rules_index)) -> dict:
    return {
        "sections": rules_index.table_of_contents(),
        "rules_as_of": rules_index.ingested_at,
    }
```

In `get_rule`, add to the returned dict, after `**_rule_summary(rule),`:

```python
        "heading": rules_index.heading(normalized),
```

- [ ] **Step 4: Run tests and lint**

Run (in `mtg-api/`): `pytest -v && ruff check . && ruff format --check .`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mtg-api/src/mtg_api/main.py mtg-api/tests/test_meta_endpoint.py mtg-api/tests/test_rules_endpoint.py
git commit -m "feat: public meta and rules table-of-contents endpoints"
```

---

### Task 6: Frontend types, README, and a live check

**Files:**
- Modify: `mtg-web/src/lib/api.ts`
- Modify: `README.md` (API section, near the existing `QueryResponse` / citations description around line 170)

**Interfaces:**
- Produces (TypeScript, used by PRs 3 and 4):

```ts
export interface CardDetails {
  name: string;
  type_line: string;
  mana_cost: string | null;
  power: string | null;
  toughness: string | null;
  loyalty: string | null;
  image_small: string | null;
  image_normal: string | null;
}
// QueryResult and Citation each gain:
//   card?: CardDetails | null;
//   heading?: string | null;
export interface Meta {
  answers_per_day: number | null;
  max_query_chars: number;
  rules_as_of: string | null;
}
export interface RulesSection {
  number: number;
  title: string;
  rules: RuleSummary[];
}
export interface RulesContents {
  sections: RulesSection[];
  rules_as_of: string | null;
}
export async function fetchMeta(): Promise<Meta>;
export async function fetchRulesIndex(): Promise<RulesContents>;
```

- [ ] **Step 1: Add the types and fetchers to `api.ts`**

Add `CardDetails`, `Meta`, `RulesSection`, `RulesContents` exactly as above. Add `card?: CardDetails | null;` and `heading?: string | null;` to both `QueryResult` and `Citation`, and `heading: string | null;` to `RuleDetail`. Add:

```ts
export async function fetchMeta(): Promise<Meta> {
  const resp = await fetch(`${API_URL}/api/v1/meta`);
  if (!resp.ok) throw new Error(`meta fetch failed: ${resp.status}`);
  return resp.json();
}

export async function fetchRulesIndex(): Promise<RulesContents> {
  const resp = await fetch(`${API_URL}/api/v1/rules`);
  if (!resp.ok) throw new Error(`rules index fetch failed: ${resp.status}`);
  return resp.json();
}
```

(`RuleSummary` already exists in `api.ts`; declare `RulesSection` after it.)

- [ ] **Step 2: Build the frontend**

Run (in `mtg-web/`): `npm run build` (and `npm run check` if PR 1 has merged)
Expected: PASS.

- [ ] **Step 3: Document in README**

In the API section, add to the `QueryResponse` description:

```markdown
Each result and citation also carries display-only fields, filled in after
the LLM context is built: `card` (`name`, `type_line`, `mana_cost`, `power`,
`toughness`, `loyalty`, `image_small`, `image_normal`) on card, oracle and
ruling sources, and `heading` (e.g. "Deathtouch" for 702.2c) on rules.
```

and list the new endpoints next to `GET /api/v1/rules/{rule_id}`:

```markdown
- `GET /api/v1/rules`: the Comprehensive Rules' table of contents (the nine sections and their three-digit rules).
- `GET /api/v1/meta`: public facts for the UI: `answers_per_day` (null when gating is off), `max_query_chars`, `rules_as_of`.
```

- [ ] **Step 4: Live check against the dev stack**

Run (repo root): `docker compose up -d --build backend`, then:

```bash
curl -s localhost:8000/api/v1/meta
curl -s localhost:8000/api/v1/rules | python -c "import sys,json; d=json.load(sys.stdin); print([s['title'] for s in d['sections']])"
curl -s -X POST localhost:8000/api/v1/query -H 'Content-Type: application/json' \
  -d '{"query":"Does Basilisk Collar give trample damage deathtouch?","generate":false}' \
  | python -c "import sys,json; d=json.load(sys.stdin); print([(r['source'], (r.get('card') or {}).get('image_small'), r.get('heading')) for r in d['results']][:6])"
```

Expected: meta JSON; nine section titles; card rows with `https://cards.scryfall.io/small/...` URLs and rule rows with headings.

- [ ] **Step 5: Commit**

```bash
git add mtg-web/src/lib/api.ts README.md
git commit -m "docs: document card details, headings, meta and rules endpoints"
```

---

## Rollout (after merge)

1. Coolify redeploys the backend from `main`.
2. `make sync-prod HOST=<host>` copies the new `cards_*.jsonl` to production and bumps `data_version`, which invalidates cached answers.
3. Spot-check: `curl https://<domain>/api/v1/meta` and one query with a card name.
