# mtg-rules

A self-hosted, semantic search app for Magic: The Gathering rules. It ingests
the MTG Comprehensive Rules, Scryfall oracle card text, and rulings into the
Qdrant vector store, then answers natural-language queries by combining an
exact card-name and keyword-ability matcher with dense + sparse hybrid
vector search.

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
                        +--+---------+-----------+--+
                           |         |           |
        Qdrant (6333)      |         |  redis    |  postgres (5433)
        vector store       |         |  (Celery  |  query_history
                           v         |   broker) |  (query/answer/
                      card matcher   |   + worker|   results, answers)
                      (Aho-Corasick) |           v
                                     |      celery tasks:
                                     v      mtg_worker.ingest -> mtg-ingestion
                              Gemini API    mtg_worker.embed  -> mtg-embed
                              (gemini-3.5-flash)
```

### Components

| Directory | What it is |
|---|---|
| `mtg-web/` | SvelteKit SPA frontend. A search page that shows the generated answer with inline citations (hover/focus previews, links to rules and Scryfall), a numbered Sources list and the other retrieved results; a `/rules/[id]` page; and `/history`. Static build served by nginx on port 3000. |
| `mtg-api/` | FastAPI backend. Query endpoint (retrieval + Gemini answer generation), history endpoint, Celery task triggers, task status. Port 8000. |
| `mtg-worker/` | Celery worker. Registers `mtg_worker.ingest` and `mtg_worker.embed`, which delegate to the two packages below. |
| `mtg-worker/mtg-ingestion/` | Fetch + parse stage. Pulls the Comprehensive Rules, Scryfall `oracle_cards` and `rulings` bulk data, writes JSONL to `data/parsed/`. |
| `mtg-worker/mtg-embed/` | Embedding stage. Reads parsed JSONL, embeds chunks (dense + sparse), upserts into the Qdrant `mtg_rules` collection. |
| `postgres` (Docker service) | Stores `query_history`: one row per `/api/v1/query` call (query, generated answer, retrieved results, error), managed by Alembic migrations in `mtg-api/alembic/`. |
| `docs/superpowers/` | Design specs and subagent-driven-development records. |

### Data flow

1. **Ingest** — `mtg_worker.ingest` (or `mtg-ingest` CLI) fetches the three
   raw sources and parses them into date-stamped JSONL files under
   `mtg-worker/mtg-ingestion/data/parsed/`.
2. **Embed** — `mtg_worker.embed` (or `mtg-embed` CLI) chunks each source
   (`rule`, `ruling`, `oracle`), skips points whose `content_hash` is
   unchanged, and upserts dense vectors (`BAAI/bge-base-en-v1.5`) plus sparse
   BM25 vectors (`Qdrant/bm25`) into Qdrant. Each payload also stores a
   `payload_hash`; when only metadata changed (same `content_hash`, different
   payload — e.g. a new field such as `scryfall_uri` or a ruling's
   `published_at`), the payload is overwritten in place without re-embedding.
3. **Query** — `POST /api/v1/query` finds exact card names in the query with an
   Aho-Corasick `CardMatcher` (plus each matched card's own rulings, fetched
   from Qdrant by `oracle_id`) and keyword abilities with a `KeywordMatcher`
   (the keyword's 702.N rule and all its subrules, held in memory), embeds
   the query with both models, runs independent dense and sparse Qdrant
   searches, normalizes and fuses the two score lists (weighted sum), builds
   a text context from the exact matches followed by the top vector hits, and
   sends that context and the query to the answer model (Google
   Gemini, `gemini-3.5-flash` by default) for a synthesized answer.
4. **Cite** — every context block is numbered (`[1] Rule 702.11b: …`,
   `[2] Card — Lightning Bolt: …`, `[3] Ruling — X (2018-01-19): …`) and the
   model is told to cite claims with those numbers only. The server owns the
   number → source mapping: it parses the answer's `[n]` / `[n][m]` /
   `[n, m]` markers, strips out-of-range or malformed ones (logged and
   counted), marks each cited result, and validates any raw rule numbers in
   the prose against the in-memory rules index. None of this depends on
   provider features like tool calling or JSON mode.
5. **Persist** — the query, the cleaned answer (or `null` if generation
   failed), the retrieved results, citations, citation stats, rule
   references and any error are saved as one row in Postgres's
   `query_history` table, then returned to the caller.
6. **Review** — `GET /api/v1/queries` reads `query_history` back out
   (`?limit=&offset=`, newest first) for reviewing past operations.

## Quick start (Docker)

Requires Docker with Compose v2 and BuildKit support (Docker Desktop 23+
or recent Docker Engine — the Dockerfiles rely on BuildKit cache mounts).

Copy [`.env.example`](.env.example) to `.env` and set `MTG_API_GEMINI_API_KEY`
to a real [Gemini](https://aistudio.google.com/apikey) API key — `docker compose up`
fails fast with a clear error if it's missing, and the backend refuses to
start without it.

```bash
docker compose up --build
```

This starts `qdrant`, `redis`, `postgres`, `worker`, `backend` (port 8000),
and `frontend` (port 3000). The first worker build pulls torch (multi-GB)
once; the backend has no torch (it embeds queries with fastembed / ONNX
Runtime). Afterward `docker compose up` reuses the cached layers, and
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

**Upgrading an existing collection:** the first embed run after pulling the
answer-citations change reports roughly `embedded=0 payload_updated=120000`
— every existing point gets its payload rewritten (adding `scryfall_uri`,
`published_at` and `payload_hash`), but nothing is re-embedded. Later runs
skip those points again.

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
| `/api/v1/query` | POST | `{"query": str}` | Hybrid search results plus a generated answer (`null` if generation failed) with validated citations; persists a `query_history` row. See below. |
| `/api/v1/queries` | GET | — | Past query/answer/result records including `citations`, `citation_stats` and `rule_references` (`null` for rows saved before citations), newest first (`?limit=&offset=`, default `limit=50, offset=0`) |
| `/api/v1/rules/{rule_id}` | GET | — | One Comprehensive Rules entry: `rule_id`, `text`, `ancestors` (top-level first), direct `subrules`, `rules_ingested_at`. Case-insensitive, tolerates a trailing `.`; 404 for unknown IDs |
| `/api/v1/ingest` | POST | — | Trigger `mtg_worker.ingest`; returns `{"task_id"}` |
| `/api/v1/embed` | POST | `{"limit": "all" \| int}` | Trigger `mtg_worker.embed`; returns `{"task_id"}` |
| `/api/v1/tasks/{id}` | GET | — | Celery task status (+ result when ready) |

`/api/v1/query` response fields:

- `query`, `answer` — the answer has invalid citation markers removed.
- `results` — every retrieved source, in context-number order (result *i*
  is `[i+1]`), each with `cited: bool` plus `rule_id` / `card_name` /
  `published_at` / `scryfall_uri` where applicable.
- `citations` — only the sources the answer cites: `{number, source_type
  ("rule" | "card" | "ruling"), title, rule_id, card_name, oracle_id, text,
  url, published_at}`. Rule URLs are the frontend route `/rules/{rule_id}`;
  card and ruling URLs are the card's Scryfall page.
- `rule_references` — raw rule numbers in the answer's prose that exist in
  the rules (unknown ones stay plain text and are logged).
- `citation_stats` — `{cited_count, invalid_count, uncited_answer}`;
  `uncited_answer` is true when an answer was generated but cites nothing.

## Configuration

All settings are `pydantic-settings` classes, overridable via env vars. The
root [`.env.example`](.env.example) documents the defaults used by the compose
file; copy it to `.env` to override.

| Var | Default | Used by |
|---|---|---|
| `MTG_API_QDRANT_HOST` / `MTG_API_QDRANT_PORT` | `qdrant` / `6333` | mtg-api |
| `MTG_API_CORS_ORIGINS` | `["http://localhost:3000"]` | mtg-api |
| `MTG_API_BROKER_URL` / `MTG_API_RESULT_BACKEND` | `redis://redis:6379/0` | mtg-api, mtg-worker |
| `MTG_API_GEMINI_API_KEY` | *(required, no default)* | mtg-api |
| `MTG_API_GEMINI_MODEL` | `gemini-3.5-flash` | mtg-api |
| `MTG_API_GEMINI_URL` / `MTG_API_GEMINI_TIMEOUT_SECONDS` | `https://generativelanguage.googleapis.com` / `60` | mtg-api |
| `MTG_API_POSTGRES_DSN` | `postgresql+psycopg://mtg:mtg@postgres:5432/mtg` | mtg-api |
| `MTG_WORKER_BROKER_URL` / `MTG_WORKER_RESULT_BACKEND` | `redis://redis:6379/0` | mtg-worker |
| `MTG_INGEST_DATA_DIR` | `data` | mtg-ingestion |
| `MTG_EMBED_PARSED_DIR` | `../mtg-ingestion/data/parsed` | mtg-embed |
| `MTG_EMBED_QDRANT_HOST` / `PORT` | `localhost` / `6333` | mtg-embed |

