# mtg-embed: Embedding Pipeline + Qdrant Vector Store Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a new `mtg-embed` package that reads the three parsed JSONL sources (Comprehensive Rules, Scryfall rulings, Scryfall oracle cards) already produced by `mtg-ingestion`, embeds them with a local `sentence-transformers` model, and upserts them as points in a single Qdrant collection, with content_hash-based idempotency so repeated runs only pay embedding cost for new or changed rows.

**Architecture:** A source-loader per input type turns raw JSONL rows into a common `EmbeddableChunk` (deterministic UUID5 point ID, prefixed chunk text, payload). A generic `embed_and_store` pipeline function batches those chunks, asks Qdrant which point IDs already have a matching `content_hash` (skipping those), embeds only the rest in batches, and upserts. Two small seam classes (`Embedder`, `QdrantStore`) keep the heavy model and the Qdrant client injectable, so all pipeline logic is unit-tested against a fake model and a real in-memory Qdrant client (`qdrant_client.QdrantClient(location=":memory:")`) — fast, no network, no GPU. A CLI wires it together for real runs.

**Tech Stack:** Python 3.12+, `sentence-transformers` (`BAAI/bge-base-en-v1.5`), `qdrant-client`, `pydantic-settings`, `typer`, `pytest`.

**Spec:** `docs/superpowers/specs/2026-08-25-mtg-embed-qdrant-pipeline-design.md`

## Global Constraints

- New top-level sibling package `mtg-embed/`, independent of `mtg-ingestion` — no cross-package import; JSONL rows are read as plain dicts by their known field names.
- Single Qdrant collection (`mtg_rules` by default) for all three source types.
- Vector size for `create_collection` must come from calling the real model (`get_sentence_embedding_dimension()`), never a hardcoded constant.
- Point IDs are deterministic (UUID5 of a fixed namespace + a natural key) so re-runs upsert, never duplicate.
- Idempotency check compares `content_hash` already stored in Qdrant's payload against the freshly computed one — Qdrant is the single source of truth, no second local state store.
- Embedding calls are always batched (`SentenceTransformer.encode(list_of_texts, batch_size=...)`) — never one call per chunk.
- Config lives in `mtg_embed.config.Settings` (`pydantic-settings`, env prefix `MTG_EMBED_`) — nothing hardcoded that a deployment might need to change (Qdrant host/port, collection name, parsed-data dir, model name, batch sizes).
- New dependencies (`qdrant-client`, `sentence-transformers`, `pydantic-settings`, `typer`) go in `mtg-embed/pyproject.toml`.
- Per user decision during brainstorming: this plan's verification runs against a small `--limit`-capped sample, not the full ~120K-row corpus. The full corpus run is a separate, later, user-initiated step using the CLI this plan builds.

---

### Task 1: Package scaffolding and config

**Files:**
- Create: `mtg-embed/pyproject.toml`
- Create: `mtg-embed/src/mtg_embed/__init__.py`
- Create: `mtg-embed/src/mtg_embed/config.py`
- Test: `mtg-embed/tests/test_config.py`

**Interfaces:**
- Consumes: nothing (first task).
- Produces: `mtg_embed.config.Settings` (pydantic-settings `BaseSettings` subclass) with fields `qdrant_host: str`, `qdrant_port: int`, `collection_name: str`, `parsed_dir: Path`, `model_name: str`, `embed_batch_size: int`, `retrieve_batch_size: int`; module-level singleton `mtg_embed.config.settings: Settings`.

- [ ] **Step 1: Create the package directory structure and empty `__init__.py`**

```bash
mkdir -p mtg-embed/src/mtg_embed/sources mtg-embed/tests
touch mtg-embed/src/mtg_embed/__init__.py
touch mtg-embed/src/mtg_embed/sources/__init__.py
```

- [ ] **Step 2: Write `mtg-embed/pyproject.toml`**

```toml
[project]
name = "mtg-embed"
version = "0.1.0"
description = "Embeds the parsed MTG rules/cards/rulings corpus into a self-hosted Qdrant vector store."
requires-python = ">=3.12"
dependencies = [
    "pydantic-settings>=2.3",
    "qdrant-client>=1.9",
    "sentence-transformers>=3.0",
    "typer>=0.12",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "ruff>=0.5",
]

[project.scripts]
mtg-embed = "mtg_embed.cli:app"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/mtg_embed"]

[tool.ruff]
line-length = 100
target-version = "py312"
```

- [ ] **Step 3: Write the failing test for `Settings`**

`mtg-embed/tests/test_config.py`:

```python
def test_settings_defaults(monkeypatch):
    for key in (
        "MTG_EMBED_QDRANT_HOST",
        "MTG_EMBED_QDRANT_PORT",
        "MTG_EMBED_COLLECTION_NAME",
        "MTG_EMBED_MODEL_NAME",
    ):
        monkeypatch.delenv(key, raising=False)

    from mtg_embed.config import Settings

    s = Settings()
    assert s.qdrant_host == "localhost"
    assert s.qdrant_port == 6333
    assert s.collection_name == "mtg_rules"
    assert s.model_name == "BAAI/bge-base-en-v1.5"
    assert s.embed_batch_size == 32
    assert s.retrieve_batch_size == 256


def test_settings_env_override(monkeypatch):
    monkeypatch.setenv("MTG_EMBED_QDRANT_HOST", "qdrant")
    monkeypatch.setenv("MTG_EMBED_QDRANT_PORT", "7000")
    monkeypatch.setenv("MTG_EMBED_COLLECTION_NAME", "custom_collection")

    from mtg_embed.config import Settings

    s = Settings()
    assert s.qdrant_host == "qdrant"
    assert s.qdrant_port == 7000
    assert s.collection_name == "custom_collection"
```

- [ ] **Step 4: Run test to verify it fails**

Run: `cd mtg-embed && pip install -e ".[dev]" && PYTHONPATH=src python -m pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mtg_embed.config'` (the `pip install -e ".[dev]"` step is what pulls in `sentence-transformers`/`torch` — this can take several minutes on first run; only needs to happen once for the whole plan).

- [ ] **Step 5: Write `mtg-embed/src/mtg_embed/config.py`**

