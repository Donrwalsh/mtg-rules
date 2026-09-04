from __future__ import annotations

from sqlalchemy import Column, DateTime, Integer, JSON, MetaData, Table, Text, func, select
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
) -> None:
    with engine.begin() as conn:
        conn.execute(
            query_history.insert().values(
                query=query,
                answer=answer,
                results=results,
                model=model,
                error=error,
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
