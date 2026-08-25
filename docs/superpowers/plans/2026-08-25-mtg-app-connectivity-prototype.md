# mtg-app connectivity prototype Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up a minimal FastAPI backend + SvelteKit SPA + Qdrant, wired together under one repo-root `docker-compose.yml` that also folds in `mtg-embed`'s existing `qdrant`/`embed` services (sharing one `qdrant_storage` volume), to prove the container topology works before any real RAG logic exists.

**Architecture:** Two new sibling packages, `mtg-api/` and `mtg-web/`, each with its own multi-stage Dockerfile. Backend exposes `/health` (real Qdrant connectivity check) and `/api/v1/query` (static dummy JSON, no search). Frontend is a single-page SvelteKit app built as static files (`adapter-static`) and served by nginx. One `docker-compose.yml` at the repo root ties `qdrant`, `embed` (relocated from `mtg-embed/docker-compose.yml`), `backend`, and `frontend` together on one volume/network.

**Tech Stack:** Python 3.12, FastAPI, uvicorn, qdrant-client, pydantic-settings, pytest, httpx (TestClient) · Node 22, SvelteKit (`adapter-static`), nginx:alpine · Docker, docker compose.

**Spec:** `docs/superpowers/specs/2026-08-25-mtg-app-connectivity-prototype-design.md`

## Global Constraints

- Backend package name: `mtg_api`, `src/` layout, matching `mtg-ingestion`/`mtg-embed` conventions. Lives at top-level `mtg-api/`, a sibling of `mtg-embed/`, not nested under anything.
- Frontend lives at top-level `mtg-web/`, likewise a sibling, not nested.
- Config via `pydantic-settings` `BaseSettings`, env prefix `MTG_API_`.
- `/api/v1/query` returns hardcoded dummy results — never calls Qdrant.
- `/health` never raises on a Qdrant outage — it catches and reports `"unreachable"`.
- Frontend calls the backend via a **host-published** URL (`http://localhost:8000`), never the compose-internal service DNS name — the browser runs outside the compose network.
- Frontend is a static build (`adapter-static`) served by nginx — no Node runtime in the final image.
- Frontend tasks (5-6) are scaffold/config work with no meaningful unit to TDD in isolation (this is explicitly a wiring prototype, not app logic) — verified instead by the end-to-end acceptance check in Task 7. Backend tasks (1-3) follow TDD throughout.
- Every new Python file lives under `mtg-api/src/mtg_api/`; tests under `mtg-api/tests/`.
- The single `docker-compose.yml` lives at the **repo root**, not inside any package. It replaces `mtg-embed/docker-compose.yml`, which is deleted once its two services are folded into the root file (Task 7) — `mtg-embed/Dockerfile` itself is untouched and still used as that service's build context.
- Working branch: `feature/mtg-app-connectivity-prototype` (already checked out before this plan's tasks begin).

---

### Task 1: Backend scaffold — config, models, package setup

**Files:**
- Create: `mtg-api/pyproject.toml`
- Create: `mtg-api/src/mtg_api/__init__.py`
- Create: `mtg-api/src/mtg_api/config.py`
- Create: `mtg-api/src/mtg_api/models.py`
- Test: `mtg-api/tests/test_config.py`
- Test: `mtg-api/tests/test_models.py`

**Interfaces:**
- Produces: `mtg_api.config.Settings` (fields `qdrant_host: str = "qdrant"`, `qdrant_port: int = 6333`, `cors_origins: list[str] = ["http://localhost:3000"]`, env prefix `MTG_API_`) and a module-level `settings = Settings()`; `mtg_api.models.QueryRequest(query: str)`, `mtg_api.models.QueryResult(source: str, title: str, text: str, score: float)`, `mtg_api.models.QueryResponse(query: str, results: list[QueryResult])`.

- [ ] **Step 1: Write the failing tests**

```python
# mtg-api/tests/test_config.py
from mtg_api.config import Settings


def test_defaults():
    s = Settings(_env_file=None)
    assert s.qdrant_host == "qdrant"
    assert s.qdrant_port == 6333
    assert s.cors_origins == ["http://localhost:3000"]


def test_env_override(monkeypatch):
    monkeypatch.setenv("MTG_API_QDRANT_HOST", "localhost")
    s = Settings(_env_file=None)
    assert s.qdrant_host == "localhost"
```

```python
# mtg-api/tests/test_models.py
from mtg_api.models import QueryRequest, QueryResponse, QueryResult


def test_query_request_requires_query_field():
    req = QueryRequest(query="how does trample work")
    assert req.query == "how does trample work"


def test_query_response_holds_results_list():
    result = QueryResult(source="rule", title="702.19", text="Trample text", score=0.9)
    resp = QueryResponse(query="trample", results=[result])
    assert resp.results[0].source == "rule"
    assert resp.results[0].score == 0.9
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd mtg-api && PYTHONPATH=src python -m pytest tests/test_config.py tests/test_models.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'mtg_api'`)

