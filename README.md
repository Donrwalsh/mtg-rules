# mtg-rules

A self-hosted, semantic search app for Magic: The Gathering rules. It ingests
the MTG Comprehensive Rules, Scryfall oracle card text, and rulings into the
Qdrant vector store, then answers natural-language queries by combining an
exact card-name matcher with dense + sparse hybrid vector search.

Built as a connectivity / retrieval prototype: FastAPI backend, SvelteKit
frontend, Celery worker, and a Qdrant + Redis backing stack, all orchestrated
by a single `docker-compose.yml`.

## Architecture

```
                        +---------------------------+
   browser (localhost:3000)   frontend (mtg-web)    |
                        |     SvelteKit SPA / nginx |
                        +------------+--------------+
                                     | fetch POST /api/v1/query
                                     v
                        +---------------------------+
                        |   backend (mtg-api)       |  FastAPI, port 8000
                        |   POST /health, /api/v1/* |
                        +----+------------+---------+
                             |            |
          Qdrant (6333)      |            |  redis (Celery broker)
          vector store       |            |  + worker (mtg-worker)
                             v            |
                        card matcher       v
                        (Aho-Corasick)   celery tasks:
                                         mtg_worker.ingest -> mtg-ingestion
                                         mtg_worker.embed  -> mtg-embed
```

### Components

| Directory | What it is |
|---|---|
| `mtg-web/` | SvelteKit SPA frontend. One page with a search box that POSTs to the backend and lists results. Static build served by nginx on port 3000. |
| `mtg-api/` | FastAPI backend. Query endpoint, Celery task triggers, task status. Port 8000. |
| `mtg-worker/` | Celery worker. Registers `mtg_worker.ingest` and `mtg_worker.embed`, which delegate to the two packages below. |
| `mtg-worker/mtg-ingestion/` | Fetch + parse stage. Pulls the Comprehensive Rules, Scryfall `oracle_cards` and `rulings` bulk data, writes JSONL to `data/parsed/`. |
| `mtg-worker/mtg-embed/` | Embedding stage. Reads parsed JSONL, embeds chunks (dense + sparse), upserts into the Qdrant `mtg_rules` collection. |
| `docs/superpowers/` | Design specs and subagent-driven-development records. |

### Data flow

1. **Ingest** — `mtg_worker.ingest` (or `mtg-ingest` CLI) fetches the three
   raw sources and parses them into date-stamped JSONL files under
   `mtg-worker/mtg-ingestion/data/parsed/`.
2. **Embed** — `mtg_worker.embed` (or `mtg-embed` CLI) chunks each source
   (`rule`, `ruling`, `oracle`), skips points whose `content_hash` is
   unchanged, and upserts dense vectors (`BAAI/bge-base-en-v1.5`) plus sparse
   BM25 vectors (`Qdrant/bm25`) into Qdrant.
3. **Query** — `POST /api/v1/query` finds exact card names in the query with an
   Aho-Corasick `CardMatcher`, embeds the query with both models, runs
   independent dense and sparse Qdrant searches, normalizes and fuses the two
   score lists (weighted sum), and returns card matches plus top vector hits.

## Quick start (Docker)

Requires Docker with Compose v2 and BuildKit support (Docker Desktop 23+
or recent Docker Engine — the Dockerfiles rely on BuildKit cache mounts).

```bash
docker compose up --build
```

This starts `qdrant`, `redis`, `worker`, `backend` (port 8000), and
`frontend` (port 3000). The first build pulls torch (multi-GB) once per
Python image; afterward `docker compose up` reuses the cached layers, and
only cached-model wipe (`docker compose down -v`) triggers re-downloads.
Backend/worker model weights live in a named `hf_cache` volume
(`/root/.cache/huggingface`), so they persist across normal `up`/`down`
cycles.

### Seed the data

The web app query endpoint needs embedded data in Qdrant. Two-step process:

```bash
# 1. Trigger the ingestion pipeline (fetch + parse)
curl -X POST localhost:8000/api/v1/ingest

# 2. Poll the task until SUCCESS, then trigger embedding
curl localhost:8000/api/v1/tasks/<task_id>
curl -X POST localhost:8000/api/v1/embed -H 'Content-Type: application/json' -d '{"limit": "all"}'
```

The live fetches hit `magic.wizards.com` and `api.scryfall.com`, so this needs
a machine that can reach them.

### Verify it works

