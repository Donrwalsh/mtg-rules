from datetime import UTC, datetime

from conftest import admin_client, memory_engine
from fastapi.testclient import TestClient

from mtg_api.main import app, get_db_engine
from mtg_api.usage import record_usage


def test_usage_requires_admin():
    app.dependency_overrides[get_db_engine] = memory_engine
    try:
        assert TestClient(app).get("/api/v1/admin/usage").status_code == 401
    finally:
        app.dependency_overrides.clear()


def test_usage_returns_the_summary(monkeypatch):
    engine = memory_engine()
    record_usage(
        engine,
        now=datetime.now(UTC),
        ip_bucket="203.0.113.7",
        is_admin=False,
        outcome="generated",
        model="m",
        cost=0.02,
    )
    app.dependency_overrides[get_db_engine] = lambda: engine
    try:
        body = admin_client(monkeypatch).get("/api/v1/admin/usage").json()
    finally:
        app.dependency_overrides.clear()
    assert body["days"][-1]["spend_usd"] == 0.02
    assert body["top_ip_buckets"][0]["ip_bucket"] == "203.0.113.7"
    assert set(body) == {"budget_usd", "days", "cache_hit_rate", "top_ip_buckets"}
