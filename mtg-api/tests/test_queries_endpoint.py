from fastapi.testclient import TestClient

from conftest import memory_engine
from mtg_api.history import save_history
from mtg_api.main import app, get_db_engine


def test_returns_empty_list_when_no_history():
    engine = memory_engine()
    app.dependency_overrides[get_db_engine] = lambda: engine
    try:
        resp = TestClient(app).get("/api/v1/queries")
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 200
    assert resp.json() == []


def test_returns_saved_rows_newest_first():
    engine = memory_engine()
    save_history(engine, query="first", answer="a1", results=[], model="m", error=None)
    save_history(engine, query="second", answer="a2", results=[], model="m", error=None)
    app.dependency_overrides[get_db_engine] = lambda: engine
    try:
        resp = TestClient(app).get("/api/v1/queries")
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 200
    body = resp.json()
    assert [row["query"] for row in body] == ["second", "first"]


def test_respects_limit_and_offset_query_params():
    engine = memory_engine()
    for i in range(3):
        save_history(engine, query=f"q{i}", answer=None, results=[], model="m", error=None)
    app.dependency_overrides[get_db_engine] = lambda: engine
    try:
        resp = TestClient(app).get("/api/v1/queries?limit=1&offset=1")
    finally:
        app.dependency_overrides.clear()
    body = resp.json()
    assert len(body) == 1
    assert body[0]["query"] == "q1"


def test_returns_citation_fields():
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
        body = TestClient(app).get("/api/v1/queries").json()
    finally:
        app.dependency_overrides.clear()
    assert body[0]["citations"][0]["url"] == "/rules/702.11b"
    assert body[0]["citation_stats"]["cited_count"] == 1
    assert body[0]["rule_references"] == ["702.11b"]