- [ ] **Step 3: Write the implementation**

```toml
# mtg-api/pyproject.toml
[project]
name = "mtg-api"
version = "0.1.0"
description = "Minimal FastAPI backend for the mtg-app connectivity prototype."
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.110",
    "uvicorn[standard]>=0.29",
    "pydantic-settings>=2.3",
    "qdrant-client>=1.9",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "httpx>=0.27",
    "ruff>=0.5",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/mtg_api"]

[tool.ruff]
line-length = 100
```

```python
# mtg-api/src/mtg_api/__init__.py
```

```python
# mtg-api/src/mtg_api/config.py
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MTG_API_")

    qdrant_host: str = "qdrant"
    qdrant_port: int = 6333
    cors_origins: list[str] = ["http://localhost:3000"]


settings = Settings()
```

```python
# mtg-api/src/mtg_api/models.py
from __future__ import annotations

from pydantic import BaseModel


class QueryRequest(BaseModel):
    query: str


class QueryResult(BaseModel):
    source: str
    title: str
    text: str
    score: float


class QueryResponse(BaseModel):
    query: str
    results: list[QueryResult]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd mtg-api && pip install -e ".[dev]" && PYTHONPATH=src python -m pytest tests/test_config.py tests/test_models.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add mtg-api/pyproject.toml mtg-api/src mtg-api/tests
git commit -m "feat(mtg-api): scaffold package, config, and request/response models"
```

---

### Task 2: `GET /health` with real Qdrant connectivity check

**Files:**
- Create: `mtg-api/src/mtg_api/qdrant_check.py`
- Create: `mtg-api/src/mtg_api/main.py`
- Test: `mtg-api/tests/test_health.py`

**Interfaces:**
- Consumes: `mtg_api.config.settings` (Task 1).
- Produces: `mtg_api.qdrant_check.check_qdrant(client) -> bool`; `mtg_api.main.app` (a `FastAPI` instance); `mtg_api.main.get_qdrant_client() -> QdrantClient` (a FastAPI dependency, overridable in tests).

- [ ] **Step 1: Write the failing test**

```python
# mtg-api/tests/test_health.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd mtg-api && PYTHONPATH=src python -m pytest tests/test_health.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'mtg_api.main'`)

- [ ] **Step 3: Write the implementation**

```python
# mtg-api/src/mtg_api/qdrant_check.py
from __future__ import annotations

from qdrant_client import QdrantClient


def check_qdrant(client: QdrantClient) -> bool:
    try:
        client.get_collections()
    except Exception:
        return False
    return True
```

```python
# mtg-api/src/mtg_api/main.py
from __future__ import annotations

from fastapi import Depends, FastAPI
from qdrant_client import QdrantClient

from mtg_api.config import settings
from mtg_api.qdrant_check import check_qdrant

app = FastAPI(title="mtg-api")


def get_qdrant_client() -> QdrantClient:
    return QdrantClient(host=settings.qdrant_host, port=settings.qdrant_port)


@app.get("/health")
def health(client: QdrantClient = Depends(get_qdrant_client)) -> dict:
    qdrant_status = "ok" if check_qdrant(client) else "unreachable"
    return {"status": "ok", "qdrant": qdrant_status}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd mtg-api && PYTHONPATH=src python -m pytest tests/test_health.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add mtg-api/src/mtg_api/qdrant_check.py mtg-api/src/mtg_api/main.py mtg-api/tests/test_health.py
git commit -m "feat(mtg-api): add /health endpoint with Qdrant connectivity check"
```

---

### Task 3: `POST /api/v1/query` with dummy results + CORS

