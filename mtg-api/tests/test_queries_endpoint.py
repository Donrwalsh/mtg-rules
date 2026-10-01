from conftest import admin_client, memory_engine

from mtg_api.history import save_history
from mtg_api.main import app, get_db_engine


def test_returns_empty_list_when_no_history(monkeypatch):
    engine = memory_engine()
    app.dependency_overrides[get_db_engine] = lambda: engine
    try:
        resp = admin_client(monkeypatch).get("/api/v1/queries")
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 200
    assert resp.json() == []


def test_returns_saved_rows_newest_first(monkeypatch):
    engine = memory_engine()
    save_history(engine, query="first", answer="a1", results=[], model="m", error=None)
    save_history(engine, query="second", answer="a2", results=[], model="m", error=None)
    app.dependency_overrides[get_db_engine] = lambda: engine
    try:
        resp = admin_client(monkeypatch).get("/api/v1/queries")
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 200
    body = resp.json()
    assert [row["query"] for row in body] == ["second", "first"]


def test_respects_limit_and_offset_query_params(monkeypatch):
    engine = memory_engine()
    for i in range(3):
        save_history(engine, query=f"q{i}", answer=None, results=[], model="m", error=None)
    app.dependency_overrides[get_db_engine] = lambda: engine
    try:
        resp = admin_client(monkeypatch).get("/api/v1/queries?limit=1&offset=1")
    finally:
        app.dependency_overrides.clear()
    body = resp.json()
    assert len(body) == 1
    assert body[0]["query"] == "q1"


def test_returns_citation_fields(monkeypatch):
    engine = memory_engine()
    save_history(
        engine,
        query="q",
        answer="Yes [1].",
        results=[],
        model="m",
        error=None,
        citations=[{"number": 1, "title": "Rule 702.11b", "url": "/rules/702.11b"}],
        citation_stats={"cited_count": 1, "invalid_count": 0, "uncited_answer": False},
        rule_references=["702.11b"],
    )
    app.dependency_overrides[get_db_engine] = lambda: engine
    try:
        body = admin_client(monkeypatch).get("/api/v1/queries").json()
    finally:
        app.dependency_overrides.clear()
    assert body[0]["citations"][0]["url"] == "/rules/702.11b"
    assert body[0]["citation_stats"]["cited_count"] == 1
    assert body[0]["rule_references"] == ["702.11b"]


def _replay(monkeypatch, engine, history_id, *, admin=True):
    from fastapi.testclient import TestClient

    app.dependency_overrides[get_db_engine] = lambda: engine
    try:
        client = admin_client(monkeypatch) if admin else TestClient(app)
        return client.get(f"/api/v1/queries/{history_id}")
    finally:
        app.dependency_overrides.clear()


def test_replay_returns_answered_row_as_query_response(monkeypatch):
    engine = memory_engine()
    result = {
        "source": "rule",
        "title": "702.2c",
        "text": "Deathtouch text",
        "score": 1.0,
        "match_type": "keyword_rule",
        "rule_id": "702.2c",
        "cited": True,
    }
    citation = {
        "number": 1,
        "source_type": "rule",
        "title": "Rule 702.2c",
        "rule_id": "702.2c",
        "text": "Deathtouch text",
        "url": "/rules/702.2c",
    }
    save_history(
        engine,
        query="trample and deathtouch?",
        answer="One damage is lethal [1].",
        results=[result],
        model="m",
        error=None,
        citations=[citation],
        citation_stats={"cited_count": 1, "invalid_count": 0, "uncited_answer": False},
        rule_references=["702.2c"],
        cached=True,
    )
    resp = _replay(monkeypatch, engine, 1)
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == 1
    assert body["created_at"]
    assert body["query"] == "trample and deathtouch?"
    assert body["answer"] == "One damage is lethal [1]."
    assert body["results"][0]["rule_id"] == "702.2c"
    assert body["citations"][0]["url"] == "/rules/702.2c"
    assert body["rule_references"] == ["702.2c"]
    assert body["citation_stats"]["cited_count"] == 1
    assert body["cached_at"] is None
    assert body["degraded"] is None
    assert body["answers_remaining"] is None


def test_replay_treats_missing_citation_fields_as_empty(monkeypatch):
    engine = memory_engine()
    save_history(engine, query="old", answer="An old answer.", results=[], model="m", error=None)
    resp = _replay(monkeypatch, engine, 1)
    assert resp.status_code == 200
    body = resp.json()
    assert body["citations"] == []
    assert body["rule_references"] == []
    assert body["citation_stats"] == {
        "cited_count": 0,
        "invalid_count": 0,
        "uncited_answer": False,
    }


def test_replay_404s_for_row_without_answer(monkeypatch):
    engine = memory_engine()
    save_history(engine, query="q", answer=None, results=[], model="m", error="boom")
    assert _replay(monkeypatch, engine, 1).status_code == 404


def test_replay_404s_for_unknown_id(monkeypatch):
    assert _replay(monkeypatch, memory_engine(), 99).status_code == 404


def test_replay_requires_admin(monkeypatch):
    engine = memory_engine()
    save_history(engine, query="q", answer="a", results=[], model="m", error=None)
    assert _replay(monkeypatch, engine, 1, admin=False).status_code in (401, 403)


def test_replay_does_not_write_history(monkeypatch):
    from mtg_api.history import list_history

    engine = memory_engine()
    save_history(engine, query="q", answer="a", results=[], model="m", error=None)
    _replay(monkeypatch, engine, 1)
    assert len(list_history(engine)) == 1


def test_replay_marks_a_cut_off_answer_incomplete(monkeypatch):
    engine = memory_engine()
    save_history(
        engine,
        query="q",
        answer="Half an",
        results=[],
        model="m",
        error="answer cut off (finish reason: MAX_TOKENS)",
    )
    save_history(engine, query="q2", answer="Whole.", results=[], model="m", error=None)
    assert _replay(monkeypatch, engine, 1).json()["answer_complete"] is False
    assert _replay(monkeypatch, engine, 2).json()["answer_complete"] is True