```python
from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All configuration lives here so the rest of the pipeline stays pure and testable.

    Override any of these with an env var, e.g. MTG_EMBED_QDRANT_HOST=qdrant.
    """

    model_config = SettingsConfigDict(env_prefix="MTG_EMBED_")

    qdrant_host: str = "localhost"
    qdrant_port: int = 6333
    collection_name: str = "mtg_rules"
    parsed_dir: Path = Path("../mtg-ingestion/data/parsed")
    model_name: str = "BAAI/bge-base-en-v1.5"
    embed_batch_size: int = 32
    retrieve_batch_size: int = 256


settings = Settings()
```

- [ ] **Step 6: Run test to verify it passes**

Run: `cd mtg-embed && PYTHONPATH=src python -m pytest tests/test_config.py -v`
Expected: PASS (2 passed)

- [ ] **Step 7: Commit**

```bash
git add mtg-embed/pyproject.toml mtg-embed/src/mtg_embed/__init__.py mtg-embed/src/mtg_embed/sources/__init__.py mtg-embed/src/mtg_embed/config.py mtg-embed/tests/test_config.py
git commit -m "feat(mtg-embed): scaffold package and config settings"
```

---

### Task 2: Deterministic point IDs and the chunk model

**Files:**
- Create: `mtg-embed/src/mtg_embed/ids.py`
- Create: `mtg-embed/src/mtg_embed/models.py`
- Test: `mtg-embed/tests/test_ids.py`
- Test: `mtg-embed/tests/test_models.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `rule_point_id(rule_id: str) -> str`, `oracle_point_id(oracle_id: str) -> str`, `ruling_point_id(oracle_id: str, index: int) -> str` (all return a UUID5 hex string). `EmbeddableChunk` dataclass with fields `point_id: str`, `source_type: str`, `text_to_embed: str`, `content_hash: str`, `payload: dict[str, Any]`.

- [ ] **Step 1: Write the failing tests for point IDs**

`mtg-embed/tests/test_ids.py`:

```python
from mtg_embed.ids import oracle_point_id, ruling_point_id, rule_point_id


def test_rule_point_id_is_deterministic():
    assert rule_point_id("100.1a") == rule_point_id("100.1a")


def test_different_rule_ids_get_different_points():
    assert rule_point_id("100.1a") != rule_point_id("100.1b")


def test_rule_and_oracle_ids_never_collide_on_the_same_raw_key():
    assert rule_point_id("abc") != oracle_point_id("abc")


def test_ruling_point_id_is_deterministic_and_index_sensitive():
    assert ruling_point_id("oid-1", 0) == ruling_point_id("oid-1", 0)
    assert ruling_point_id("oid-1", 0) != ruling_point_id("oid-1", 1)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd mtg-embed && PYTHONPATH=src python -m pytest tests/test_ids.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mtg_embed.ids'`

- [ ] **Step 3: Write `mtg-embed/src/mtg_embed/ids.py`**

```python
from __future__ import annotations

import uuid

# Fixed, arbitrary namespace so point IDs stay stable across processes and
# runs. The exact value doesn't matter -- it just must never change once
# points exist in Qdrant, or every re-run would look like a fresh corpus.
_NAMESPACE = uuid.UUID("6f6e0e2a-6f0a-4c1a-9f1a-6b0c9f6f6e2a")


def rule_point_id(rule_id: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, f"rule:{rule_id}"))


def oracle_point_id(oracle_id: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, f"oracle:{oracle_id}"))


def ruling_point_id(oracle_id: str, index: int) -> str:
    return str(uuid.uuid5(_NAMESPACE, f"ruling:{oracle_id}:{index}"))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd mtg-embed && PYTHONPATH=src python -m pytest tests/test_ids.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Write the failing test for the chunk model**

`mtg-embed/tests/test_models.py`:

```python
from mtg_embed.models import EmbeddableChunk


def test_embeddable_chunk_holds_its_fields():
    chunk = EmbeddableChunk(
        point_id="some-id",
        source_type="rule",
        text_to_embed="Section 100: General\nSome rule text.",
        content_hash="hash-1",
        payload={"source_type": "rule", "rule_id": "100.1"},
    )
    assert chunk.point_id == "some-id"
    assert chunk.source_type == "rule"
    assert chunk.payload["rule_id"] == "100.1"
```

- [ ] **Step 6: Run test to verify it fails**

Run: `cd mtg-embed && PYTHONPATH=src python -m pytest tests/test_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mtg_embed.models'`

- [ ] **Step 7: Write `mtg-embed/src/mtg_embed/models.py`**

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class EmbeddableChunk:
    """One point-to-be: everything needed to embed it and store it in Qdrant."""

    point_id: str
    source_type: str  # "rule" | "ruling" | "oracle"
    text_to_embed: str
    content_hash: str
    payload: dict[str, Any]
```

- [ ] **Step 8: Run test to verify it passes**

Run: `cd mtg-embed && PYTHONPATH=src python -m pytest tests/test_models.py -v`
Expected: PASS (1 passed)

- [ ] **Step 9: Commit**

```bash
git add mtg-embed/src/mtg_embed/ids.py mtg-embed/src/mtg_embed/models.py mtg-embed/tests/test_ids.py mtg-embed/tests/test_models.py
git commit -m "feat(mtg-embed): add deterministic point IDs and chunk model"
```

---

### Task 3: Rules source loader (hierarchy prefix)

**Files:**
- Create: `mtg-embed/src/mtg_embed/sources/rules.py`
- Test: `mtg-embed/tests/test_sources_rules.py`

**Interfaces:**
- Consumes: `mtg_embed.ids.rule_point_id`, `mtg_embed.models.EmbeddableChunk`.
- Produces: `load_rule_chunks(path: Path, limit: int | None = None) -> list[EmbeddableChunk]`.

- [ ] **Step 1: Write the failing tests**

`mtg-embed/tests/test_sources_rules.py`:

```python
import json
from pathlib import Path

from mtg_embed.ids import rule_point_id
from mtg_embed.sources.rules import load_rule_chunks

ROWS = [
    {"rule_id": "601", "text": "Casting Spells", "parent_id": None, "content_hash": "h601"},
    {"rule_id": "601.2", "text": "Playing a Spell", "parent_id": "601", "content_hash": "h6012"},
    {
        "rule_id": "601.2a",
        "text": "A player may cast an instant spell any time they have priority.",
        "parent_id": "601.2",
        "content_hash": "h6012a",
    },
]


def _write_rows(tmp_path: Path) -> Path:
    dest = tmp_path / "rules.jsonl"
    with dest.open("w", encoding="utf-8") as f:
        for row in ROWS:
            f.write(json.dumps(row) + "\n")
    return dest


