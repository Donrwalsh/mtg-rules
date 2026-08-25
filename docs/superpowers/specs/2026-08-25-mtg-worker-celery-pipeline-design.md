# mtg-worker: Celery-driven ingestion + embedding, triggered from the API

Status: approved, ready for implementation planning
Date: 2026-08-25

## Purpose

Today, refreshing the corpus means someone manually running
`mtg-ingest run-all` and then `mtg-embed run` (or the old ad-hoc compose
services) from a terminal. This stage makes both operations
API-triggerable and asynchronous: `mtg-api` kicks off a task and returns
immediately with a task ID; a separate Celery worker container actually
does the (slow, model-loading, network-fetching) work; the caller polls a
status endpoint. No new business logic — this wires existing, already-working
code behind a queue.

## Package layout

`mtg-ingestion/` and `mtg-embed/` physically relocate under a new
top-level `mtg-worker/` package, git-moved wholesale — their internals
(`src/`, `tests/`, `pyproject.toml`, `Dockerfile`) are untouched by the
move itself, only their location changes:

```
mtg-worker/
  mtg-ingestion/        # git mv of the old top-level mtg-ingestion/, unchanged internally
  mtg-embed/             # git mv of the old top-level mtg-embed/, unchanged internally
  src/mtg_worker/
    __init__.py
    config.py             # Settings: broker_url, result_backend
    celery_app.py          # the Celery() instance both tasks register against
    tasks.py                # ingest_task, embed_task(limit)
  tests/
  pyproject.toml
  Dockerfile              # installs all three local packages, runs the Celery worker
```

Both moved packages keep their own `Dockerfile`s as optional standalone
manual-run tools (unreferenced by root compose after this change — see
"Retired compose services" below). Neither package's source code changes.

## Reusing existing code, unmodified

