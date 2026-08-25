from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

from pydantic import BaseModel


def _sha256(*parts: str) -> str:
    """Hash the fields that define a record's meaningful content.

    Used later by the diff stage to detect whether a record actually changed
    between ingestion runs, so unchanged records can skip re-embedding.
    Unit separator (\\x1f) between parts avoids accidental collisions like
    ("ab", "c") vs ("a", "bc").
    """
    joined = "\x1f".join(parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


class RuleChunk(BaseModel):
    """One numbered rule or subrule from the Comprehensive Rules.

    rule_id is the citable identifier (e.g. "702.19b") and doubles as the
    natural primary key once this lands in Postgres.
    """

    rule_id: str
    text: str
    parent_id: str | None = None
    content_hash: str = ""

    def model_post_init(self, __context: Any) -> None:
        if not self.content_hash:
            self.content_hash = _sha256(self.rule_id, self.text)


class Card(BaseModel):
    """Oracle-level card identity. Per-printing data (set, collector number,
    art) is intentionally not modeled -- rules Q&A only cares about the one
    canonical rules text per unique card.
    """

    oracle_id: str
    name: str
    oracle_text: str = ""
    type_line: str = ""
    mana_cost: str | None = None
    content_hash: str = ""

    def model_post_init(self, __context: Any) -> None:
        if not self.content_hash:
            self.content_hash = _sha256(
                self.oracle_id,
                self.name,
                self.oracle_text,
                self.type_line,
                self.mana_cost or "",
            )


class Ruling(BaseModel):
    """A single Scryfall ruling, linked back to a Card via oracle_id."""

    oracle_id: str
    published_at: str
    comment: str
    content_hash: str = ""

    def model_post_init(self, __context: Any) -> None:
        if not self.content_hash:
            self.content_hash = _sha256(self.oracle_id, self.published_at, self.comment)


class IngestionRun(BaseModel):
    """Bookkeeping record for one fetch+parse run of a single source.

    Not wired into storage yet (that's the persistence stage we're deferring),
    but the shape is settled now so the diff stage can adopt it without a
    model migration later.
    """

    source: str
    started_at: datetime
    finished_at: datetime | None = None
    record_count: int = 0
    raw_path: str | None = None
    parsed_path: str | None = None