def test_leaf_rule_gets_full_section_chain_prefix(tmp_path):
    path = _write_rows(tmp_path)
    chunks = {c.payload["rule_id"]: c for c in load_rule_chunks(path)}

    leaf = chunks["601.2a"]
    assert leaf.text_to_embed == (
        "Section 601: Casting Spells > 601.2: Playing a Spell\n"
        "A player may cast an instant spell any time they have priority."
    )
    assert leaf.payload["section_id"] == "601"
    assert leaf.payload["section_title"] == "Casting Spells"


def test_top_level_rule_has_no_prefix(tmp_path):
    path = _write_rows(tmp_path)
    chunks = {c.payload["rule_id"]: c for c in load_rule_chunks(path)}

    top = chunks["601"]
    assert top.text_to_embed == "Casting Spells"
    assert top.payload["section_id"] == "601"
    assert top.payload["section_title"] == "Casting Spells"


def test_point_id_and_payload_shape(tmp_path):
    path = _write_rows(tmp_path)
    chunks = {c.payload["rule_id"]: c for c in load_rule_chunks(path)}

    leaf = chunks["601.2a"]
    assert leaf.point_id == rule_point_id("601.2a")
    assert leaf.source_type == "rule"
    assert leaf.content_hash == "h6012a"
    assert leaf.payload["source_type"] == "rule"
    assert leaf.payload["text"] == "A player may cast an instant spell any time they have priority."


def test_limit_caps_number_of_chunks(tmp_path):
    path = _write_rows(tmp_path)
    chunks = load_rule_chunks(path, limit=2)
    assert len(chunks) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd mtg-embed && PYTHONPATH=src python -m pytest tests/test_sources_rules.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mtg_embed.sources.rules'`

- [ ] **Step 3: Write `mtg-embed/src/mtg_embed/sources/rules.py`**

```python
from __future__ import annotations

import json
from pathlib import Path

from mtg_embed.ids import rule_point_id
from mtg_embed.models import EmbeddableChunk


def _read_rows(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _section_chain(rule_id: str, by_id: dict[str, dict]) -> tuple[str, str, str]:
    """Walk parent_id up to the top-level ancestor.

    Returns (section_id, section_title, prefix) where prefix looks like
    "Section 601: Casting Spells > 601.2: Playing a Spell\n" -- empty for a
    top-level rule, which has no ancestors to chain.
    """
    chain: list[dict] = []
    current = by_id.get(rule_id)
    while current is not None:
        chain.append(current)
        parent_id = current.get("parent_id")
        current = by_id.get(parent_id) if parent_id else None
    chain.reverse()  # top-level ancestor first

    top = chain[0]
    section_id = top["rule_id"]
    section_title = top["text"]

    ancestors = chain[:-1]  # exclude the rule itself
    parts = [
        f"Section {row['rule_id']}: {row['text']}" if i == 0 else f"{row['rule_id']}: {row['text']}"
        for i, row in enumerate(ancestors)
    ]
    prefix = " > ".join(parts) + "\n" if parts else ""
    return section_id, section_title, prefix


def load_rule_chunks(path: Path, limit: int | None = None) -> list[EmbeddableChunk]:
    rows = _read_rows(path)
    by_id = {row["rule_id"]: row for row in rows}

    chunks: list[EmbeddableChunk] = []
    for row in rows:
        if limit is not None and len(chunks) >= limit:
            break

        section_id, section_title, prefix = _section_chain(row["rule_id"], by_id)
        chunks.append(
            EmbeddableChunk(
                point_id=rule_point_id(row["rule_id"]),
                source_type="rule",
                text_to_embed=f"{prefix}{row['text']}",
                content_hash=row["content_hash"],
                payload={
                    "source_type": "rule",
                    "content_hash": row["content_hash"],
                    "text": row["text"],
                    "rule_id": row["rule_id"],
                    "section_id": section_id,
                    "section_title": section_title,
                },
            )
        )
    return chunks
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd mtg-embed && PYTHONPATH=src python -m pytest tests/test_sources_rules.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add mtg-embed/src/mtg_embed/sources/rules.py mtg-embed/tests/test_sources_rules.py
git commit -m "feat(mtg-embed): add rules source loader with section-chain prefix"
```

---

### Task 4: Cards (oracle) source loader

**Files:**
- Create: `mtg-embed/src/mtg_embed/sources/cards.py`
- Test: `mtg-embed/tests/test_sources_cards.py`

**Interfaces:**
- Consumes: `mtg_embed.ids.oracle_point_id`, `mtg_embed.models.EmbeddableChunk`.
- Produces: `load_card_chunks(path: Path, limit: int | None = None) -> list[EmbeddableChunk]`.

- [ ] **Step 1: Write the failing tests**

`mtg-embed/tests/test_sources_cards.py`:

```python
import json
from pathlib import Path

from mtg_embed.ids import oracle_point_id
from mtg_embed.sources.cards import load_card_chunks

ROWS = [
    {
        "oracle_id": "oid-1",
        "name": "Lightning Bolt",
        "oracle_text": "Lightning Bolt deals 3 damage to any target.",
        "type_line": "Instant",
        "mana_cost": "{R}",
        "content_hash": "hcard1",
    },
    {
        "oracle_id": "oid-2",
        "name": "Static Orb",
        "oracle_text": "As long as this artifact is untapped, players can't untap more than two permanents during their untap steps.",
        "type_line": "Artifact",
        "mana_cost": "{3}",
        "content_hash": "hcard2",
    },
]


def _write_rows(tmp_path: Path) -> Path:
    dest = tmp_path / "cards.jsonl"
    with dest.open("w", encoding="utf-8") as f:
        for row in ROWS:
            f.write(json.dumps(row) + "\n")
    return dest


def test_card_chunk_has_no_prefix_and_joins_fields(tmp_path):
    path = _write_rows(tmp_path)
    chunks = {c.payload["card_name"]: c for c in load_card_chunks(path)}

    bolt = chunks["Lightning Bolt"]
    assert bolt.text_to_embed == (
        "Lightning Bolt\nInstant\n{R}\nLightning Bolt deals 3 damage to any target."
    )
    assert bolt.point_id == oracle_point_id("oid-1")
    assert bolt.source_type == "oracle"
    assert bolt.content_hash == "hcard1"
    assert bolt.payload["source_type"] == "oracle"
    assert bolt.payload["text"] == "Lightning Bolt deals 3 damage to any target."


def test_limit_caps_number_of_chunks(tmp_path):
    path = _write_rows(tmp_path)
    chunks = load_card_chunks(path, limit=1)
    assert len(chunks) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd mtg-embed && PYTHONPATH=src python -m pytest tests/test_sources_cards.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mtg_embed.sources.cards'`