**Files:**
- Modify: `mtg-api/src/mtg_api/main.py`
- Test: `mtg-api/tests/test_query.py`

**Interfaces:**
- Consumes: `mtg_api.models.QueryRequest/QueryResponse/QueryResult` (Task 1), `mtg_api.config.settings` (Task 1), `mtg_api.main.app` (Task 2).
- Produces: `POST /api/v1/query` route on `app`.

- [ ] **Step 1: Write the failing test**

```python
# mtg-api/tests/test_query.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd mtg-api && PYTHONPATH=src python -m pytest tests/test_query.py -v`
Expected: FAIL (404 on `/api/v1/query`, no CORS header)

- [ ] **Step 3: Write the implementation**

Add to `mtg-api/src/mtg_api/main.py` (imports go at the top of the file
with the existing ones, not inline):

```python
from fastapi.middleware.cors import CORSMiddleware

from mtg_api.models import QueryRequest, QueryResponse, QueryResult
```

Then, after the existing `app = FastAPI(...)` line:

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

_DUMMY_RESULTS = [
    QueryResult(
        source="rule",
        title="702.19. Trample",
        text="702.19a Trample is a static ability...",
        score=0.91,
    ),
    QueryResult(
        source="ruling",
        title="Craterhoof Behemoth",
        text="If the creature with trample is blocked, you may assign...",
        score=0.87,
    ),
]


@app.post("/api/v1/query", response_model=QueryResponse)
def query(request: QueryRequest) -> QueryResponse:
    return QueryResponse(query=request.query, results=_DUMMY_RESULTS)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd mtg-api && PYTHONPATH=src python -m pytest tests/ -v`
Expected: PASS (all tests so far, 9 total)

- [ ] **Step 5: Commit**

```bash
git add mtg-api/src/mtg_api/main.py mtg-api/tests/test_query.py
git commit -m "feat(mtg-api): add POST /api/v1/query with dummy results and CORS"
```

---

### Task 4: Backend Dockerfile

**Files:**
- Create: `mtg-api/Dockerfile`

**Interfaces:**
- Consumes: `mtg-api/pyproject.toml` (Task 1), `mtg_api.main:app` (Task 2/3).
- Produces: an image that runs `uvicorn mtg_api.main:app` on port 8000.

- [ ] **Step 1: Write the Dockerfile**

```dockerfile
# mtg-api/Dockerfile
FROM python:3.12-slim AS builder
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .

