from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool

from mtg_api.history import metadata as history_metadata

# Side-effect imports to register tables on metadata
_answer_cache = __import__("mtg_api.answer_cache", fromlist=[""])
_usage = __import__("mtg_api.usage", fromlist=[""])


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


def admin_client(monkeypatch, password: str = "pw"):
    """A TestClient logged in as the admin, sending the X-Admin-Request
    marker on every request."""
    from fastapi.testclient import TestClient
    from pydantic import SecretStr

    from mtg_api.config import settings
    from mtg_api.main import app

    monkeypatch.setattr(settings, "admin_password", SecretStr(password))
    client = TestClient(app, headers={"X-Admin-Request": "1"})
    assert client.post("/api/v1/auth/login", json={"password": password}).status_code == 200
    return client


class StreamsFromGenerate:
    """For fake answerers that define generate(): serves it as a one-chunk
    stream, the way the pipeline reads answers. Raises on first read, as a
    real failed stream does."""

    def stream(self, query, context):
        from mtg_api.llm import StreamChunk

        g = self.generate(query, context)
        yield StreamChunk(
            text=g.text,
            input_tokens=g.input_tokens,
            output_tokens=g.output_tokens,
            thinking_tokens=g.thinking_tokens,
            finish_reason=g.finish_reason,
        )
