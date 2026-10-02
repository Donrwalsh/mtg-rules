"""Reuse of generated answers for repeated questions."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

from sqlalchemy import JSON, Column, DateTime, Integer, Table, Text, select
from sqlalchemy.engine import Engine

from mtg_api.config import OVERRIDABLE_SETTINGS, Settings, generator_label
from mtg_api.history import metadata
from mtg_api.llm import PROMPT_VERSION
from mtg_api.usage import as_utc

# Written by deploy/sync_data.py next to the parsed JSONL; any sync is a new
# data version, so no cached answer outlives a rules, card or ruling update.
DATA_VERSION_FILE = "data_version"

answer_cache = Table(
    "answer_cache",
    metadata,
    # sha256 of the normalized question, prompt version, settings and data version.
    Column("key", Text, primary_key=True),
    Column("normalized_query", Text, nullable=False),
    # The whole QueryResponse, so [n] citations stay tied to their sources.
    Column("response", JSON, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("hit_count", Integer, nullable=False, server_default="0"),
)


def normalize_query(query: str) -> str:
    return " ".join(query.lower().split()).rstrip("?!. ")


def cache_key(query: str, s: Settings, data_version: str) -> str:
    # Every overridable setting shapes either the context or the answer.
    parts = {
        "query": normalize_query(query),
        "prompt_version": PROMPT_VERSION,
        "data_version": data_version,
        "settings": {name: getattr(s, name) for name in OVERRIDABLE_SETTINGS},
    }
    if s.answer_provider != "gemini":
        # Gemini keys stay exactly as they were, so production's cache survives.
        parts["generator"] = generator_label(s)
    encoded = json.dumps(parts, sort_keys=True, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def get_cached(engine: Engine, key: str) -> tuple[dict, datetime] | None:
    with engine.begin() as conn:
        row = conn.execute(
            select(answer_cache.c.response, answer_cache.c.created_at).where(
                answer_cache.c.key == key
            )
        ).first()
        if row is None:
            return None
        conn.execute(
            answer_cache.update()
            .where(answer_cache.c.key == key)
            .values(hit_count=answer_cache.c.hit_count + 1)
        )
    return row.response, as_utc(row.created_at)


def put_cached(
    engine: Engine, key: str, *, normalized_query: str, response: dict, now: datetime
) -> None:
    # Delete + insert rather than a dialect-specific upsert: an admin "fresh"
    # answer replaces the old entry.
    with engine.begin() as conn:
        conn.execute(answer_cache.delete().where(answer_cache.c.key == key))
        conn.execute(
            answer_cache.insert().values(
                key=key,
                normalized_query=normalized_query,
                response=response,
                created_at=now,
                hit_count=0,
            )
        )


def read_data_version(parsed_dir: Path) -> str:
    marker = parsed_dir / DATA_VERSION_FILE
    if marker.is_file():
        return marker.read_text(encoding="utf-8").strip()
    # Dev stack (no sync): the latest parsed files stand in for the version.
    names = []
    for kind in ("cards", "rules"):
        matches = sorted(parsed_dir.glob(f"{kind}_*.jsonl")) if parsed_dir.is_dir() else []
        names.append(matches[-1].name if matches else "none")
    return "|".join(names)