Additional knobs (query side): `MTG_API_DENSE_MODEL_NAME`,
`MTG_API_SPARSE_MODEL_NAME`, `MTG_API_HYBRID_DENSE_WEIGHT` /
`MTG_API_HYBRID_SPARSE_WEIGHT` (default 0.5 each), `MTG_API_HYBRID_TOP_K`,
`MTG_API_HYBRID_SCORE_THRESHOLD`, `MTG_API_RULES_TOP_K` (default 5: extra
hits from a rules-only search; 0 disables it), `MTG_API_GENERATION_TEMPERATURE`
(default 0) / `MTG_API_GENERATION_MAX_TOKENS` (unset: the model's default),
`MTG_API_EMBED_THREADS` (ONNX Runtime threads per query-embedding model;
unset uses every core, so cap it on a host shared with other apps),
`MTG_API_TASK_ENDPOINTS` (default true; false makes `/ingest`, `/embed`
and `/tasks` return 404, for deployments without a worker).

The frontend container reads `MTG_WEB_AUTH_PASSWORD` / `MTG_WEB_AUTH_USER`
(default `admin`) at startup. With a password set, the whole site requires
basic auth. Unset (the dev stack), it's open.

The worker indexes with `sentence-transformers` while the backend embeds
queries with fastembed's ONNX export of the same model; the two agree to
cosine 1.0000 on the eval questions.

Model weights download from HuggingFace on first use.

## Running components without Docker

Each package is an installable Python project (`hatchling`) requiring
Python >= 3.12:

```bash
pip install -e "mtg-worker/mtg-ingestion[dev]"
pip install -e "mtg-worker/mtg-embed[dev]"
pip install -e "mtg-api[dev]"
pip install -e "evals[dev]"     # eval harness (see "Evals")
```

### Regenerating the dependency locks

The Docker builds consume pinned `requirements.lock` files, not the `>=`
floors in the pyproject files — that's what keeps the heavy torch layer
cacheable. Regenerate after a deliberate dependency change with `uv pip
compile`, targeting the container's Python version *and platform* — the
Dockerfiles build `python:3.12-slim` (Linux) images, and `uv pip compile`
otherwise resolves for whatever host OS you run it on. Compiling on Windows
or macOS without `--python-platform` silently pulls in platform-only
packages (e.g. `pywin32`) that the Linux build then fails to install:

```bash
# mtg-api — runtime deps only
uv pip compile --python-version 3.12 --python-platform x86_64-unknown-linux-gnu \
  mtg-api/pyproject.toml -o mtg-api/requirements.lock

# mtg-worker — union of mtg_worker + mtg-ingestion + mtg-embed runtime deps
uv pip compile --python-version 3.12 --python-platform x86_64-unknown-linux-gnu \
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

`pytest` per package (parsing/embedding logic only; no network needed). Run
each suite from its own directory — collecting them in one invocation
collides on shared test-module names:

```bash
(cd mtg-worker && pytest)
(cd mtg-worker/mtg-ingestion && pytest)
(cd mtg-worker/mtg-embed && pytest)
(cd mtg-api && pytest)
(cd evals && pytest)
(cd deploy && pytest)       # production data sync script
```

Lint each package with `ruff check . && ruff format --check .`. In
`mtg-worker/`, use `src tests mtg-ingestion mtg-embed` in place of `.`.
ruff is pinned (`ruff==0.16.4`) because its default rules and format style
change between releases.

### CI

GitHub Actions runs one workflow per component on pushes and pull requests
to `main` (`.github/workflows/`):

| Workflow | Runs |
|---|---|
| Backend CI | mtg-api: lint, tests, Docker image build |
| Frontend CI | mtg-web: `npm run build`, Docker image build |
| Worker CI | mtg-worker, mtg-ingestion, mtg-embed: lint, tests (CPU-only torch) |
| Evals CI | evals: lint, tests |
| Deploy CI | deploy: lint, tests |

The image builds catch Dockerfile breakage before Coolify, which builds the
same Dockerfiles on deploy.

## Evals

`eval.yaml` (repo root) is a 50-question set. Its header documents the
fields. Each case lists the rules, rulings and cards that must reach the LLM
context, plus a reference answer. The `evals/` package runs it against the
running stack and diffs the result against a committed baseline.

### Enabling eval mode (local only)

The harness needs an API surface that is off by default and must never be
on in production: per-request setting overrides, `generate=false`, the
`context_hash` / `prompt_version` / `generator` response fields, and
`GET /api/v1/config`. Turn it on locally:

```bash
echo "MTG_API_EVAL_MODE=true" >> .env
docker compose up -d --build backend
curl localhost:8000/api/v1/config     # 404 means eval mode is still off
```

With eval mode off, a request with non-empty `overrides` gets a 403 and
`/api/v1/config` returns 404. Requests the harness sends carry
`"source": "eval"`, and the API never saves those to query history. This
applies with or without eval mode, so eval runs never show up in
`GET /api/v1/queries` or the History page.

### Running

```bash
pip install -e "evals[dev]"
make eval-validate                  # eval.yaml vs the latest parsed rules/cards/rulings
make eval                           # retrieval mode, dev split, compare to baseline
make eval EXP=dense70               # same, with an experiment's overrides
make eval-full TAG=negative         # + answer generation and the LLM judge
make eval-test                      # both modes on the held-out test split
make eval-baseline MODE=retrieval   # promote the latest retrieval run
make eval-compare A=<run> B=<run>   # diff any two runs
make eval-show                      # reprint the latest run's report (RUN=<run> for another)
make eval-sweep EXPS="dense70 topk15"
```

All run targets accept `EXP=`, `TAG="a b"` and `ID="x y"`. Each target is
a thin wrapper around `python -m mtg_evals ...` (`--help` lists every
option). On Windows without GNU make, install it with
`winget install ezwinports.make`, or call the module directly:

```bash
python -m mtg_evals run --mode retrieval --split dev --exp dense70
```

- **retrieval** mode makes no LLM calls. It scores whether each required
  rule (prefix match), ruling and card appears in the returned results,
  where the first one ranks, and whether the card matcher returned the
  expected cards and none of the forbidden ones. Default concurrency is 4.
- **full** mode also generates answers and grades them with an LLM judge:
  correct / partial / incorrect against the reference answer, or
  declined_properly / answered_anyway for out-of-scope questions. It also
  parses yes/no verdicts deterministically and records citation checks.
  Default concurrency is 1, since the model is local. Configure the judge
  with any OpenAI-compatible endpoint: `EVAL_JUDGE_BASE_URL`,
  `EVAL_JUDGE_MODEL`, `EVAL_JUDGE_API_KEY` (see `.env.example`). The report
  warns if the judge is the same model as the generator.

Answers are cached in `evals/.cache/` by question, context hash, generator,
`PROMPT_VERSION` and generation overrides. Judge verdicts are cached by
question, reference answer, answer, judge model and judge prompt version.
A repeat run only calls the models for what changed. Delete the directory
to start fresh. Bump `PROMPT_VERSION` in `mtg-api/src/mtg_api/llm.py`
whenever the answer prompt changes.

Every run writes `evals/runs/<UTC time>-<sha>[-dirty]-<mode>[-<exp>].json`
(gitignored). The run file holds the metadata, the API config, per-case
scores and aggregates. The committed baselines are
`evals/baseline-retrieval.json` and `evals/baseline-full.json`. The report
lists REGRESSIONS and FIXED cases with a reason each, and warns about
added or removed cases, a changed `eval.yaml`, and config differences the
experiment doesn't explain. The exit code is 1 if any case regressed and 2
if the pre-run checks failed or the command was used wrong.

Experiments live in `evals/experiments/<name>.yaml` as `description` +
`overrides`. The API accepts only these override keys: the `hybrid_*`
settings, `rules_top_k`, `card_ruling_limit`, `collection_name`, `gemini_model`,
`generation_temperature` and `generation_max_tokens`. Any other key gets
a 422.

## Production deployment

Production runs on [Coolify](https://coolify.io) (Docker Compose build pack)
on a small host shared with other apps (target: a 4 GB, 2 vCPU VM, where
Coolify itself takes roughly 0.5–0.8 GB). The stack is
[`docker-compose.prod.yml`](docker-compose.prod.yml). It is standalone, not
an override of the dev compose file.

- **Services:** `qdrant`, `postgres`, `backend`, `frontend`. There is no
  worker or redis: ingest and embed run on a dev machine (a GPU makes
  embedding fast), and the results are copied to the server.
- **Networking:** no published ports. Coolify's Traefik terminates TLS and
  routes the frontend's domain to nginx on port 80. nginx serves the SPA
  and proxies `/api` and `/health` to the backend, so the browser only ever
  talks to one origin (no CORS).
- **Limits:** memory caps (backend 900m, qdrant 512m, postgres 256m,
  frontend 64m), a 1-CPU cap and one ONNX thread per model on the backend,
  and log rotation (3 × 10 MB per container). Measured at idle after a
  query: about 950 MB for the whole stack.
- **Builds:** Coolify builds both images from the repo on each deploy. The
  backend has no torch, so both builds are light.

### Coolify setup

1. New resource → your Git repository → build pack **Docker Compose**,
   compose file `/docker-compose.prod.yml`, and the branch to deploy.
2. On the `frontend` service, set the domain (e.g. `https://rules.example.com`).
   Leave the other services without domains.
3. Environment variables: set `MTG_API_GEMINI_API_KEY`. Coolify generates
   `SERVICE_PASSWORD_POSTGRES` and `SERVICE_PASSWORD_WEB` itself. Optional:
   `MTG_WEB_AUTH_USER` (default `admin`), `MTG_API_GEMINI_MODEL`,
   `MTG_API_EMBED_THREADS` (default 1).
4. Deploy. On the first deploy the backend crash-loops and the frontend
   doesn't start until the data is seeded (below): the backend refuses to
   start without the parsed rules and cards.

### Access

The site is private while it's being tested. The frontend's nginx puts the
whole site behind HTTP basic auth: every page and every `/api` call. Log in
as `MTG_WEB_AUTH_USER` (default `admin`), with the `SERVICE_PASSWORD_WEB`
value from the resource's environment variables in Coolify (you can replace
it with your own). The browser sends the credentials with the SPA's
same-origin API calls automatically. `/health` stays open for uptime
monitoring.