Both packages already expose their orchestration as plain functions
underneath a Typer decorator — the decorator doesn't prevent calling the
function directly, it just also registers it as a CLI command. Calling
with explicit keyword arguments sidesteps the one trap (Typer's
`typer.Option(...)` sentinel objects as parameter defaults, which only
resolve to real values when Typer's own CLI parser runs):

- `mtg_ingestion.cli.run_all()` — no parameters, safe to call directly
  as-is.
- `mtg_embed.cli.run(source="all", limit=limit)` — called with both
  keyword arguments always explicit, so the `typer.Option(...)` defaults
  are never touched.

Neither package needs a single code change for this stage.

## `mtg-worker` internals

`mtg_worker.config.Settings` (pydantic-settings, env prefix
`MTG_WORKER_`): `broker_url: str = "redis://redis:6379/0"`,
`result_backend: str = "redis://redis:6379/0"`.

`mtg_worker.celery_app`: `celery_app = Celery("mtg_worker",
broker=settings.broker_url, backend=settings.result_backend)`.

`mtg_worker.tasks`:
- `@celery_app.task(name="mtg_worker.ingest") def ingest_task() -> None`
  — imports and calls `mtg_ingestion.cli.run_all()` (import kept inside
  the task body, matching the lazy-heavy-import convention already used
  in `mtg_embed.cli.run`).
- `@celery_app.task(name="mtg_worker.embed") def embed_task(limit: int
  | None = None) -> None` — imports and calls `mtg_embed.cli.run(source="all",
  limit=limit)`.

Both tasks return `None` on success — their existing `typer.echo(...)`
calls become worker log lines (harmless outside a Click context; `echo`
just writes to stdout). No structured result payload is captured from
either function in this stage; the task status endpoint reports Celery's
own state (`PENDING`/`STARTED`/`SUCCESS`/`FAILURE`), which is enough to
know a run finished — richer per-run summaries are a future refinement,
not required now.

## `mtg-api` additions

`mtg_api.config.Settings` gains `broker_url: str =
"redis://redis:6379/0"` and `result_backend: str =
"redis://redis:6379/0"` (env `MTG_API_BROKER_URL` /
`MTG_API_RESULT_BACKEND`) — the API only ever needs the same broker
config to *send* tasks and read their status; it never imports
`mtg_worker` itself, keeping the packages decoupled the same way
`mtg-api` and `mtg-embed` already are.

`mtg_api.celery_client.get_celery_client() -> Celery` — a FastAPI
dependency returning `Celery("mtg_worker", broker=settings.broker_url,
backend=settings.result_backend)`, overridable in tests exactly like the
existing `get_qdrant_client` dependency (a fake with `.send_task(name,
kwargs=None) -> object-with-.id` and `.AsyncResult(task_id) ->
object-with-.status/.result`).

`mtg_api.models` gains `EmbedRequest(limit: str = "all")`.

Three new routes on `mtg_api.main.app`:
- `POST /api/v1/ingest` → `client.send_task("mtg_worker.ingest")` →
  `{"task_id": result.id}`.
- `POST /api/v1/embed` (body: `EmbedRequest`) → parse `limit`: `"all"`
  becomes `None`, anything else must parse as a positive `int` or the
  request is rejected with `400` (`"limit must be \"all\" or a positive
  integer"`); then `client.send_task("mtg_worker.embed",
  kwargs={"limit": parsed_limit})` → `{"task_id": result.id}`.
- `GET /api/v1/tasks/{task_id}` → `client.AsyncResult(task_id)` →
  `{"task_id": task_id, "status": result.status, "result": result.result
  if result.ready() else None}`. Never calls `.get()` — that blocks; both
  `.status` and `.result` read the backend without waiting.

## Compose changes

Add `redis` service (`redis:7-alpine`, no host port published — only
`worker` and `backend` need it, both inside the compose network).

Add `worker` service: `build: ./mtg-worker`, `depends_on: [redis,
qdrant]`, environment `MTG_WORKER_BROKER_URL=redis://redis:6379/0`,
`MTG_WORKER_RESULT_BACKEND=redis://redis:6379/0`,
`MTG_INGEST_DATA_DIR=/app/data`, `MTG_EMBED_PARSED_DIR=/app/data/parsed`,
`MTG_EMBED_QDRANT_HOST=qdrant`, `MTG_EMBED_QDRANT_PORT=6333`; volume
`./mtg-worker/mtg-ingestion/data:/app/data` (read-write — ingestion
writes here, embedding reads from the same path, both inside the same
container now, no more cross-package bind mount).

`backend` service gains `MTG_API_BROKER_URL`/`MTG_API_RESULT_BACKEND`
env vars and `redis` added to `depends_on`.

**Retired compose services:** `mtg-ingestion/docker-compose.yml` is
deleted (its role — `docker compose run --rm ingestion run-all` — is now
reachable via `POST /api/v1/ingest`). This mirrors the earlier removal of
`mtg-embed/docker-compose.yml` when it folded into the root file. Both
packages' own `Dockerfile`s remain, just no longer wired into any
compose file — still usable standalone (`docker build` + `docker run`)
if someone wants to bypass Celery entirely.

## Testing plan

- `mtg-worker/tests/test_tasks.py`: monkeypatch
  `mtg_ingestion.cli.run_all` / `mtg_embed.cli.run` to fakes, invoke each
  task function directly (Celery tasks are plain callables outside a
  worker context — `.run()` or calling them directly both work), assert
  the fake was called with the right arguments (`embed_task(limit=50)`
  calls the fake with `source="all", limit=50`).
- `mtg-api/tests/test_ingest.py`, `test_embed.py`, `test_tasks_status.py`:
  DI-override `get_celery_client` with a fake, same pattern as
  `test_health.py`'s `get_qdrant_client` override. Cover: ingest returns
  a task ID; embed with `"all"` sends `limit=None`; embed with `"25"`
  sends `limit=25`; embed with `"not-a-number"` returns `400`; task
  status reflects the fake's `.status`/`.result`.
- No real Redis/Celery worker required for any unit test.
- One end-to-end compose smoke check closes out implementation: bring up
  `redis`, `qdrant`, `worker`, `backend`; `POST /api/v1/ingest`; poll
  `GET /api/v1/tasks/{id}` until `SUCCESS`; confirm `mtg-worker/mtg-ingestion/data/`
  has fresh files on the host. Embedding's full-corpus run is *not*
  triggered automatically in this check (it's slow/model-downloading) —
  a `POST /api/v1/embed` with a small `limit` is enough to prove the
  wiring.

## Out of scope

- No retry/backoff policy beyond Celery's defaults.
- No auth on the trigger endpoints.
- No per-source-type granularity — ingestion always runs all three
  sources; embedding always embeds all three types, only the row
  `limit` is parameterized (matches the explicit simplification this
  stage asked for).
- No richer task result payload (row counts, per-source summary) beyond
  Celery's own status — `mtg_embed.cli.run`'s already-existing
  `typer.echo` summary output stays in the worker's logs only.
- No Flower or other Celery monitoring UI.

## Dependencies

`mtg-worker/pyproject.toml`: `celery>=5.3`, `redis>=5.0`; dev:
`pytest>=8.0`, `ruff>=0.5`. `mtg-api/pyproject.toml` gains `celery>=5.3`
(client-side `send_task`/`AsyncResult` only, no worker) and `redis>=5.0`.
