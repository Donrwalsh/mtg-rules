"""Gemini usage accounting and the per-IP / global gate in front of it."""

from __future__ import annotations

from sqlalchemy import Boolean, Column, DateTime, Float, Index, Integer, Table, Text

from mtg_api.history import metadata

# One row per /api/v1/query call that asked for an answer (outside eval mode).
llm_usage = Table(
    "llm_usage",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    # Set explicitly (UTC) by the app, never by the database, so quota
    # windows compare like with like on Postgres and SQLite.
    Column("created_at", DateTime(timezone=True), nullable=False),
    # IPv4 address, or IPv6 /64 in CIDR form.
    Column("ip_bucket", Text, nullable=False),
    Column("is_admin", Boolean, nullable=False),
    # generated | cached | degraded_ip | degraded_global | error
    Column("outcome", Text, nullable=False),
    Column("model", Text, nullable=False),
    Column("input_tokens", Integer, nullable=False),
    Column("output_tokens", Integer, nullable=False),
    Column("thinking_tokens", Integer, nullable=False),
    Column("cost_usd", Float, nullable=False),
    Index("ix_llm_usage_created_at", "created_at"),
    Index("ix_llm_usage_ip_bucket_created_at", "ip_bucket", "created_at"),
)
