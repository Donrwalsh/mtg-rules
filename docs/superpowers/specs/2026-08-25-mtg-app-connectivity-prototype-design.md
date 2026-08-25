# mtg-app connectivity prototype: FastAPI + SvelteKit + Qdrant

Status: draft, awaiting approval
Date: 2026-08-25

## Purpose

Prove the container topology for the future RAG app works end to end —
frontend talks to backend, backend talks to Qdrant, all three (four,
counting `mtg-embed`) run under one Coolify-style compose file — before
any real retrieval logic exists. No RAG, no parsing, no cross-encoder.
Success = a browser search box returns a static dummy JSON payload that
round-tripped through the backend, and the backend's own health/Qdrant
checks pass.

## Scope decisions

- **Location:** two new top-level sibling packages, alongside
  `mtg-ingestion/` and `mtg-embed/`: `mtg-api/` (backend) and `mtg-web/`
  (frontend). Neither nests inside the other.
- **Single `docker-compose.yml` at the repo root** covers everything —
  `qdrant`, `embed` (moved here from `mtg-embed/docker-compose.yml`,
  which is deleted), `backend`, `frontend`. One file, one project, so the
  `qdrant_storage` volume is genuinely one volume rather than two
  same-named-but-distinct volumes under separate compose projects.
- **Shared Qdrant volume:** `backend` reads from the same `qdrant_storage`
  volume `embed` writes to — the backend's `/health` check and (later,
  out of scope here) real search both depend on data `mtg-embed` already
  put there. A single compose file/volume declaration is what makes this
  sharing real rather than name-coincidental.
- **`.env.example` at the repo root**, next to the compose file.
- **Backend package name:** `mtg_api`. **Frontend package name:**
  `mtg-web` (npm), matching the directory names.

## 1. FastAPI backend (`mtg-api/`)

- `GET /health` → `{"status": "ok", "qdrant": "ok" | "unreachable"}`.
  Qdrant check: construct a `QdrantClient(host=settings.qdrant_host,
  port=settings.qdrant_port)` and call `get_collections()`; catch
  connection errors and report `"unreachable"` rather than raising —
  health checks must never 500 just because a dependency is down.
- `POST /api/v1/query` — request body `{"query": str}` (Pydantic
  `QueryRequest`), response `{"query": str, "results": [QueryResult, ...]}`
  where `QueryResult = {"source": str, "title": str, "text": str,
  "score": float}`. Returns **hardcoded dummy data** (2-3 fake rule/ruling
  hits, always the same shape) — no Qdrant call in this endpoint yet,
  even though the same Qdrant volume now genuinely has embedded data in
  it (wiring the real search call is explicitly out of scope for this
  prototype).
- Config: `mtg_api.config.Settings` (`pydantic-settings`, env prefix
  `MTG_API_`): `qdrant_host="qdrant"`, `qdrant_port=6333`,
  `cors_origins: list[str] = ["http://localhost:3000"]`.
- CORS: `CORSMiddleware` restricted to `settings.cors_origins`, methods
  `GET, POST`, so the SvelteKit served frontend can call it cross-origin.

## 2. SvelteKit frontend (`mtg-web/`, SPA mode)

- One route (`/`): a text input, a submit button, and a results list.
- On submit: `fetch(PUBLIC_API_URL + "/api/v1/query", {method: "POST",
  body: JSON.stringify({query}), headers: {"Content-Type":
  "application/json"}})`, then render each returned result's `title` and
  `text` in a plain list. No styling beyond minimal readability — this is
  a wiring test, not a UI.
- `PUBLIC_API_URL` read from a Vite env var, defaulted to
  `http://localhost:8000`. Called out explicitly: the browser calls the
  **host-published** port (`localhost:8000`), never the compose-internal
  service DNS name (`backend:8000`) — fetch runs client-side in the
  browser, outside the compose network. This is the most common mistake
  in this kind of setup.
- Build mode: `@sveltejs/adapter-static` (full SPA fallback) — served by
  nginx in the container, not Node.

## 3. Containerization

- **Backend Dockerfile** (`mtg-api/Dockerfile`): multi-stage —
  `python:3.12-slim` builder installs the package with `pip install .`,
  runtime stage copies installed site-packages + source, runs
  `uvicorn mtg_api.main:app --host 0.0.0.0 --port 8000`.
- **Frontend Dockerfile** (`mtg-web/Dockerfile`): multi-stage —
  `node:22-slim` builder runs `npm install && npm run build`
  (adapter-static output in `build/`), runtime stage is `nginx:alpine`
  serving `build/` with an SPA-fallback `nginx.conf`
  (`try_files $uri /index.html`). `PUBLIC_API_URL` is a build ARG, baked
  in at build time — static builds can't read runtime env vars.
- **`docker-compose.yml`** (repo root), four services on one bridge
  network:
  - `qdrant`: official image, `qdrant_storage` named volume, port `6333`
    published (so `mtg-embed` re-runs or manual inspection work from the
    host too).
  - `embed`: builds `./mtg-embed` (moved verbatim from the deleted
    `mtg-embed/docker-compose.yml`), reads `../mtg-ingestion/data/parsed`
    → `./mtg-ingestion/data/parsed` relative to the new root context,
    bind-mounted read-only, `depends_on: [qdrant]`. Not started by
    default in normal `up` runs that only care about the web app — it's
    a one-shot job, invoked with `docker compose run --rm embed ...` the
    same way `mtg-embed`'s own docs already describe.
  - `backend`: builds `./mtg-api`, env `MTG_API_QDRANT_HOST=qdrant` etc.,
    publishes `8000:8000`, `depends_on: [qdrant]`.
  - `frontend`: builds `./mtg-web`, publishes `3000:80`, build-arg
    `PUBLIC_API_URL=http://localhost:8000`, `depends_on: [backend]`.
  - `.env.example` (repo root): documents every var above; `.env` itself
    stays gitignored (root `.gitignore` already ignores `.env`).
  - `mtg-embed/docker-compose.yml` is deleted as part of this work — its
    `qdrant`/`embed` service definitions move into the root file
    unchanged in substance (image, env vars, mount, command), so
    `mtg-embed`'s own existing usage docs (`docker compose run --rm
    embed ...`) keep working, just invoked from the repo root instead of
    from inside `mtg-embed/`.

## Out of scope

- No real search/embedding call from `/api/v1/query` — dummy data only,
  even though real embedded data now sits in the shared volume.
- No auth, no CI.
- No production TLS/reverse-proxy config — Coolify is assumed to own the
  edge; this compose file only proves the four services boot and talk to
  each other.
- No fix for `mtg-embed`'s pre-existing CLI quirk (the documented
  `mtg-embed run --source ... --limit ...` invocation doesn't actually
  work due to Typer's single-command collapsing — carried over unchanged
  from the prior session, not touched here).

## Acceptance check

`docker compose up --build` at the repo root brings up `qdrant`,
`backend`, and `frontend` (an explicit `docker compose run --rm embed`
seeds real data into the shared volume separately, on demand);
`curl localhost:8000/health` returns `{"status": "ok", "qdrant": "ok"}`;
opening `localhost:3000`, typing a query, and submitting shows the dummy
results rendered in the browser — proving frontend → backend → (backend
→ Qdrant, independently, via `/health`) connectivity end to end, on the
same volume `mtg-embed` populates.
