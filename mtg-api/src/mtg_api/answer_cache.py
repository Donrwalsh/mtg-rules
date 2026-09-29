"""Reuse of generated answers for repeated questions."""

from __future__ import annotations

from sqlalchemy import JSON, Column, DateTime, Integer, Table, Text

from mtg_api.history import metadata

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
