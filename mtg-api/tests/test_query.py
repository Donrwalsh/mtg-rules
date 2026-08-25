from fastapi.testclient import TestClient

from mtg_api.main import app


def test_query_echoes_query_and_returns_dummy_results():
    resp = TestClient(app).post("/api/v1/query", json={"query": "how does trample work"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["query"] == "how does trample work"
    assert len(body["results"]) >= 1
    assert set(body["results"][0].keys()) == {"source", "title", "text", "score"}


def test_query_rejects_missing_query_field():
    resp = TestClient(app).post("/api/v1/query", json={})
    assert resp.status_code == 422


def test_cors_header_present_for_configured_origin():
    resp = TestClient(app).post(
        "/api/v1/query",
        json={"query": "trample"},
        headers={"Origin": "http://localhost:3000"},
    )
    assert resp.headers["access-control-allow-origin"] == "http://localhost:3000"
