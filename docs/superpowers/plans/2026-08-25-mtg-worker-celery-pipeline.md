# mtg-worker Celery Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make ingestion and embedding API-triggerable and asynchronous — `mtg-api` sends a Celery task and returns a task ID immediately; a new `mtg-worker` container (wrapping the existing, unmodified `mtg-ingestion` and `mtg-embed` packages) does the actual work; the caller polls a status endpoint.

**Architecture:** `mtg-ingestion/` and `mtg-embed/` physically relocate under a new top-level `mtg-worker/` package via `git mv` — their internals don't change. A new `mtg_worker` package adds a thin Celery app plus two tasks that call the existing, already-tested `run_all()` / `run()` functions directly. `mtg-api` gains a Celery client (send-only, never imports `mtg_worker`) and three routes. Redis is the broker/backend, added as a new compose service.

**Tech Stack:** Celery 5.3+, redis-py 5.0+, Redis 7 (compose service) · existing FastAPI/pydantic-settings stack in `mtg-api` · existing Typer/pytest stack in `mtg-ingestion`/`mtg-embed` (untouched).

**Spec:** `docs/superpowers/specs/2026-08-25-mtg-worker-celery-pipeline-design.md`

## Global Constraints

- Working branch: `feature/mtg-worker-celery-pipeline` (already checked out, one commit ahead of `main`).
- Celery broker and result backend are both Redis, same URL: `redis://redis:6379/0` in containers.
- `mtg_worker.config.Settings` env prefix `MTG_WORKER_`; `mtg_api.config.Settings` (existing, env prefix `MTG_API_`) gains the same two fields.
- Neither `mtg_ingestion` nor `mtg_embed` source code changes — tasks call `mtg_ingestion.cli.run_all()` and `mtg_embed.cli.run(source="all", limit=limit)` directly, always with explicit keyword arguments (never relying on their `typer.Option(...)` defaults).
- Task names are exactly `"mtg_worker.ingest"` and `"mtg_worker.embed"` — used both in `@celery_app.task(name=...)` and in `mtg-api`'s `client.send_task(...)` calls; a mismatch means the API sends a task the worker never registered.
- `mtg-api` never imports `mtg_worker` — it only needs the same broker URL to send tasks by name and read status by ID, exactly like `mtg_embed` and `mtg_api` already don't share code.
- Every new Python file in `mtg-worker` lives under `mtg-worker/src/mtg_worker/`; tests under `mtg-worker/tests/`.
- `POST /api/v1/embed`'s `limit` field: `"all"` maps to `None`; any other value must parse as a positive `int` or the request is rejected with `400` and detail `'limit must be "all" or a positive integer'`.
- `GET /api/v1/tasks/{task_id}` never calls `.get()` on the Celery result (that blocks) — only `.status`, `.ready()`, and `.result`.

---

### Task 1: Relocate mtg-ingestion/mtg-embed under mtg-worker/, scaffold the new package

**Files:**
- Move: `mtg-ingestion/` → `mtg-worker/mtg-ingestion/` (git mv, whole directory)
- Move: `mtg-embed/` → `mtg-worker/mtg-embed/` (git mv, whole directory)
- Create: `mtg-worker/pyproject.toml`
- Create: `mtg-worker/src/mtg_worker/__init__.py`
- Create: `mtg-worker/src/mtg_worker/config.py`
- Test: `mtg-worker/tests/test_config.py`

**Interfaces:**
- Produces: `mtg_worker.config.Settings` (fields `broker_url: str = "redis://redis:6379/0"`, `result_backend: str = "redis://redis:6379/0"`, env prefix `MTG_WORKER_`) and a module-level `settings = Settings()`.

- [ ] **Step 1: Move the two packages**

```bash
git mv mtg-ingestion mtg-worker/mtg-ingestion
git mv mtg-embed mtg-worker/mtg-embed
```

- [ ] **Step 2: Sanity-check both moved packages' own tests still pass from their new location**