- [ ] **Step 3: Write `mtg-embed/src/mtg_embed/sources/cards.py`**

```python
from __future__ import annotations

import json
from pathlib import Path

from mtg_embed.ids import oracle_point_id
from mtg_embed.models import EmbeddableChunk


def _read_rows(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def load_card_chunks(path: Path, limit: int | None = None) -> list[EmbeddableChunk]:
    rows = _read_rows(path)
    if limit is not None:
        rows = rows[:limit]

    chunks: list[EmbeddableChunk] = []
    for row in rows:
        oracle_text = row.get("oracle_text", "")
        text_to_embed = "\n".join(
            [row["name"], row.get("type_line", ""), row.get("mana_cost") or "", oracle_text]
        )
        chunks.append(
            EmbeddableChunk(
                point_id=oracle_point_id(row["oracle_id"]),
                source_type="oracle",
                text_to_embed=text_to_embed,
                content_hash=row["content_hash"],
                payload={
                    "source_type": "oracle",
                    "content_hash": row["content_hash"],
                    "text": oracle_text,
                    "card_name": row["name"],
                },
            )
        )
    return chunks
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd mtg-embed && PYTHONPATH=src python -m pytest tests/test_sources_cards.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add mtg-embed/src/mtg_embed/sources/cards.py mtg-embed/tests/test_sources_cards.py
git commit -m "feat(mtg-embed): add cards source loader"
```

---

### Task 5: Rulings source loader (joined to cards)

**Files:**
- Create: `mtg-embed/src/mtg_embed/sources/rulings.py`
- Test: `mtg-embed/tests/test_sources_rulings.py`

**Interfaces:**
- Consumes: `mtg_embed.ids.ruling_point_id`, `mtg_embed.models.EmbeddableChunk`.
- Produces: `load_ruling_chunks(rulings_path: Path, cards_path: Path, limit: int | None = None) -> tuple[list[EmbeddableChunk], int]` — the `int` is the count of rulings skipped because their `oracle_id` had no matching card.

- [ ] **Step 1: Write the failing tests**

`mtg-embed/tests/test_sources_rulings.py`:

```python
import json
from pathlib import Path

from mtg_embed.ids import ruling_point_id
from mtg_embed.sources.rulings import load_ruling_chunks

CARD_ROWS = [
    {
        "oracle_id": "oid-1",
        "name": "Lightning Bolt",
        "oracle_text": "Lightning Bolt deals 3 damage to any target.",
        "type_line": "Instant",
        "mana_cost": "{R}",
        "content_hash": "hcard1",
    }
]

RULING_ROWS = [
    {"oracle_id": "oid-1", "published_at": "2020-01-01", "comment": "First ruling.", "content_hash": "hr1"},
    {"oracle_id": "oid-1", "published_at": "2020-01-02", "comment": "Second ruling.", "content_hash": "hr2"},
    {"oracle_id": "oid-missing", "published_at": "2020-01-01", "comment": "Orphan ruling.", "content_hash": "hr3"},
]


def _write(tmp_path: Path, name: str, rows: list[dict]) -> Path:
    dest = tmp_path / name
    with dest.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
    return dest


def test_ruling_prefix_includes_card_name_and_oracle_text_snippet(tmp_path):
    rulings_path = _write(tmp_path, "rulings.jsonl", RULING_ROWS)
    cards_path = _write(tmp_path, "cards.jsonl", CARD_ROWS)

    chunks, _ = load_ruling_chunks(rulings_path, cards_path)
    first = chunks[0]

    assert first.text_to_embed == (
        "Lightning Bolt — Lightning Bolt deals 3 damage to any target.\nRuling: First ruling."
    )
    assert first.payload["card_name"] == "Lightning Bolt"
    assert first.payload["text"] == "First ruling."
    assert first.content_hash == "hr1"


def test_ruling_index_increments_per_oracle_id(tmp_path):
    rulings_path = _write(tmp_path, "rulings.jsonl", RULING_ROWS)
    cards_path = _write(tmp_path, "cards.jsonl", CARD_ROWS)

    chunks, _ = load_ruling_chunks(rulings_path, cards_path)

    assert chunks[0].point_id == ruling_point_id("oid-1", 0)
    assert chunks[1].point_id == ruling_point_id("oid-1", 1)


def test_ruling_with_no_matching_card_is_skipped_and_counted(tmp_path):
    rulings_path = _write(tmp_path, "rulings.jsonl", RULING_ROWS)
    cards_path = _write(tmp_path, "cards.jsonl", CARD_ROWS)

    chunks, skipped_no_card = load_ruling_chunks(rulings_path, cards_path)

    assert skipped_no_card == 1
    assert len(chunks) == 2


def test_limit_caps_number_of_chunks(tmp_path):
    rulings_path = _write(tmp_path, "rulings.jsonl", RULING_ROWS)
    cards_path = _write(tmp_path, "cards.jsonl", CARD_ROWS)

    chunks, _ = load_ruling_chunks(rulings_path, cards_path, limit=1)
    assert len(chunks) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd mtg-embed && PYTHONPATH=src python -m pytest tests/test_sources_rulings.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mtg_embed.sources.rulings'`

- [ ] **Step 3: Write `mtg-embed/src/mtg_embed/sources/rulings.py`**

