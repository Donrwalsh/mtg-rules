from fastapi.testclient import TestClient

from mtg_api.main import app, get_celery_client


class _FakeResult:
    def __init__(self, status, result=None, ready=True):
        self.status = status
        self.result = result
        self._ready = ready

    def ready(self):
        return self._ready


class _FakeCeleryClient:
    def __init__(self, result):
        self._result = result

    def AsyncResult(self, task_id):
        return self._result


def test_status_reports_success_with_no_result_payload():
    fake = _FakeCeleryClient(_FakeResult(status="SUCCESS", result=None, ready=True))
    app.dependency_overrides[get_celery_client] = lambda: fake
    try:
        resp = TestClient(app).get("/api/v1/tasks/abc-123")
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 200
    assert resp.json() == {"task_id": "abc-123", "status": "SUCCESS", "result": None}


def test_status_reports_pending_without_reading_result():
    fake = _FakeCeleryClient(_FakeResult(status="PENDING", result=None, ready=False))
    app.dependency_overrides[get_celery_client] = lambda: fake
    try:
        resp = TestClient(app).get("/api/v1/tasks/abc-123")
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 200
    assert resp.json() == {"task_id": "abc-123", "status": "PENDING", "result": None}
