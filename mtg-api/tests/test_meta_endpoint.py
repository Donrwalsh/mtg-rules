from fastapi.testclient import TestClient

from mtg_api import main
from mtg_api.main import app, get_rules_index
from mtg_api.rules_index import RulesIndex


def _get(monkeypatch, **settings):
    for name, value in settings.items():
        monkeypatch.setattr(main.settings, name, value)
    app.dependency_overrides[get_rules_index] = lambda: RulesIndex([], "2026-09-28")
    try:
        return TestClient(app).get("/api/v1/meta")
    finally:
        app.dependency_overrides.clear()


def test_meta_reports_the_daily_limit_when_gated(monkeypatch):
    resp = _get(monkeypatch, gating_enabled=True, ip_daily_llm_limit=20, max_query_chars=500)
    assert resp.status_code == 200
    assert resp.json() == {
        "answers_per_day": 20,
        "max_query_chars": 500,
        "rules_as_of": "2026-09-28",
    }


def test_meta_reports_unlimited_when_not_gated(monkeypatch):
    assert _get(monkeypatch, gating_enabled=False).json()["answers_per_day"] is None