Run:
```bash
cd mtg-worker/mtg-ingestion && PYTHONPATH=src python -m pytest -v
cd ../mtg-embed && PYTHONPATH=src python -m pytest -v
```
Expected: both suites pass exactly as before the move (their internals are untouched — this only confirms the move itself broke nothing).

- [ ] **Step 3: Write the failing config test**

```python
# mtg-worker/tests/test_config.py
from mtg_worker.config import Settings


def test_defaults():
    s = Settings(_env_file=None)
    assert s.broker_url == "redis://redis:6379/0"
    assert s.result_backend == "redis://redis:6379/0"


def test_env_override(monkeypatch):
    monkeypatch.setenv("MTG_WORKER_BROKER_URL", "redis://localhost:6379/0")
    s = Settings(_env_file=None)
    assert s.broker_url == "redis://localhost:6379/0"
```

- [ ] **Step 4: Run test to verify it fails**

Run: `cd mtg-worker && PYTHONPATH=src python -m pytest tests/test_config.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'mtg_worker'`)

- [ ] **Step 5: Write the implementation**

```toml
# mtg-worker/pyproject.toml
[project]
name = "mtg-worker"
version = "0.1.0"
description = "Celery worker running the MTG ingestion and embedding pipelines, triggered from mtg-api."
requires-python = ">=3.12"
dependencies = [
    "celery>=5.3",
    "redis>=5.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "ruff>=0.5",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/mtg_worker"]

[tool.ruff]
line-length = 100
```

```python
# mtg-worker/src/mtg_worker/__init__.py
```

```python
# mtg-worker/src/mtg_worker/config.py
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MTG_WORKER_")

    broker_url: str = "redis://redis:6379/0"
    result_backend: str = "redis://redis:6379/0"


settings = Settings()
```

- [ ] **Step 6: Install and run tests to verify they pass**

Run:
```bash
cd mtg-worker
pip install -e "./mtg-ingestion" -e "./mtg-embed" -e ".[dev]"
PYTHONPATH=src python -m pytest tests/test_config.py -v
```
Expected: PASS (2 tests). (The two local-package installs are needed here because Task 2's tests will import `mtg_ingestion`/`mtg_embed`; installing them now keeps this task self-contained for whoever runs it.)

- [ ] **Step 7: Commit**

```bash
git add mtg-worker/pyproject.toml mtg-worker/src mtg-worker/tests
git commit -m "feat(mtg-worker): relocate mtg-ingestion and mtg-embed, scaffold the worker package"
```

---

### Task 2: Celery app and task definitions

**Files:**
- Create: `mtg-worker/src/mtg_worker/celery_app.py`
- Create: `mtg-worker/src/mtg_worker/tasks.py`
- Test: `mtg-worker/tests/test_tasks.py`

**Interfaces:**
- Consumes: `mtg_worker.config.settings` (Task 1); `mtg_ingestion.cli.run_all() -> None` and `mtg_embed.cli.run(source: str, limit: int | None) -> None` (both pre-existing, unchanged).
- Produces: `mtg_worker.celery_app.celery_app` (a `Celery` instance); `mtg_worker.tasks.ingest_task() -> None` (registered as `"mtg_worker.ingest"`); `mtg_worker.tasks.embed_task(limit: int | None = None) -> None` (registered as `"mtg_worker.embed"`).

- [ ] **Step 1: Write the failing tests**

```python
# mtg-worker/tests/test_tasks.py
from mtg_worker.tasks import embed_task, ingest_task


def test_ingest_task_calls_run_all(monkeypatch):
    calls = []
    monkeypatch.setattr("mtg_ingestion.cli.run_all", lambda: calls.append(True))
    ingest_task()
    assert calls == [True]


def test_embed_task_calls_run_with_source_all_and_given_limit(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "mtg_embed.cli.run", lambda source, limit: calls.append((source, limit))
    )
    embed_task(limit=25)
    assert calls == [("all", 25)]


def test_embed_task_defaults_limit_to_none(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "mtg_embed.cli.run", lambda source, limit: calls.append((source, limit))
    )
    embed_task()
    assert calls == [("all", None)]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd mtg-worker && PYTHONPATH=src python -m pytest tests/test_tasks.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'mtg_worker.tasks'`)

