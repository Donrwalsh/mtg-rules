from __future__ import annotations

from sqlalchemy import JSON, Boolean, Column, DateTime, Integer, MetaData, Table, Text, func, select
from sqlalchemy.engine import Engine

metadata = MetaData()

query_history = Table(
    "query_history",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("query", Text, nullable=False),
    Column("answer", Text, nullable=True),
    Column("results", JSON, nullable=False),
    Column("model", Text, nullable=False),
    Column("error", Text, nullable=True),
    Column("citations", JSON, nullable=True),
    Column("citation_stats", JSON, nullable=True),
    Column("rule_references", JSON, nullable=True),
    Column("cached", Boolean, nullable=False, server_default="0"),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)


def save_history(
    engine: Engine,
    *,
    query: str,
    answer: str | None,
    results: list[dict],
    model: str,
    error: str | None,
    citations: list[dict] | None = None,
    citation_stats: dict | None = None,
    rule_references: list[str] | None = None,
    cached: bool = False,
) -> None:
    with engine.begin() as conn:
        conn.execute(
            query_history.insert().values(
                query=query,
                answer=answer,
                results=results,
                model=model,
                error=error,
                citations=citations,
                citation_stats=citation_stats,
                rule_references=rule_references,
                cached=cached,
            )
        )


def list_history(engine: Engine, *, limit: int = 50, offset: int = 0) -> list[dict]:
    stmt = (
        select(query_history)
        .order_by(query_history.c.created_at.desc(), query_history.c.id.desc())
        .limit(limit)
        .offset(offset)
    )
    with engine.connect() as conn:
        rows = conn.execute(stmt).mappings().all()
    return [dict(row) for row in rows]


def get_history(engine: Engine, history_id: int) -> dict | None:
    stmt = select(query_history).where(query_history.c.id == history_id)
    with engine.connect() as conn:
        row = conn.execute(stmt).mappings().first()
    return dict(row) if row else None