```python
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from mtg_embed.ids import ruling_point_id
from mtg_embed.models import EmbeddableChunk

_SNIPPET_LEN = 200


def _read_rows(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def load_ruling_chunks(
    rulings_path: Path, cards_path: Path, limit: int | None = None
) -> tuple[list[EmbeddableChunk], int]:
    cards_by_oracle_id = {row["oracle_id"]: row for row in _read_rows(cards_path)}
    rows = _read_rows(rulings_path)

    chunks: list[EmbeddableChunk] = []
    skipped_no_card = 0
    # Index tracks each oracle_id's position in file order (not the kept-chunk
    # count), so it stays stable across runs regardless of which cards match.
    next_index: dict[str, int] = defaultdict(int)

    for row in rows:
        if limit is not None and len(chunks) >= limit:
            break

        oracle_id = row["oracle_id"]
        index = next_index[oracle_id]
        next_index[oracle_id] += 1

        card = cards_by_oracle_id.get(oracle_id)
        if card is None:
            skipped_no_card += 1
            continue

        snippet = card.get("oracle_text", "")[:_SNIPPET_LEN]
        prefix = f"{card['name']} — {snippet}\nRuling: "

        chunks.append(
            EmbeddableChunk(
                point_id=ruling_point_id(oracle_id, index),
                source_type="ruling",
                text_to_embed=f"{prefix}{row['comment']}",
                content_hash=row["content_hash"],
                payload={
                    "source_type": "ruling",
                    "content_hash": row["content_hash"],
                    "text": row["comment"],
                    "card_name": card["name"],
                },
            )
        )

    return chunks, skipped_no_card
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd mtg-embed && PYTHONPATH=src python -m pytest tests/test_sources_rulings.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add mtg-embed/src/mtg_embed/sources/rulings.py mtg-embed/tests/test_sources_rulings.py
git commit -m "feat(mtg-embed): add rulings source loader joined to cards"
```

---

### Task 6: Embedder seam (injectable model)

**Files:**
- Create: `mtg-embed/src/mtg_embed/embedder.py`
- Test: `mtg-embed/tests/test_embedder.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `Embedder(model, batch_size: int = 32)` with `.vector_size -> int` (property) and `.encode(texts: list[str]) -> list[list[float]]`; `load_sentence_transformer_embedder(model_name: str, batch_size: int) -> Embedder` (real-model factory, lazy `sentence_transformers` import so the module itself never requires that heavy package at import time).

- [ ] **Step 1: Write the failing tests**

`mtg-embed/tests/test_embedder.py`:

```python
from mtg_embed.embedder import Embedder


class FakeModel:
    """Stands in for sentence_transformers.SentenceTransformer's interface."""

    def __init__(self, dim: int = 4):
        self._dim = dim
        self.calls: list[tuple[int, int]] = []  # (num_texts, batch_size)

    def encode(self, texts, batch_size, show_progress_bar=False):
        self.calls.append((len(texts), batch_size))
        return [[float(len(t))] * self._dim for t in texts]

    def get_sentence_embedding_dimension(self) -> int:
        return self._dim


def test_vector_size_comes_from_the_model():
    embedder = Embedder(FakeModel(dim=768), batch_size=32)
    assert embedder.vector_size == 768


def test_encode_passes_batch_size_through_to_the_model():
    model = FakeModel()
    embedder = Embedder(model, batch_size=2)
    embedder.encode(["a", "bb", "ccc"])
    assert model.calls == [(3, 2)]


def test_encode_returns_one_vector_per_text():
    embedder = Embedder(FakeModel(dim=4), batch_size=32)
    vectors = embedder.encode(["a", "bb"])
    assert len(vectors) == 2
    assert len(vectors[0]) == 4


def test_encode_empty_list_returns_empty_list_without_calling_the_model():
    model = FakeModel()
    embedder = Embedder(model, batch_size=32)
    assert embedder.encode([]) == []
    assert model.calls == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd mtg-embed && PYTHONPATH=src python -m pytest tests/test_embedder.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mtg_embed.embedder'`

- [ ] **Step 3: Write `mtg-embed/src/mtg_embed/embedder.py`**

```python
from __future__ import annotations

from typing import Protocol, Sequence


class EncoderModel(Protocol):
    def encode(
        self, texts: Sequence[str], batch_size: int, show_progress_bar: bool
    ) -> list[list[float]]: ...

    def get_sentence_embedding_dimension(self) -> int: ...


class Embedder:
    """Thin wrapper around a sentence-transformers-shaped model.

    Takes the model as a constructor argument rather than loading it itself,
    so tests can inject a fake and never touch the real model or a network
    connection.
    """

    def __init__(self, model: EncoderModel, batch_size: int = 32):
        self._model = model
        self._batch_size = batch_size

    @property
    def vector_size(self) -> int:
        return self._model.get_sentence_embedding_dimension()

    def encode(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return self._model.encode(texts, batch_size=self._batch_size, show_progress_bar=False)


def load_sentence_transformer_embedder(model_name: str, batch_size: int) -> Embedder:
    """Real-model factory. Imports sentence_transformers lazily so importing
    this module (or anything that imports it) never requires that heavy
    dependency to be installed unless this factory is actually called."""
    from sentence_transformers import SentenceTransformer

    return Embedder(SentenceTransformer(model_name), batch_size=batch_size)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd mtg-embed && PYTHONPATH=src python -m pytest tests/test_embedder.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add mtg-embed/src/mtg_embed/embedder.py mtg-embed/tests/test_embedder.py
git commit -m "feat(mtg-embed): add injectable Embedder seam"
```

---

### Task 7: Qdrant store seam

**Files:**
- Create: `mtg-embed/src/mtg_embed/qdrant_store.py`
- Test: `mtg-embed/tests/test_qdrant_store.py`

**Interfaces:**
- Consumes: `mtg_embed.models.EmbeddableChunk`.
- Produces: `QdrantStore(client: QdrantClient, collection_name: str)` with `.ensure_collection(vector_size: int) -> None`, `.existing_hashes(point_ids: list[str]) -> dict[str, str]`, `.upsert(chunks: list[EmbeddableChunk], vectors: list[list[float]]) -> None`.

- [ ] **Step 1: Write the failing tests**

`mtg-embed/tests/test_qdrant_store.py`:

```python
from qdrant_client import QdrantClient

from mtg_embed.models import EmbeddableChunk
from mtg_embed.qdrant_store import QdrantStore


def _store() -> QdrantStore:
    # In-memory Qdrant: real client, real semantics, no network or docker.
    client = QdrantClient(location=":memory:")
    return QdrantStore(client, "test_collection")


def test_ensure_collection_is_idempotent():
    store = _store()
    store.ensure_collection(vector_size=4)
    store.ensure_collection(vector_size=4)  # must not raise on second call
    assert store.existing_hashes(["00000000-0000-0000-0000-000000000000"]) == {}


def test_upsert_then_existing_hashes_round_trips_content_hash():
    store = _store()
    store.ensure_collection(vector_size=4)

    chunk = EmbeddableChunk(
        point_id="6f6e0e2a-6f0a-4c1a-9f1a-6b0c9f6f6e2a",
        source_type="rule",
        text_to_embed="text",
        content_hash="hash-1",
        payload={"source_type": "rule", "content_hash": "hash-1", "text": "text"},
    )
    store.upsert([chunk], [[0.1, 0.2, 0.3, 0.4]])

    assert store.existing_hashes([chunk.point_id]) == {chunk.point_id: "hash-1"}


def test_existing_hashes_empty_for_unknown_ids():
    store = _store()
    store.ensure_collection(vector_size=4)
    assert store.existing_hashes(["00000000-0000-0000-0000-000000000000"]) == {}


def test_existing_hashes_of_empty_list_makes_no_call():
    store = _store()
    store.ensure_collection(vector_size=4)
    assert store.existing_hashes([]) == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd mtg-embed && PYTHONPATH=src python -m pytest tests/test_qdrant_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mtg_embed.qdrant_store'`