- [ ] **Step 3: Write the implementation**

```python
# mtg-worker/src/mtg_worker/celery_app.py
from __future__ import annotations

from celery import Celery

from mtg_worker.config import settings

celery_app = Celery("mtg_worker", broker=settings.broker_url, backend=settings.result_backend)
```

```python
# mtg-worker/src/mtg_worker/tasks.py
from __future__ import annotations

from mtg_worker.celery_app import celery_app


@celery_app.task(name="mtg_worker.ingest")
def ingest_task() -> None:
    from mtg_ingestion.cli import run_all

    run_all()


@celery_app.task(name="mtg_worker.embed")
def embed_task(limit: int | None = None) -> None:
    from mtg_embed.cli import run as embed_run

    embed_run(source="all", limit=limit)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd mtg-worker && PYTHONPATH=src python -m pytest tests/ -v`
Expected: PASS (5 tests total)

- [ ] **Step 5: Commit**

```bash
git add mtg-worker/src/mtg_worker/celery_app.py mtg-worker/src/mtg_worker/tasks.py mtg-worker/tests/test_tasks.py
git commit -m "feat(mtg-worker): add Celery app and ingest/embed task definitions"
```

---

### Task 3: mtg-worker Dockerfile

**Files:**
- Create: `mtg-worker/Dockerfile`

**Interfaces:**
- Consumes: `mtg-worker/pyproject.toml` (Task 1), `mtg-worker/mtg-ingestion/` and `mtg-worker/mtg-embed/` (Task 1), `mtg_worker.celery_app:celery_app` (Task 2).
- Produces: an image whose default command starts the Celery worker.

- [ ] **Step 1: Write the Dockerfile**

```dockerfile
# mtg-worker/Dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY mtg-ingestion ./mtg-ingestion
COPY mtg-embed ./mtg-embed
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir ./mtg-ingestion ./mtg-embed .
CMD ["celery", "-A", "mtg_worker.celery_app", "worker", "--loglevel=info"]
```

- [ ] **Step 2: Build the image and confirm the Celery app loads (no Redis needed for this check)**

Run:
```bash
cd mtg-worker
docker build -t mtg-worker:dev .
docker run --rm mtg-worker:dev celery -A mtg_worker.celery_app status --timeout 1
```
Expected: the command runs and prints a Celery connection error (no broker reachable standalone) rather than an import error — that's enough to prove the app/task modules import cleanly inside the image. An `ImportError`/`ModuleNotFoundError` here means the Dockerfile's installs are wrong; a connection/timeout error is the expected outcome with no Redis running.

- [ ] **Step 3: Commit**

```bash
git add mtg-worker/Dockerfile
git commit -m "feat(mtg-worker): add Dockerfile running the Celery worker"
```

---

### Task 4: mtg-api Celery client + config

**Files:**
- Modify: `mtg-api/pyproject.toml`
- Modify: `mtg-api/src/mtg_api/config.py`
- Create: `mtg-api/src/mtg_api/celery_client.py`
- Modify: `mtg-api/tests/test_config.py`
- Test: `mtg-api/tests/test_celery_client.py`

**Interfaces:**
- Consumes: nothing new from earlier tasks in this plan (this task only touches `mtg-api`, which is independent of `mtg-worker`).
- Produces: `mtg_api.config.Settings` gains `broker_url: str = "redis://redis:6379/0"`, `result_backend: str = "redis://redis:6379/0"`; `mtg_api.celery_client.get_celery_client() -> Celery`.

- [ ] **Step 1: Write the failing tests**

