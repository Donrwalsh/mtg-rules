import pytest
from fastapi.testclient import TestClient

from mtg_api import main
from mtg_api.main import app, get_celery_client


class _ExplodingCeleryClient:
    """Any use means a disabled endpoint still reached Celery."""

    def send_task(self, *args, **kwargs):
        raise AssertionError("send_task called")

    def AsyncResult(self, *args, **kwargs):
        raise AssertionError("AsyncResult called")


@pytest.fixture
def task_endpoints_off(monkeypatch):
    monkeypatch.setattr(main.settings, "task_endpoints", False)
    app.dependency_overrides[get_celery_client] = lambda: _ExplodingCeleryClient()
    yield
    app.dependency_overrides.clear()


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("post", "/api/v1/ingest", None),
        ("post", "/api/v1/embed", {"limit": "all"}),
        ("get", "/api/v1/tasks/abc-123", None),
    ],
)
def test_task_endpoints_are_404_when_disabled(task_endpoints_off, method, path, body):
    resp = TestClient(app).request(method, path, json=body)
    assert resp.status_code == 404