The backend also runs with `MTG_API_TASK_ENDPOINTS=false`, so `/ingest`,
`/embed` and `/tasks` return 404 (production has no Celery worker), and
with eval mode off.

To open the site to the public, remove `MTG_WEB_AUTH_PASSWORD` from the
frontend in `docker-compose.prod.yml`. Before you do, add rate limiting on
`/api/v1/query` (every query is a paid Gemini call), and restrict
`/api/v1/queries`, which shows every visitor's questions.

### Seeding the data

[`deploy/sync_data.py`](deploy/sync_data.py) copies this machine's data into
the deployed stack over SSH. It sends the local Qdrant collection (as a
snapshot) and the latest `cards_*.jsonl` / `rules_*.jsonl`, restarts the
backend, and starts the rest of the stack:

```bash
make sync-prod HOST=root@your-server     # or: python deploy/sync_data.py --host root@your-server
```

Run it after the first deploy, and again whenever you re-ingest or re-embed
locally. It needs SSH access as a user that can run `docker` on the server,
and the dev stack's Qdrant running on `localhost:6333`.

- **Finding the stack:** Coolify prefixes container and volume names with a
  generated UUID. The script finds the backend as the container with a
  *volume* at `/app/data/parsed`, and qdrant as the container in the same
  compose project. If several stacks match, pass `PROJECT=<uuid>`
  (`--project`).