Add to `mtg-api/tests/test_config.py` (append, don't replace the existing two tests):

```python
def test_broker_defaults():
    s = Settings(_env_file=None)
    assert s.broker_url == "redis://redis:6379/0"
    assert s.result_backend == "redis://redis:6379/0"


def test_broker_env_override(monkeypatch):
    monkeypatch.setenv("MTG_API_BROKER_URL", "redis://localhost:6379/0")
    s = Settings(_env_file=None)
    assert s.broker_url == "redis://localhost:6379/0"
```

```python
# mtg-api/tests/test_celery_client.py
from mtg_api.celery_client import get_celery_client


def test_get_celery_client_returns_configured_instance():
    client = get_celery_client()
    assert client.conf.broker_url == "redis://redis:6379/0"
    assert client.conf.result_backend == "redis://redis:6379/0"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd mtg-api && PYTHONPATH=src python -m pytest tests/test_config.py tests/test_celery_client.py -v`
Expected: `test_broker_defaults`/`test_broker_env_override` FAIL with `AttributeError` (no `broker_url` field yet); `test_celery_client.py` FAILs to collect (`ModuleNotFoundError: No module named 'mtg_api.celery_client'`)

- [ ] **Step 3: Write the implementation**

Add `"celery>=5.3"` and `"redis>=5.0"` to `mtg-api/pyproject.toml`'s `dependencies` list (alongside the existing `fastapi`, `uvicorn[standard]`, `pydantic-settings`, `qdrant-client` entries).

Modify `mtg-api/src/mtg_api/config.py` — add two fields to the existing `Settings` class, after `cors_origins`:

```python
    broker_url: str = "redis://redis:6379/0"
    result_backend: str = "redis://redis:6379/0"
```

```python
# mtg-api/src/mtg_api/celery_client.py
from __future__ import annotations

from celery import Celery

from mtg_api.config import settings


def get_celery_client() -> Celery:
    return Celery("mtg_worker", broker=settings.broker_url, backend=settings.result_backend)
```

- [ ] **Step 4: Reinstall (new dependency) and run tests to verify they pass**

Run:
```bash
cd mtg-api
pip install -e ".[dev]"
python -m pytest tests/test_config.py tests/test_celery_client.py -v
```
Expected: PASS (6 tests: the existing 2 plus 4 new)

- [ ] **Step 5: Commit**

```bash
git add mtg-api/pyproject.toml mtg-api/src/mtg_api/config.py mtg-api/src/mtg_api/celery_client.py mtg-api/tests/test_config.py mtg-api/tests/test_celery_client.py
git commit -m "feat(mtg-api): add Celery client and broker settings"
```

---

### Task 5: `POST /api/v1/ingest`

**Files:**
- Modify: `mtg-api/src/mtg_api/main.py`
- Test: `mtg-api/tests/test_ingest.py`

**Interfaces:**
- Consumes: `mtg_api.celery_client.get_celery_client` (Task 4).
- Produces: `POST /api/v1/ingest` route on `app`, sending task name `"mtg_worker.ingest"`.

- [ ] **Step 1: Write the failing test**

```python
# mtg-api/tests/test_ingest.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd mtg-api && python -m pytest tests/test_ingest.py -v`
Expected: FAIL (`ImportError: cannot import name 'get_celery_client' from 'mtg_api.main'` — it's only defined in `celery_client.py` so far, not yet imported/exposed from `main.py`)

- [ ] **Step 3: Write the implementation**

Add to the imports at the top of `mtg-api/src/mtg_api/main.py`:

```python
from celery import Celery

from mtg_api.celery_client import get_celery_client
```

Add the route (anywhere after the existing `/health` route):

```python
@app.post("/api/v1/ingest")
def trigger_ingest(client: Celery = Depends(get_celery_client)) -> dict:
    result = client.send_task("mtg_worker.ingest")
    return {"task_id": result.id}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd mtg-api && python -m pytest tests/ -v`
Expected: PASS (all tests so far)

- [ ] **Step 5: Commit**

```bash
git add mtg-api/src/mtg_api/main.py mtg-api/tests/test_ingest.py
git commit -m "feat(mtg-api): add POST /api/v1/ingest"
```

---

### Task 6: `POST /api/v1/embed`

**Files:**
- Modify: `mtg-api/src/mtg_api/models.py`
- Modify: `mtg-api/src/mtg_api/main.py`
- Test: `mtg-api/tests/test_embed.py`

**Interfaces:**
- Consumes: `mtg_api.celery_client.get_celery_client` (Task 4).
- Produces: `mtg_api.models.EmbedRequest(limit: str = "all")`; `POST /api/v1/embed` route on `app`, sending task name `"mtg_worker.embed"` with `kwargs={"limit": int | None}`.

- [ ] **Step 1: Write the failing tests**

```python
# mtg-api/tests/test_embed.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd mtg-api && python -m pytest tests/test_embed.py -v`
Expected: FAIL (404 on `/api/v1/embed` — route doesn't exist yet)

- [ ] **Step 3: Write the implementation**

Add to `mtg-api/src/mtg_api/models.py` (append):

```python
class EmbedRequest(BaseModel):
    limit: str = "all"
```

Add to the imports at the top of `mtg-api/src/mtg_api/main.py`:

```python
from fastapi import HTTPException

from mtg_api.models import EmbedRequest
```

(Merge this into the existing `from mtg_api.models import QueryRequest, QueryResponse, QueryResult` line rather than adding a second import line for the same module — the final line reads `from mtg_api.models import EmbedRequest, QueryRequest, QueryResponse, QueryResult`. Similarly merge the `HTTPException` import into the existing `from fastapi import Depends, FastAPI` line.)

Add the route:

```python
@app.post("/api/v1/embed")
def trigger_embed(request: EmbedRequest, client: Celery = Depends(get_celery_client)) -> dict:
    if request.limit == "all":
        limit = None
    else:
        try:
            limit = int(request.limit)
        except ValueError:
            raise HTTPException(status_code=400, detail='limit must be "all" or a positive integer')
        if limit <= 0:
            raise HTTPException(status_code=400, detail='limit must be "all" or a positive integer')
    result = client.send_task("mtg_worker.embed", kwargs={"limit": limit})
    return {"task_id": result.id}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd mtg-api && python -m pytest tests/ -v`
Expected: PASS (all tests so far)

- [ ] **Step 5: Commit**

```bash
git add mtg-api/src/mtg_api/models.py mtg-api/src/mtg_api/main.py mtg-api/tests/test_embed.py
git commit -m "feat(mtg-api): add POST /api/v1/embed"
```

---

### Task 7: `GET /api/v1/tasks/{task_id}`

**Files:**
- Modify: `mtg-api/src/mtg_api/main.py`
- Test: `mtg-api/tests/test_task_status.py`

**Interfaces:**
- Consumes: `mtg_api.celery_client.get_celery_client` (Task 4).
- Produces: `GET /api/v1/tasks/{task_id}` route on `app`.

- [ ] **Step 1: Write the failing test**

```python
# mtg-api/tests/test_task_status.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd mtg-api && python -m pytest tests/test_task_status.py -v`
Expected: FAIL (404 on `/api/v1/tasks/abc-123` — route doesn't exist yet)

- [ ] **Step 3: Write the implementation**

Add to `mtg-api/src/mtg_api/main.py`:

```python
@app.get("/api/v1/tasks/{task_id}")
def get_task_status(task_id: str, client: Celery = Depends(get_celery_client)) -> dict:
    result = client.AsyncResult(task_id)
    return {
        "task_id": task_id,
        "status": result.status,
        "result": result.result if result.ready() else None,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd mtg-api && python -m pytest tests/ -v`
Expected: PASS (all tests so far)

- [ ] **Step 5: Commit**

```bash
git add mtg-api/src/mtg_api/main.py mtg-api/tests/test_task_status.py
git commit -m "feat(mtg-api): add GET /api/v1/tasks/{task_id}"
```

---

### Task 8: Compose wiring — redis, worker, backend env, retire stale services, verify end to end

**Files:**
- Modify: `docker-compose.yml` (repo root)
- Delete: `mtg-worker/mtg-ingestion/docker-compose.yml`

**Interfaces:**
- Consumes: `mtg-worker/Dockerfile` (Task 3), `mtg_worker.tasks` task names (Task 2), `mtg-api`'s `MTG_API_BROKER_URL`/`MTG_API_RESULT_BACKEND` (Task 4), the three new routes (Tasks 5-7).
- Produces: `docker compose up --build` bringing up `qdrant`, `redis`, `worker`, `backend`, `frontend` together; `POST /api/v1/ingest` and `POST /api/v1/embed` do real work end to end.

- [ ] **Step 1: Delete the now-redundant `mtg-ingestion` compose file**

```bash
git rm mtg-worker/mtg-ingestion/docker-compose.yml
```

Its role (`docker compose run --rm ingestion run-all`) is superseded by `POST /api/v1/ingest`.

- [ ] **Step 2: Rewrite the root `docker-compose.yml`**

The current file has `qdrant`, `embed`, `backend`, `frontend` services. Its `embed` service (`build: ./mtg-embed`) is both functionally superseded (embedding now runs via `worker` + `POST /api/v1/embed`) and structurally broken (that path no longer exists after Task 1's move) — remove it entirely. Add `redis` and `worker`; add broker env vars and a `redis` dependency to `backend`. Replace the whole file with:

```yaml
# docker-compose.yml
services:
  qdrant:
    image: qdrant/qdrant:latest
    ports:
      - "6333:6333"
    volumes:
      - qdrant_storage:/qdrant/storage

  redis:
    image: redis:7-alpine

  worker:
    build: ./mtg-worker
    depends_on:
      - redis
      - qdrant
    environment:
      MTG_WORKER_BROKER_URL: redis://redis:6379/0
      MTG_WORKER_RESULT_BACKEND: redis://redis:6379/0
      MTG_INGEST_DATA_DIR: /app/data
      MTG_EMBED_PARSED_DIR: /app/data/parsed
      MTG_EMBED_QDRANT_HOST: qdrant
      MTG_EMBED_QDRANT_PORT: "6333"
    volumes:
      - ./mtg-worker/mtg-ingestion/data:/app/data

  backend:
    build: ./mtg-api
    environment:
      MTG_API_QDRANT_HOST: qdrant
      MTG_API_QDRANT_PORT: "6333"
      MTG_API_CORS_ORIGINS: '["http://localhost:3000"]'
      MTG_API_BROKER_URL: redis://redis:6379/0
      MTG_API_RESULT_BACKEND: redis://redis:6379/0
    ports:
      - "8000:8000"
    depends_on:
      - qdrant
      - redis

  frontend:
    build:
      context: ./mtg-web
      args:
        PUBLIC_API_URL: ${PUBLIC_API_URL:-http://localhost:8000}
    ports:
      - "3000:80"
    depends_on:
      - backend

volumes:
  qdrant_storage:
```

- [ ] **Step 3: Validate the compose file parses**

Run: `docker compose config --quiet`
Expected: no output, exit code 0.

- [ ] **Step 4: Bring up the stack and run a real ingest end to end**

Run:
```bash
docker compose up --build -d qdrant redis worker backend
sleep 5
curl -s -X POST localhost:8000/api/v1/ingest
```
Expected: `{"task_id": "<some-uuid>"}`. Note the task ID, then poll:
```bash
curl -s localhost:8000/api/v1/tasks/<task-id>
```
repeating every ~10s until `"status": "SUCCESS"`. This is a real fetch-and-parse run against the live Comprehensive Rules and Scryfall endpoints — it can take a couple of minutes. Confirm fresh files landed on the host:
```bash
ls -la mtg-worker/mtg-ingestion/data/parsed
```

- [ ] **Step 5: Run a real embed end to end, capped small**

```bash
curl -s -X POST localhost:8000/api/v1/embed -H "Content-Type: application/json" -d '{"limit": "10"}'
```
Expected: `{"task_id": "<some-uuid>"}`. Poll `GET /api/v1/tasks/<task-id>` the same way until `"status": "SUCCESS"`. This is a real model-download-and-embed run against the real Qdrant service — the first run downloads `BAAI/bge-base-en-v1.5`, which can take a few minutes.

- [ ] **Step 6: Tear down**

```bash
docker compose down
```

- [ ] **Step 7: Commit**

```bash
git add docker-compose.yml
git commit -m "feat(mtg-app): wire redis and the Celery worker into the root compose file"
```
