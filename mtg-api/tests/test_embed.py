from fastapi.testclient import TestClient

from mtg_api.main import app, get_celery_client


class _FakeAsyncResult:
    def __init__(self, task_id):
        self.id = task_id


class _FakeCeleryClient:
    def __init__(self):
        self.sent = []

    def send_task(self, name, kwargs=None):
        self.sent.append((name, kwargs))
        return _FakeAsyncResult("fake-task-id")


def _override(fake):
    app.dependency_overrides[get_celery_client] = lambda: fake


def test_embed_with_all_sends_limit_none():
    fake = _FakeCeleryClient()
    _override(fake)
    try:
        resp = TestClient(app).post("/api/v1/embed", json={"limit": "all"})
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 200
    assert resp.json() == {"task_id": "fake-task-id"}
    assert fake.sent == [("mtg_worker.embed", {"limit": None})]


def test_embed_defaults_limit_to_all_when_omitted():
    fake = _FakeCeleryClient()
    _override(fake)
    try:
        resp = TestClient(app).post("/api/v1/embed", json={})
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 200
    assert fake.sent == [("mtg_worker.embed", {"limit": None})]


def test_embed_with_numeric_string_sends_parsed_int():
    fake = _FakeCeleryClient()
    _override(fake)
    try:
        resp = TestClient(app).post("/api/v1/embed", json={"limit": "25"})
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 200
    assert fake.sent == [("mtg_worker.embed", {"limit": 25})]


def test_embed_with_non_numeric_limit_returns_400():
    fake = _FakeCeleryClient()
    _override(fake)
    try:
        resp = TestClient(app).post("/api/v1/embed", json={"limit": "not-a-number"})
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 400
    assert fake.sent == []


def test_embed_with_zero_limit_returns_400():
    fake = _FakeCeleryClient()
    _override(fake)
    try:
        resp = TestClient(app).post("/api/v1/embed", json={"limit": "0"})
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 400
    assert fake.sent == []
