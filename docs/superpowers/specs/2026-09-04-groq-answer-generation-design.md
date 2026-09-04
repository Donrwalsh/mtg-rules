# Groq answer generation + query history

Status: approved, ready for implementation planning
Date: 2026-09-04

## Purpose

`POST /api/v1/query` today returns a raw list of retrieved sources (exact
card matches + fused dense/sparse vector hits) — no synthesized answer, by
explicit decision of the prior hybrid-query spec ("no answer synthesis,
no citation formatting"). This spec adds that synthesis stage: the
existing retrieved passages are stuffed into a prompt and sent to Groq
(`openai/gpt-oss-120b`), and the generated answer is returned
alongside the unchanged results list. Every operation (query, answer,
retrieved passages) is also persisted to a new Postgres store so past
queries can be reviewed later via a new read endpoint. This is a first
version: synchronous, best-effort persistence, no auth, no answer
streaming.

## Architecture

```
browser -> mtg-web (Svelte)
              | POST /api/v1/query
              v
mtg-api (FastAPI)
  1. card_matcher + hybrid_search (unchanged, existing)
  2. build context from card_results + vector_results
  3. Groq openai/gpt-oss-120b completion (context + query -> answer)
  4. INSERT query_history row (query, answer, results, model, error)
  5. return {query, results, answer}
              |
              v
         Postgres (new service)      Groq API (external)
```

`GET /api/v1/queries` reads `query_history` back out for review — no
frontend history page in this version, JSON only.

## Data model

New table `query_history` (Postgres, managed by Alembic):

| column | type | notes |
|---|---|---|
| `id` | `serial primary key` | |
| `query` | `text not null` | the user's original query string |
| `answer` | `text null` | Groq's generated answer; `null` if generation failed |
| `results` | `jsonb not null` | the same `QueryResult[]` list returned to the caller, as JSON |
| `model` | `text not null` | the Groq model name used (`settings.groq_model` at call time) |
| `error` | `text null` | exception message if Groq generation failed; `null` on success |
| `created_at` | `timestamptz not null default now()` | |

One row per `/api/v1/query` call, written regardless of whether Groq or
the DB write itself partially failed (the row write is the last thing
that runs, wrapped in its own try/except so it can never be the reason
the endpoint fails — see Error handling).

## New components (`mtg-api`)

**`llm.py`** — Groq wrapper, mirrors the existing `Embedder`/
`SparseEmbedder` seam pattern (a thin class wrapping a real client,
constructed via a factory function so tests can substitute a fake):
- `build_context(results: list[QueryResult]) -> str` — formats each
  result as a short block (`[{source}] {title}\n{text}`), joined by
  blank lines. Uses the full `card_results + vector_results` list
  already produced by the existing retrieval step (capped today at
  `hybrid_top_k=10` vector hits plus however many card matches) — no new
  truncation logic; Groq's 128k-token context window comfortably fits
  this.
- `GroqAnswerer(client, model: str)` — `.generate(query: str, context:
  str) -> str`, sends one chat completion call: a system prompt
  instructing the model to answer Magic: The Gathering rules questions
  using only the provided context, and to say so plainly if the context
  doesn't cover the question, followed by a user message containing the
  context and the query. Returns the completion text. Raises on any SDK
  error — callers catch it, `llm.py` itself does not swallow anything.
- `load_groq_answerer(api_key: str, model: str) -> GroqAnswerer` — real
  factory, `from groq import Groq` imported inside the function body
  (same lazy-import style as `load_sentence_transformer_embedder`).

**`history.py`** — persistence layer, SQLAlchemy Core (no ORM models,
consistent with this being a single append-mostly table):
- `query_history` — a `sqlalchemy.Table` definition matching the schema
  above.
- `save_history(engine, *, query, answer, results, model, error) -> None`
  — one INSERT. `results` is passed already-serialized to plain
  dicts/JSON-safe values (via `[r.model_dump() for r in results]`,
  called by `main.py` before invoking this).
- `list_history(engine, *, limit: int = 50, offset: int = 0) -> list[dict]`
  — `SELECT * FROM query_history ORDER BY created_at DESC LIMIT/OFFSET`,
  returns rows as dicts.

**`alembic/`** — standard Alembic layout (`alembic.ini`, `env.py`,
`versions/`) rooted in `mtg-api/`. One migration: create `query_history`
with the columns above.

**`main.py` changes:**
- New settings-backed dependency `get_db_engine() -> Engine`, `@lru_cache`
  wrapped like the existing model/client providers, built from
  `settings.postgres_dsn`.
- New dependency `get_groq_answerer() -> GroqAnswerer`, `@lru_cache`
  wrapped, built from `settings.groq_api_key` / `settings.groq_model`.
  Added to the `lifespan` warm-up alongside the existing matcher/embedder
  warm-up (the Groq client itself is cheap to construct — this is for
  consistency, not cold-start cost).
- `POST /api/v1/query` — after building `card_results + vector_results`
  exactly as today:
  1. `context = build_context(all_results)`.
  2. Call `answerer.generate(request.query, context)` inside a
     try/except; on success, `answer = <text>`, `error = None`; on any
     exception, `answer = None`, `error = str(exc)` (logged too).
  3. `save_history(engine, query=request.query, answer=answer,
     results=[r.model_dump() for r in all_results], model=settings.groq_model,
     error=error)` inside its own try/except — any exception here is
     logged and swallowed, never re-raised.
  4. Return `QueryResponse(query=request.query, results=all_results,
     answer=answer)` — HTTP 200 in every case except the pre-existing
     retrieval path failing (unchanged behavior there).
- New `GET /api/v1/queries?limit=&offset=` — calls `list_history`,
  returns the rows as JSON directly (no new Pydantic response model
  needed; the rows are already JSON-safe dicts).

## Config (`mtg_api.config.Settings`, unchanged `MTG_API_` prefix)

- `groq_api_key: str` — required, no default (never committed).
- `groq_model: str = "openai/gpt-oss-120b"` (originally scoped as
  `llama-3.3-70b-versatile`; Groq had fully retired that model from its
  catalog by implementation time — confirmed via `GET
  https://api.groq.com/openai/v1/models` returning a 404 for it — so the
  default became this 120B open-weight model, the closest available
  capability tier).
- `postgres_dsn: str = "postgresql+psycopg://mtg:mtg@postgres:5432/mtg"` (the
  `+psycopg` dialect suffix is required — a bare `postgresql://` makes
  SQLAlchemy default to the `psycopg2` driver, which isn't installed).

## Infra changes

**`docker-compose.yml`:** new `postgres` service (`postgres:16`, a named
volume for data, `POSTGRES_USER`/`POSTGRES_PASSWORD`/`POSTGRES_DB` env
matching the default DSN above); `backend` gets `depends_on: postgres`
and the three new `MTG_API_*` env vars wired through (mirroring how
`MTG_API_QDRANT_HOST` etc. are already passed today).

**`mtg-api/Dockerfile` / entrypoint:** the container's startup command
runs `alembic upgrade head` before launching `uvicorn`, so `docker
compose up` remains the only setup step — no separate manual migration
command, consistent with how the rest of this stack already "just
starts."

**`mtg-api/pyproject.toml`:** add `groq`, `sqlalchemy>=2.0`, `psycopg[binary]`,
`alembic`.

## Frontend (`mtg-web`)

- `src/lib/api.ts` — `QueryResponse` gains `answer: string | null`.
- `src/routes/+page.svelte` — renders `resp.answer` (when non-null) above
  the existing results `<ul>`, which stays exactly as it is today. When
  `answer` is `null` (Groq failed), the results list still renders; no
  error banner for this case specifically (a failed generation is not a
  failed search).

## Error handling

- Groq call fails (timeout, rate limit, API error, bad key): caught in
  `main.py`, `answer=None`, `error=<message>` persisted, HTTP 200,
  results list still returned. Search keeps working even if generation
  is down.
- Postgres write fails (DB down, connection error): caught in `main.py`,
  logged, swallowed. Never affects the response — persistence is
  strictly best-effort and secondary to serving the answer.
- `GET /api/v1/queries` has no such fallback — a DB failure there is a
  genuine failure of the endpoint's only job, and propagates as a normal
  500.

## Testing plan

- `llm.py`: `build_context` formatting against a small fixed
  `QueryResult` list; `GroqAnswerer.generate` against a fake Groq client
  (asserts the system/user message shape and that the client's return
  text passes through); an exception from the fake client propagates
  unchanged.
- `history.py`: `save_history`/`list_history` against a real ephemeral
  Postgres (testcontainers), asserting round-trip of all columns
  including `jsonb` results and ordering by `created_at desc`.
- `main.py` / `POST /api/v1/query`: extend the existing `TestClient`
  suite with `get_groq_answerer`/`get_db_engine` overridden to fakes —
  cases: Groq succeeds + DB succeeds (happy path, `answer` populated);
  Groq raises (answer null, results still present, 200); DB write raises
  (response still correct, 200); confirm the existing card/vector-hit
  test cases from the prior spec still pass unchanged.
- `GET /api/v1/queries`: returns previously-saved rows in the right
  order; empty table returns `[]`.
- Alembic migration smoke test: `alembic upgrade head` runs clean against
  a fresh ephemeral Postgres.

## Out of scope

- No frontend history/review page — `GET /api/v1/queries` is JSON-only
  for now.
- No auth on either endpoint.
- No answer streaming — one blocking completion call per query.
- No pagination UI, no filtering/search over history beyond
  `limit`/`offset`.
- No retry/backoff around the Groq call — a failure is recorded and
  surfaced as `answer=null`, not retried.
- No context truncation strategy — relies on Groq's context window being
  large enough for the existing `hybrid_top_k`-bounded result set; revisit
  if that changes.

## Dependencies

`mtg-api/pyproject.toml`: add `groq`, `sqlalchemy>=2.0`,
`psycopg[binary]`, `alembic`.
