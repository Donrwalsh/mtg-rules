from fastapi.testclient import TestClient

from mtg_api.main import app, get_qdrant_client


class _FakeQdrantOk:
    def get_collections(self):
        return []


class _FakeQdrantDown:
    def get_collections(self):
        raise ConnectionError("qdrant unreachable")


def test_health_reports_ok_when_qdrant_reachable():
    app.dependency_overrides[get_qdrant_client] = lambda: _FakeQdrantOk()
    try:
        resp = TestClient(app).get("/health")
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "qdrant": "ok"}


def test_health_reports_unreachable_when_qdrant_down():
    app.dependency_overrides[get_qdrant_client] = lambda: _FakeQdrantDown()
    try:
        resp = TestClient(app).get("/health")
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "qdrant": "unreachable"}