FROM python:3.12-slim
WORKDIR /app
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY --from=builder /app/src ./src
ENV PYTHONPATH=/app/src
EXPOSE 8000
CMD ["uvicorn", "mtg_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 2: Build and smoke-test the image**

Run:
```bash
cd mtg-api
docker build -t mtg-api:dev .
docker run --rm -p 8000:8000 mtg-api:dev &
sleep 2
curl -s localhost:8000/health
```
Expected: JSON with `"status": "ok"` (`"qdrant": "unreachable"` is correct
here — no Qdrant container is running for this standalone smoke test).
Stop the container afterward (`docker stop` on its container ID).

- [ ] **Step 3: Commit**

```bash
git add mtg-api/Dockerfile
git commit -m "feat(mtg-api): add multi-stage Dockerfile"
```

---

### Task 5: Frontend scaffold — SvelteKit SPA with search UI

**Files:**
- Create: `mtg-web/package.json`
- Create: `mtg-web/svelte.config.js`
- Create: `mtg-web/vite.config.js`
- Create: `mtg-web/src/app.html`
- Create: `mtg-web/src/routes/+layout.js`
- Create: `mtg-web/src/routes/+page.svelte`
- Create: `mtg-web/src/lib/api.ts`

**Interfaces:**
- Produces: a static-buildable SvelteKit app (`npm run build` → `build/`)
  whose page posts to `${PUBLIC_API_URL}/api/v1/query` and renders
  `results[].title` / `results[].text`.

- [ ] **Step 1: Write `package.json`**

```json
{
  "name": "mtg-web",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite dev",
    "build": "vite build",
    "preview": "vite preview"
  },
  "devDependencies": {
    "@sveltejs/adapter-static": "^3.0.0",
    "@sveltejs/kit": "^2.5.0",
    "@sveltejs/vite-plugin-svelte": "^3.1.0",
    "svelte": "^4.2.0",
    "vite": "^5.2.0"
  }
}
```

- [ ] **Step 2: Write `svelte.config.js`**

```js
import adapter from '@sveltejs/adapter-static';

export default {
  kit: {
    adapter: adapter({
      pages: 'build',
      assets: 'build',
      fallback: 'index.html',
      strict: false
    })
  }
};
```

- [ ] **Step 3: Write `vite.config.js`**

```js
import { sveltekit } from '@sveltejs/vite-plugin-svelte';
import { defineConfig } from 'vite';

export default defineConfig({
  plugins: [sveltekit()]
});
```

- [ ] **Step 4: Write `src/app.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  %sveltekit.head%
</head>
<body data-sveltekit-preload-data="hover">
  <div style="display: contents">%sveltekit.body%</div>
</body>
</html>
```

- [ ] **Step 5: Write `src/routes/+layout.js`** (marks the app fully
  static/prerendered, required by `adapter-static`'s SPA fallback mode)

```js
export const ssr = false;
export const prerender = true;
```

- [ ] **Step 6: Write `src/lib/api.ts`**

```ts
const API_URL = import.meta.env.PUBLIC_API_URL || 'http://localhost:8000';

export interface QueryResult {
  source: string;
  title: string;
  text: string;
  score: number;
}

export interface QueryResponse {
  query: string;
  results: QueryResult[];
}

export async function submitQuery(query: string): Promise<QueryResponse> {
  const resp = await fetch(`${API_URL}/api/v1/query`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query })
  });
  if (!resp.ok) {
    throw new Error(`query failed: ${resp.status}`);
  }
  return resp.json();
}
```

- [ ] **Step 7: Write `src/routes/+page.svelte`**

```svelte
<script lang="ts">
  import { submitQuery, type QueryResult } from '$lib/api';

  let query = '';
  let results: QueryResult[] = [];
  let error = '';

  async function onSubmit() {
    error = '';
    try {
      const resp = await submitQuery(query);
      results = resp.results;
    } catch (e) {
      error = String(e);
    }
  }
</script>

<main>
  <h1>MTG Rules Search (prototype)</h1>
  <form on:submit|preventDefault={onSubmit}>
    <input type="text" bind:value={query} placeholder="Ask a rules question" />
    <button type="submit">Search</button>
  </form>

  {#if error}
    <p style="color: red">{error}</p>
  {/if}

  <ul>
    {#each results as result}
      <li>
        <strong>{result.title}</strong> ({result.source}, score {result.score})
        <p>{result.text}</p>
      </li>
    {/each}
  </ul>
</main>
```

- [ ] **Step 8: Install and verify the dev build runs**

Run:
```bash
cd mtg-web
npm install
npm run build
```
Expected: `build/index.html` and `build/_app/` exist, no build errors.

- [ ] **Step 9: Commit**

```bash
git add mtg-web/package.json mtg-web/svelte.config.js mtg-web/vite.config.js mtg-web/src
git commit -m "feat(mtg-web): scaffold SvelteKit SPA with query form"
```

---

### Task 6: Frontend Dockerfile (multi-stage, nginx)

**Files:**
- Create: `mtg-web/Dockerfile`
- Create: `mtg-web/nginx.conf`
- Create: `mtg-web/.dockerignore`

**Interfaces:**
- Consumes: `mtg-web/`'s (Task 5) `npm run build` output (`build/`).
- Produces: an image serving the static SPA on port 80.

- [ ] **Step 1: Write `.dockerignore`**

```
node_modules
build
.svelte-kit
```

- [ ] **Step 2: Write `nginx.conf`**

```nginx
server {
    listen 80;
    root /usr/share/nginx/html;
    index index.html;

    location / {
        try_files $uri $uri/ /index.html;
    }
}
```

- [ ] **Step 3: Write `Dockerfile`**

```dockerfile
# mtg-web/Dockerfile
FROM node:22-slim AS builder
WORKDIR /app
COPY package.json ./
RUN npm install
COPY . .
ARG PUBLIC_API_URL=http://localhost:8000
ENV PUBLIC_API_URL=$PUBLIC_API_URL
RUN npm run build

FROM nginx:alpine
COPY --from=builder /app/build /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
```

- [ ] **Step 4: Build and smoke-test the image**

Run:
```bash
cd mtg-web
docker build -t mtg-web:dev --build-arg PUBLIC_API_URL=http://localhost:8000 .
docker run --rm -p 3000:80 mtg-web:dev &
sleep 2
curl -s -o /dev/null -w "%{http_code}" localhost:3000
```
Expected: `200`. Stop the container afterward.

- [ ] **Step 5: Commit**

```bash
git add mtg-web/Dockerfile mtg-web/nginx.conf mtg-web/.dockerignore
git commit -m "feat(mtg-web): add multi-stage Dockerfile serving the SPA via nginx"
```

---

### Task 7: Root docker-compose — fold in mtg-embed, wire backend/frontend, verify end to end

**Files:**
- Create: `docker-compose.yml` (repo root)
- Create: `.env.example` (repo root)
- Delete: `mtg-embed/docker-compose.yml`

**Interfaces:**
- Consumes: `mtg-api/Dockerfile` (Task 4), `mtg-web/Dockerfile` (Task 6), `MTG_API_*` env vars (Task 1), `mtg-embed/Dockerfile` (pre-existing, untouched — read its `ENTRYPOINT`/expected env vars from the file itself before writing the `embed` service block below).
- Produces: `docker compose up --build` (from the repo root) bringing up
  `qdrant`, `backend`, `frontend` together on one bridge network sharing
  one `qdrant_storage` volume; `docker compose run --rm embed ...`
  available the same way it was from inside `mtg-embed/` before.

- [ ] **Step 1: Read `mtg-embed/docker-compose.yml` before deleting it**

Run: `cat mtg-embed/docker-compose.yml`

Copy its `qdrant` and `embed` service blocks verbatim into the new root
file in Step 3 below — do not re-derive them from memory. The one path
that changes is the `embed` service's bind mount: it was
`../mtg-ingestion/data/parsed:/app/data:ro` relative to `mtg-embed/`;
relative to the new repo-root file it becomes
`./mtg-ingestion/data/parsed:/app/data:ro`. Its `build:` context changes
from `.` to `./mtg-embed`.

- [ ] **Step 2: Write `.env.example`**

```
# .env.example
MTG_API_QDRANT_HOST=qdrant
MTG_API_QDRANT_PORT=6333
MTG_API_CORS_ORIGINS=["http://localhost:3000"]
PUBLIC_API_URL=http://localhost:8000
```

- [ ] **Step 3: Write the root `docker-compose.yml`**

```yaml
# docker-compose.yml
services:
  qdrant:
    image: qdrant/qdrant:latest
    ports:
      - "6333:6333"
    volumes:
      - qdrant_storage:/qdrant/storage

  embed:
    build: ./mtg-embed
    depends_on:
      - qdrant
    environment:
      MTG_EMBED_QDRANT_HOST: qdrant
      MTG_EMBED_QDRANT_PORT: "6333"
      MTG_EMBED_PARSED_DIR: /app/data
    volumes:
      - ./mtg-ingestion/data/parsed:/app/data:ro
    command: ["run", "--source", "all"]

  backend:
    build: ./mtg-api
    environment:
      MTG_API_QDRANT_HOST: qdrant
      MTG_API_QDRANT_PORT: "6333"
      MTG_API_CORS_ORIGINS: '["http://localhost:3000"]'
    ports:
      - "8000:8000"
    depends_on:
      - qdrant

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

- [ ] **Step 4: Delete the old compose file**

```bash
git rm mtg-embed/docker-compose.yml
```

- [ ] **Step 5: Validate the compose file parses**

Run: `docker compose config --quiet`
Expected: no output, exit code 0.

- [ ] **Step 6: Run the full stack and verify the acceptance check**

Run:
```bash
cp .env.example .env
docker compose up --build -d qdrant backend frontend
sleep 5
curl -s localhost:8000/health
```
Expected: `{"status": "ok", "qdrant": "ok"}`.

Then open `http://localhost:3000` in a browser, type any text into the
search box, submit, and confirm the two dummy results render.

- [ ] **Step 7: Tear down**

```bash
docker compose down
```

- [ ] **Step 8: Commit**

```bash
git add docker-compose.yml .env.example
git commit -m "feat(mtg-app): wire backend, frontend, qdrant, and embed under one compose file"
```