- **Nothing is staged on the server's disk:**
  - The snapshot (about 580 MB) streams into the qdrant container and is
    recovered from there.
  - The JSONL streams into the parsed-data volume through a throwaway
    `alpine` container.
- **It fails loudly:** it exits non-zero if the server's point count after
  recovery doesn't match the local one, or if the backend isn't healthy
  within 5 minutes.
- **Testing:** `--local` runs the server-side steps against the local
  Docker instead. Point it at a local copy of the production stack, e.g.
  `docker compose -p mtgprod -f docker-compose.prod.yml up -d`, with
  `SERVICE_PASSWORD_POSTGRES` and `MTG_API_GEMINI_API_KEY` exported.

### Host preparation

Hetzner images come without swap. Add a 2 GB swapfile as a safety net on a
4 GB box, and keep the Hetzner Cloud Firewall to ports 22, 80 and 443
(Docker's published ports bypass `ufw`).

## Known limitations

- The rules parser stops before the Glossary section.
- Only oracle-level card identity is modeled — no per-printing/set data.
- The diff/persistence stage (comparing parsed JSONL to the store by
  `content_hash`, scheduling re-syncs) is not yet built; re-running embed is
  idempotent for unchanged content.
- No user accounts, rate limiting or CI. Production is private behind
  basic auth, and TLS is handled by Coolify's proxy (see "Production
  deployment").
- Citations are validated for existence only: a cited `[n]` is guaranteed to
  be a source that was in the context, not that it supports the sentence.
- A raw rule number in the answer that exists in the rules is linked even if
  that rule wasn't in the context (the model quoting from memory).
- The rule page shows the rules file's ingest date, not the Comprehensive
  Rules' own "effective as of" date, which isn't carried through parsing.