- [ ] **Step 3: Write `mtg-embed/src/mtg_embed/qdrant_store.py`**

```python
from __future__ import annotations

from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

from mtg_embed.models import EmbeddableChunk


class QdrantStore:
    def __init__(self, client: QdrantClient, collection_name: str):
        self._client = client
        self._collection_name = collection_name

    def ensure_collection(self, vector_size: int) -> None:
        existing = [c.name for c in self._client.get_collections().collections]
        if self._collection_name in existing:
            return
        self._client.create_collection(
            collection_name=self._collection_name,
            vectors_config=qmodels.VectorParams(size=vector_size, distance=qmodels.Distance.COSINE),
        )

    def existing_hashes(self, point_ids: list[str]) -> dict[str, str]:
        if not point_ids:
            return {}
        points = self._client.retrieve(
            collection_name=self._collection_name,
            ids=point_ids,
            with_payload=["content_hash"],
        )
        return {str(p.id): p.payload["content_hash"] for p in points if p.payload}

    def upsert(self, chunks: list[EmbeddableChunk], vectors: list[list[float]]) -> None:
        if not chunks:
            return
        points = [
            qmodels.PointStruct(id=chunk.point_id, vector=vector, payload=chunk.payload)
            for chunk, vector in zip(chunks, vectors)
        ]
        self._client.upsert(collection_name=self._collection_name, points=points)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd mtg-embed && PYTHONPATH=src python -m pytest tests/test_qdrant_store.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add mtg-embed/src/mtg_embed/qdrant_store.py mtg-embed/tests/test_qdrant_store.py
git commit -m "feat(mtg-embed): add QdrantStore seam over the Qdrant client"
```

---

### Task 8: Pipeline orchestration (the idempotency logic)

**Files:**
- Create: `mtg-embed/src/mtg_embed/pipeline.py`
- Test: `mtg-embed/tests/test_pipeline.py`

**Interfaces:**
- Consumes: `Embedder.encode`, `Embedder.vector_size` (Task 6); `QdrantStore.ensure_collection`, `.existing_hashes`, `.upsert` (Task 7); `EmbeddableChunk` (Task 2).
- Produces: `RunSummary` dataclass (`source_type: str`, `total_seen: int`, `embedded: int`, `skipped_unchanged: int`); `embed_and_store(chunks: list[EmbeddableChunk], store: QdrantStore, embedder: Embedder, retrieve_batch_size: int = 256) -> RunSummary`.

This is the task that directly proves the idempotency requirement: re-running with unchanged `content_hash` values must skip re-embedding, and a changed hash must trigger re-embedding.

- [ ] **Step 1: Write the failing tests**

`mtg-embed/tests/test_pipeline.py`:

```python
from qdrant_client import QdrantClient

from mtg_embed.embedder import Embedder
from mtg_embed.models import EmbeddableChunk
from mtg_embed.pipeline import embed_and_store
from mtg_embed.qdrant_store import QdrantStore


class FakeModel:
    def __init__(self, dim: int = 4):
        self._dim = dim
        self.encode_calls = 0

    def encode(self, texts, batch_size, show_progress_bar=False):
        self.encode_calls += 1
        return [[float(len(t))] * self._dim for t in texts]

    def get_sentence_embedding_dimension(self) -> int:
        return self._dim


def _chunk(point_id: str, content_hash: str) -> EmbeddableChunk:
    return EmbeddableChunk(
        point_id=point_id,
        source_type="rule",
        text_to_embed=f"text for {point_id}",
        content_hash=content_hash,
        payload={"source_type": "rule", "content_hash": content_hash, "text": "x"},
    )


def _fresh_store() -> QdrantStore:
    store = QdrantStore(QdrantClient(location=":memory:"), "pipeline_test")
    store.ensure_collection(vector_size=4)
    return store


def test_first_run_embeds_every_chunk():
    store = _fresh_store()
    embedder = Embedder(FakeModel(), batch_size=32)
    chunks = [
        _chunk("11111111-1111-1111-1111-111111111111", "h1"),
        _chunk("22222222-2222-2222-2222-222222222222", "h2"),
    ]

    summary = embed_and_store(chunks, store, embedder)

    assert summary.embedded == 2
    assert summary.skipped_unchanged == 0
    assert summary.total_seen == 2
    assert summary.source_type == "rule"


def test_second_run_with_unchanged_hashes_skips_everything():
    store = _fresh_store()
    embedder = Embedder(FakeModel(), batch_size=32)
    chunks = [
        _chunk("11111111-1111-1111-1111-111111111111", "h1"),
        _chunk("22222222-2222-2222-2222-222222222222", "h2"),
    ]

    embed_and_store(chunks, store, embedder)
    summary = embed_and_store(chunks, store, embedder)  # re-run, nothing changed

    assert summary.embedded == 0
    assert summary.skipped_unchanged == 2


def test_changed_content_hash_gets_re_embedded():
    store = _fresh_store()
    embedder = Embedder(FakeModel(), batch_size=32)
    point_id = "11111111-1111-1111-1111-111111111111"

    embed_and_store([_chunk(point_id, "h1")], store, embedder)
    summary = embed_and_store([_chunk(point_id, "h1-changed")], store, embedder)

    assert summary.embedded == 1
    assert summary.skipped_unchanged == 0


def test_empty_chunk_list_returns_zeroed_summary():
    store = _fresh_store()
    embedder = Embedder(FakeModel(), batch_size=32)

    summary = embed_and_store([], store, embedder)

    assert summary.total_seen == 0
    assert summary.embedded == 0
    assert summary.skipped_unchanged == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd mtg-embed && PYTHONPATH=src python -m pytest tests/test_pipeline.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mtg_embed.pipeline'`

- [ ] **Step 3: Write `mtg-embed/src/mtg_embed/pipeline.py`**

