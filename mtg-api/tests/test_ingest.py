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


def test_ingest_triggers_task_and_returns_task_id():
    fake = _FakeCeleryClient()
    app.dependency_overrides[get_celery_client] = lambda: fake
    try:
        resp = TestClient(app).post("/api/v1/ingest")
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 200
    assert resp.json() == {"task_id": "fake-task-id"}
    assert fake.sent == [("mtg_worker.ingest", None)]