```bash
curl localhost:8000/health
# {"status":"ok","qdrant":"ok"}

curl -X POST localhost:8000/api/v1/query \
  -H 'Content-Type: application/json' \
  -d '{"query": "When exactly can I cast a sorcery?"}'
```

Or open http://localhost:3000, type a question, and submit.

## Backend API

| Endpoint | Method | Body | Description |
|---|---|---|---|
| `/health` | GET | — | Service health + Qdrant reachability |
| `/api/v1/query` | POST | `{"query": str}` | Hybrid search results |
| `/api/v1/ingest` | POST | — | Trigger `mtg_worker.ingest`; returns `{"task_id"}` |
| `/api/v1/embed` | POST | `{"limit": "all" \| int}` | Trigger `mtg_worker.embed`; returns `{"task_id"}` |
| `/api/v1/tasks/{id}` | GET | — | Celery task status (+ result when ready) |

## Configuration

All settings are `pydantic-settings` classes, overridable via env vars. The
root [`.env.example`](.env.example) documents the defaults used by the compose
file; copy it to `.env` to override.

| Var | Default | Used by |
|---|---|---|
| `MTG_API_QDRANT_HOST` / `MTG_API_QDRANT_PORT` | `qdrant` / `6333` | mtg-api |
| `MTG_API_CORS_ORIGINS` | `["http://localhost:3000"]` | mtg-api |
| `MTG_API_BROKER_URL` / `MTG_API_RESULT_BACKEND` | `redis://redis:6379/0` | mtg-api, mtg-worker |
| `MTG_WORKER_BROKER_URL` / `MTG_WORKER_RESULT_BACKEND` | `redis://redis:6379/0` | mtg-worker |
| `MTG_INGEST_DATA_DIR` | `data` | mtg-ingestion |
| `MTG_EMBED_PARSED_DIR` | `../mtg-ingestion/data/parsed` | mtg-embed |
| `MTG_EMBED_QDRANT_HOST` / `PORT` | `localhost` / `6333` | mtg-embed |
| `PUBLIC_API_URL` | `http://localhost:8000` | mtg-web build ARG |

Additional knobs (query side): `MTG_API_DENSE_MODEL_NAME`,
`MTG_API_SPARSE_MODEL_NAME`, `MTG_API_HYBRID_DENSE_WEIGHT` /
`MTG_API_HYBRID_SPARSE_WEIGHT` (default 0.5 each), `MTG_API_HYBRID_TOP_K`,
`MTG_API_HYBRID_SCORE_THRESHOLD`.

Model weights download from HuggingFace on first use.

## Running components without Docker

Each package is an installable Python project (`hatchling`) requiring
Python >= 3.12:

```bash
pip install -e "mtg-worker/mtg-ingestion[dev]"
pip install -e "mtg-worker/mtg-embed[dev]"
pip install -e "mtg-api[dev]"
```

### Regenerating the dependency locks

The Docker builds consume pinned `requirements.lock` files, not the `>=`
floors in the pyproject files — that's what keeps the heavy torch layer
cacheable. Regenerate after a deliberate dependency change with `uv pip
compile`, targeting the container's Python version:

```bash
# mtg-api — runtime deps only
uv pip compile --python-version 3.12 mtg-api/pyproject.toml -o mtg-api/requirements.lock

# mtg-worker — union of mtg_worker + mtg-ingestion + mtg-embed runtime deps
uv pip compile --python-version 3.12 \
  mtg-worker/mtg-ingestion/pyproject.toml mtg-worker/mtg-embed/pyproject.toml mtg-worker/pyproject.toml \
  -o mtg-worker/requirements.lock
```

CLI entry points:

```bash
mtg-ingest run-all                                   # fetch + parse everything
mtg-embed run --source rules|cards|rulings|all      # embed into Qdrant
```

Frontend:

```bash
cd mtg-web
npm install
npm run dev        # Vite dev server
npm run build      # static build for adapter-static
```

## Tests

`pytest` per package (parsing/embedding logic only; no network needed):

```bash
pytest mtg-worker/mtg-ingestion/tests mtg-worker/mtg-embed/tests mtg-api/tests
```

## Known limitations

- The rules parser stops before the Glossary section.
- Only oracle-level card identity is modeled — no per-printing/set data.
- The diff/persistence stage (comparing parsed JSONL to the store by
  `content_hash`, scheduling re-syncs) is not yet built; re-running embed is
  idempotent for unchanged content.
- No auth, CI, or production TLS — the compose file targets a Coolify-style
  single-host deployment with the edge handled upstream.