```python
from __future__ import annotations

from dataclasses import dataclass

from mtg_embed.embedder import Embedder
from mtg_embed.models import EmbeddableChunk
from mtg_embed.qdrant_store import QdrantStore


@dataclass
class RunSummary:
    source_type: str
    total_seen: int
    embedded: int
    skipped_unchanged: int


def _batched(items: list[EmbeddableChunk], size: int) -> list[list[EmbeddableChunk]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def embed_and_store(
    chunks: list[EmbeddableChunk],
    store: QdrantStore,
    embedder: Embedder,
    retrieve_batch_size: int = 256,
) -> RunSummary:
    if not chunks:
        return RunSummary(source_type="", total_seen=0, embedded=0, skipped_unchanged=0)

    source_type = chunks[0].source_type
    embedded = 0
    skipped = 0

    for batch in _batched(chunks, retrieve_batch_size):
        existing = store.existing_hashes([c.point_id for c in batch])
        to_embed = [c for c in batch if existing.get(c.point_id) != c.content_hash]
        skipped += len(batch) - len(to_embed)

        if to_embed:
            vectors = embedder.encode([c.text_to_embed for c in to_embed])
            store.upsert(to_embed, vectors)
            embedded += len(to_embed)

    return RunSummary(
        source_type=source_type,
        total_seen=len(chunks),
        embedded=embedded,
        skipped_unchanged=skipped,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd mtg-embed && PYTHONPATH=src python -m pytest tests/test_pipeline.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add mtg-embed/src/mtg_embed/pipeline.py mtg-embed/tests/test_pipeline.py
git commit -m "feat(mtg-embed): add embed_and_store pipeline with content_hash idempotency"
```

---

### Task 9: CLI

**Files:**
- Create: `mtg-embed/src/mtg_embed/cli.py`
- Test: `mtg-embed/tests/test_cli.py`

**Interfaces:**
- Consumes: `Settings`/`settings` (Task 1), `load_rule_chunks` (Task 3), `load_card_chunks` (Task 4), `load_ruling_chunks` (Task 5), `load_sentence_transformer_embedder`, `Embedder` (Task 6), `QdrantStore` (Task 7), `embed_and_store`, `RunSummary` (Task 8).
- Produces: `mtg_embed.cli.app` (typer app, entry point `mtg-embed`), `mtg_embed.cli._latest(directory: Path, pattern: str) -> Path`.

`SentenceTransformer` and `QdrantClient` are only imported inside the `run` command body (not at module top), so importing `mtg_embed.cli` — and testing argument validation — never requires a network connection or the model weights.

- [ ] **Step 1: Write the failing tests**

`mtg-embed/tests/test_cli.py`:

```python
from pathlib import Path

import pytest
from typer.testing import CliRunner

from mtg_embed.cli import _latest, app

runner = CliRunner()


def test_latest_picks_the_lexicographically_last_match(tmp_path: Path):
    (tmp_path / "rules_2026-01-01.jsonl").write_text("{}\n")
    (tmp_path / "rules_2026-08-25.jsonl").write_text("{}\n")

    result = _latest(tmp_path, "rules_*.jsonl")

    assert result.name == "rules_2026-08-25.jsonl"


def test_latest_raises_when_no_files_match(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        _latest(tmp_path, "rules_*.jsonl")


def test_run_rejects_unknown_source_before_touching_network():
    result = runner.invoke(app, ["run", "--source", "bogus"])
    assert result.exit_code != 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd mtg-embed && PYTHONPATH=src python -m pytest tests/test_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mtg_embed.cli'`

- [ ] **Step 3: Write `mtg-embed/src/mtg_embed/cli.py`**

```python
from __future__ import annotations

from pathlib import Path

import typer

from mtg_embed.config import settings
from mtg_embed.pipeline import embed_and_store
from mtg_embed.sources.cards import load_card_chunks
from mtg_embed.sources.rules import load_rule_chunks
from mtg_embed.sources.rulings import load_ruling_chunks

app = typer.Typer(help="Embed the parsed MTG rules/cards/rulings corpus into Qdrant.")

_SOURCES = ("rules", "cards", "rulings")


def _latest(directory: Path, pattern: str) -> Path:
    matches = sorted(directory.glob(pattern))
    if not matches:
        raise FileNotFoundError(f"No files matching {pattern!r} in {directory}")
    return matches[-1]


@app.command("run")
def run(
    source: str = typer.Option("all", help="rules|cards|rulings|all"),
    limit: int | None = typer.Option(None, help="Cap rows read per source, for cheap verification runs."),
) -> None:
    sources = _SOURCES if source == "all" else (source,)
    for name in sources:
        if name not in _SOURCES:
            raise typer.BadParameter(f"Unknown source {name!r}; expected one of {_SOURCES} or 'all'.")

    # Imported here, not at module top, so argument validation above never
    # requires the model weights or a Qdrant connection.
    from qdrant_client import QdrantClient

    from mtg_embed.embedder import load_sentence_transformer_embedder
    from mtg_embed.qdrant_store import QdrantStore

    client = QdrantClient(host=settings.qdrant_host, port=settings.qdrant_port)
    store = QdrantStore(client, settings.collection_name)
    embedder = load_sentence_transformer_embedder(settings.model_name, settings.embed_batch_size)
    store.ensure_collection(embedder.vector_size)

    summaries = []
    skipped_no_card = 0

    if "rules" in sources:
        rules_path = _latest(settings.parsed_dir, "rules_*.jsonl")
        chunks = load_rule_chunks(rules_path, limit=limit)
        summaries.append(embed_and_store(chunks, store, embedder, settings.retrieve_batch_size))

    if "cards" in sources:
        cards_path = _latest(settings.parsed_dir, "cards_*.jsonl")
        chunks = load_card_chunks(cards_path, limit=limit)
        summaries.append(embed_and_store(chunks, store, embedder, settings.retrieve_batch_size))

    if "rulings" in sources:
        rulings_path = _latest(settings.parsed_dir, "rulings_*.jsonl")
        cards_path = _latest(settings.parsed_dir, "cards_*.jsonl")
        chunks, skipped_no_card = load_ruling_chunks(rulings_path, cards_path, limit=limit)
        summaries.append(embed_and_store(chunks, store, embedder, settings.retrieve_batch_size))

    typer.echo("")
    typer.echo("Embedding summary:")
    grand_total = grand_embedded = grand_skipped = 0
    for s in summaries:
        typer.echo(
            f"  {s.source_type}: embedded={s.embedded} skipped_unchanged={s.skipped_unchanged} "
            f"total_seen={s.total_seen}"
        )
        grand_total += s.total_seen
        grand_embedded += s.embedded
        grand_skipped += s.skipped_unchanged
    if skipped_no_card:
        typer.echo(f"  rulings skipped (no matching card): {skipped_no_card}")
    typer.echo(f"  TOTAL: embedded={grand_embedded} skipped_unchanged={grand_skipped} total_seen={grand_total}")


if __name__ == "__main__":
    app()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd mtg-embed && PYTHONPATH=src python -m pytest tests/test_cli.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Run the full test suite to confirm nothing regressed**

Run: `cd mtg-embed && PYTHONPATH=src python -m pytest -v`
Expected: all tests across every task pass (config, ids, models, sources x3, embedder, qdrant_store, pipeline, cli)

- [ ] **Step 6: Commit**

```bash
git add mtg-embed/src/mtg_embed/cli.py mtg-embed/tests/test_cli.py
git commit -m "feat(mtg-embed): add mtg-embed run CLI"
```

---

### Task 10: Docker packaging

**Files:**
- Create: `mtg-embed/Dockerfile`
- Create: `mtg-embed/docker-compose.yml`

**Interfaces:**
- Consumes: `mtg-embed` console script entry point (`mtg-embed` command, Task 9), `Settings` env vars `MTG_EMBED_QDRANT_HOST`, `MTG_EMBED_QDRANT_PORT`, `MTG_EMBED_PARSED_DIR` (Task 1).
- Produces: a buildable image and a `docker compose` stack (`qdrant` + `embed` services) that Task 11 runs.

- [ ] **Step 1: Write `mtg-embed/Dockerfile`**

```dockerfile
FROM python:3.12-slim

