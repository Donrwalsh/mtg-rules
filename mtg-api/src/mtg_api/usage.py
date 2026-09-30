"""Gemini usage accounting and the per-IP / global gate in front of it."""

from __future__ import annotations

import ipaddress
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    Index,
    Integer,
    Table,
    Text,
    func,
    select,
)
from sqlalchemy.engine import Engine

from mtg_api.config import Settings
from mtg_api.history import metadata
from mtg_api.llm import Generation

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

# Outcomes that called Gemini, and so use up a visitor's quota.
ANSWER_OUTCOMES = ("generated", "error")


def as_utc(dt: datetime) -> datetime:
    """SQLite hands back naive datetimes; every value here is stored as UTC."""
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def day_start(now: datetime) -> datetime:
    return now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


def ip_bucket(host: str) -> str:
    """The unit quotas count against: an IPv4 address, or an IPv6 /64 (one
    subscriber usually holds a whole /64). Anything else passes through."""
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return host
    if addr.version == 6:
        if addr.ipv4_mapped:
            return str(addr.ipv4_mapped)
        return str(ipaddress.ip_network(f"{addr}/64", strict=False))
    return str(addr)


def cost_usd(generation: Generation, s: Settings) -> float:
    output = generation.output_tokens + generation.thinking_tokens
    return (
        generation.input_tokens * s.gemini_input_price_per_mtok
        + output * s.gemini_output_price_per_mtok
    ) / 1_000_000


def record_usage(
    engine: Engine,
    *,
    now: datetime,
    ip_bucket: str,
    is_admin: bool,
    outcome: str,
    model: str,
    generation: Generation | None = None,
    cost: float = 0.0,
) -> None:
    g = generation or Generation("")
    with engine.begin() as conn:
        conn.execute(
            llm_usage.insert().values(
                created_at=now,
                ip_bucket=ip_bucket,
                is_admin=is_admin,
                outcome=outcome,
                model=model,
                input_tokens=g.input_tokens,
                output_tokens=g.output_tokens,
                thinking_tokens=g.thinking_tokens,
                cost_usd=cost,
            )
        )


def spend_since(engine: Engine, since: datetime) -> float:
    stmt = select(func.coalesce(func.sum(llm_usage.c.cost_usd), 0.0)).where(
        llm_usage.c.created_at >= since
    )
    with engine.connect() as conn:
        return float(conn.execute(stmt).scalar_one())


def answers_since(engine: Engine, bucket: str, since: datetime) -> int:
    stmt = select(func.count()).where(
        llm_usage.c.ip_bucket == bucket,
        llm_usage.c.created_at >= since,
        llm_usage.c.is_admin.is_(False),
        llm_usage.c.outcome.in_(ANSWER_OUTCOMES),
    )
    with engine.connect() as conn:
        return int(conn.execute(stmt).scalar_one())


@dataclass(frozen=True)
class Gate:
    degraded: str | None  # None | "ip_quota" | "global_budget"
    answers_remaining: int


def check_gate(engine: Engine, s: Settings, bucket: str, now: datetime) -> Gate:
    """Whether this visitor may have an LLM answer now. The global cap is
    checked first so its message wins when both apply."""
    today = day_start(now)
    used_today = answers_since(engine, bucket, today)
    remaining = max(0, s.ip_daily_llm_limit - used_today)
    if spend_since(engine, today) >= s.daily_budget_usd:
        return Gate("global_budget", remaining)
    if used_today >= s.ip_daily_llm_limit:
        return Gate("ip_quota", 0)
    window_start = now - timedelta(minutes=s.ip_window_minutes)
    if answers_since(engine, bucket, window_start) >= s.ip_window_llm_limit:
        return Gate("ip_quota", remaining)
    return Gate(None, remaining)


def usage_summary(
    engine: Engine, s: Settings, now: datetime, *, days: int = 7, top: int = 10
) -> dict:
    """The admin usage panel: spend and outcomes per UTC day (oldest first,
    today last), today's cache hit rate and today's busiest IP buckets.
    Aggregated in Python: a week of rows is small, and it keeps the SQL
    portable between Postgres and the SQLite tests."""
    today = day_start(now)
    first = today - timedelta(days=days - 1)
    with engine.connect() as conn:
        rows = conn.execute(select(llm_usage).where(llm_usage.c.created_at >= first)).mappings()
        rows = [dict(r) for r in rows]

    per_day = {
        (first + timedelta(days=i)).date(): {"spend_usd": 0.0, "outcomes": Counter()}
        for i in range(days)
    }
    buckets: dict[str, dict] = {}
    for row in rows:
        created = as_utc(row["created_at"])
        day = per_day.get(created.date())
        if day is None:
            continue
        day["spend_usd"] += row["cost_usd"]
        day["outcomes"][row["outcome"]] += 1
        if created >= today:
            b = buckets.setdefault(
                row["ip_bucket"],
                {"ip_bucket": row["ip_bucket"], "requests": 0, "answers": 0, "spend_usd": 0.0},
            )
            b["requests"] += 1
            b["answers"] += row["outcome"] in ANSWER_OUTCOMES
            b["spend_usd"] += row["cost_usd"]

    today_outcomes = per_day[today.date()]["outcomes"]
    asked = today_outcomes["cached"] + sum(today_outcomes[o] for o in ANSWER_OUTCOMES)
    return {
        "budget_usd": s.daily_budget_usd,
        "days": [
            {
                "date": date.isoformat(),
                "spend_usd": day["spend_usd"],
                "outcomes": dict(day["outcomes"]),
            }
            for date, day in per_day.items()
        ],
        "cache_hit_rate": today_outcomes["cached"] / asked if asked else None,
        "top_ip_buckets": sorted(buckets.values(), key=lambda b: -b["requests"])[:top],
    }


def check_gating_config(s: Settings) -> None:
    """Gating counts dollars, so it needs both prices. Fail at startup, not
    by silently treating every answer as free."""
    if s.gating_enabled and (
        s.gemini_input_price_per_mtok <= 0 or s.gemini_output_price_per_mtok <= 0
    ):
        raise RuntimeError(
            "MTG_API_GATING_ENABLED needs MTG_API_GEMINI_INPUT_PRICE_PER_MTOK and "
            "MTG_API_GEMINI_OUTPUT_PRICE_PER_MTOK"
        )
