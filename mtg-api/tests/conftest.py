from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool

from mtg_api.history import metadata as history_metadata


def memory_engine() -> Engine:
    """A fresh in-memory SQLite engine with the query_history schema created.

    Uses StaticPool + check_same_thread=False because FastAPI runs sync path
    operations in a worker thread pool -- the default SQLite :memory: pooling
    ties a connection to the thread that created it, which would hand a
    request a different, schema-less database than the one a test set up on
    the main thread.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    history_metadata.create_all(engine)
    return engine