WORKDIR /app

# Copy only what's needed to install the package first, so dependency
# installs get cached in their own layer and aren't invalidated by every
# source change.
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .

ENTRYPOINT ["mtg-embed"]
CMD ["run", "--help"]
```

- [ ] **Step 2: Write `mtg-embed/docker-compose.yml`**

```yaml
services:
  qdrant:
    image: qdrant/qdrant:latest
    ports:
      - "6333:6333"
    volumes:
      - qdrant_storage:/qdrant/storage

  embed:
    build: .
    depends_on:
      - qdrant
    environment:
      MTG_EMBED_QDRANT_HOST: qdrant
      MTG_EMBED_QDRANT_PORT: "6333"
      MTG_EMBED_PARSED_DIR: /app/data
    volumes:
      - ../mtg-ingestion/data/parsed:/app/data:ro
    # Override this per-run, e.g.:
    #   docker compose run --rm embed run --source rules --limit 50
    command: ["run", "--source", "all"]

volumes:
  qdrant_storage:
```

- [ ] **Step 3: Validate the compose file parses correctly**

Run: `cd mtg-embed && docker compose config`
Expected: prints the resolved config with no errors (confirms YAML is valid and the `build`/`volumes`/`environment` keys resolve) — if the `docker` CLI isn't available in this environment, visually re-check the YAML indentation and the relative volume path instead, and revisit this step once Docker is available.

- [ ] **Step 4: Commit**

```bash
git add mtg-embed/Dockerfile mtg-embed/docker-compose.yml
git commit -m "feat(mtg-embed): add Docker packaging with a qdrant service"
```

---

### Task 11: End-to-end sample verification

**Files:** none created — this task runs the pipeline built in Tasks 1-10 against a small real slice of the actual `mtg-ingestion` output, using the real model and a real Qdrant, to prove the seams (Tasks 6 and 7) are wired correctly. Per the brainstorming decision, this is a `--limit`-capped sample, not the full ~120K-row corpus — that full run is a separate step left for you to kick off afterward with `mtg-embed run --source all` (no `--limit`).

**Interfaces:**
- Consumes: everything built in Tasks 1-10.
- Produces: nothing new — this is a verification task.

- [ ] **Step 1: Start Qdrant**

Run: `cd mtg-embed && docker compose up -d qdrant`
Expected: the `qdrant` container starts and is healthy (`docker compose ps` shows it `running`); if Docker isn't available in this environment, fall back to local embedded mode for this verification only by setting `MTG_EMBED_QDRANT_HOST` unused and instead running Qdrant's local-mode client directly in a throwaway Python check — note in the final report that Docker Compose itself was not exercised.

- [ ] **Step 2: Confirm dependencies are installed and the parsed source files exist**

Run: `cd mtg-embed && PYTHONPATH=src python -c "import qdrant_client, sentence_transformers; print('ok')"`
Expected: prints `ok`

Run: `ls ../mtg-ingestion/data/parsed/`
Expected: shows the existing `rules_*.jsonl`, `cards_*.jsonl`, `rulings_*.jsonl` files

- [ ] **Step 3: Run the pipeline against a small sample of every source**

Run: `cd mtg-embed && MTG_EMBED_QDRANT_HOST=localhost MTG_EMBED_QDRANT_PORT=6333 PYTHONPATH=src python -m mtg_embed.cli run --source all --limit 50`
Expected: prints an "Embedding summary:" block with `rule: embedded=50 skipped_unchanged=0 total_seen=50`, `oracle: embedded=50 skipped_unchanged=0 total_seen=50`, and a `ruling` line (`embedded` may be less than 50 total_seen if some sampled rulings had no matching card within the first 50 rows — that's expected, not a bug), plus a `TOTAL` line.

- [ ] **Step 4: Re-run the exact same command and confirm idempotency end to end**

Run: `cd mtg-embed && MTG_EMBED_QDRANT_HOST=localhost MTG_EMBED_QDRANT_PORT=6333 PYTHONPATH=src python -m mtg_embed.cli run --source all --limit 50`
Expected: the summary now shows `embedded=0` for every source type and `skipped_unchanged` equal to the `embedded` counts from Step 3 — proving the content_hash check works against a real Qdrant instance and a real model run, not just the in-memory unit tests.

- [ ] **Step 5: Report the result**

Summarize, in your final report to the user: the per-source-type counts from Steps 3 and 4, confirmation that the second run skipped everything, and the exact command to run the full corpus later (`mtg-embed run --source all`, no `--limit`, expect this to take a while on CPU given ~120K rows).

- [ ] **Step 6: Stop the local Qdrant container** (leaves no long-running service behind)

Run: `cd mtg-embed && docker compose down`
