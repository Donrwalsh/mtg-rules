# Groq Answer Generation + Query History Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Groq (`openai/gpt-oss-120b`) answer-generation step to `mtg-api`'s existing `POST /api/v1/query`, and persist every query/answer/result-set to a new Postgres `query_history` table, readable back via a new `GET /api/v1/queries`.

**Architecture:** The existing card-match + hybrid-vector retrieval in `main.py` is untouched. After it produces `card_results + vector_results`, a new `llm.py` builds a text context from those results and calls Groq for a synthesized answer; a new `history.py` persists the query/answer/results/error to Postgres via SQLAlchemy Core, with an Alembic migration owning the schema. Both the Groq call and the history write are individually wrapped so a failure in either degrades gracefully (`answer: null`) rather than failing the request.

**Tech Stack:** FastAPI, `groq` (official SDK), SQLAlchemy Core 2.x, Alembic, Postgres 16, psycopg (binary), pytest, SQLite in-memory (for history tests), SvelteKit.

**Spec:** [docs/superpowers/specs/2026-09-04-groq-answer-generation-design.md](../specs/2026-09-04-groq-answer-generation-design.md)

## Global Constraints

- Groq model: `openai/gpt-oss-120b`, configurable via `MTG_API_GROQ_MODEL` (default value). Swapped in during execution from the spec's original `llama-3.3-70b-versatile` — Groq had retired that model entirely by implementation time (confirmed 404 from `GET /openai/v1/models` against a real key) — this is the closest available capability tier.
- Groq call failure -> `answer: null`, `error` recorded, HTTP 200, existing `results` still returned. Never raises past the endpoint.
- Postgres write failure -> logged, swallowed. Never affects the response, in either direction (success or failure of the Groq call).
- No auth on either endpoint. No answer streaming. No retry/backoff around the Groq call. No context truncation (relies on Groq's large context window covering the existing `hybrid_top_k`-bounded result set).
- New `MTG_API_`-prefixed settings: `groq_api_key` (secret, no safe default), `groq_model`, `postgres_dsn`.
- One row per `/api/v1/query` call in `query_history`, written after generation completes (success or failure), containing the same `results` list already returned to the caller.
- Deviation from the spec's literal column type: `results` is stored as SQLAlchemy's dialect-agnostic `JSON` (not Postgres `JSONB`), so `history.py`'s own tests can run against SQLite in-memory with no Docker/testcontainers dependency, consistent with this repo's existing "no network needed" pytest convention (see root `README.md`). This still satisfies the spec's requirement — storing the `QueryResult[]` list as queryable JSON — it just doesn't use JSONB-specific operators, which nothing in this spec needs anyway.

---

## Task 1: Groq/Postgres config settings

**Files:**
- Modify: `mtg-api/src/mtg_api/config.py`
- Test: `mtg-api/tests/test_config.py`

**Interfaces:**
- Produces: `settings.groq_api_key: str` (default `""`), `settings.groq_model: str` (default `"openai/gpt-oss-120b"`), `settings.postgres_dsn: str` (default `"postgresql+psycopg://mtg:mtg@postgres:5432/mtg"`). Every later task that touches Groq or Postgres reads these off the existing `settings` singleton.

- [ ] **Step 1: Write the failing tests**

Append to `mtg-api/tests/test_config.py`:

```python
def test_groq_defaults():
    s = Settings(_env_file=None)
    assert s.groq_api_key == ""
    assert s.groq_model == "openai/gpt-oss-120b"


def test_groq_env_override(monkeypatch):
    monkeypatch.setenv("MTG_API_GROQ_API_KEY", "test-key")
    monkeypatch.setenv("MTG_API_GROQ_MODEL", "llama-3.1-8b-instant")
    s = Settings(_env_file=None)
    assert s.groq_api_key == "test-key"
    assert s.groq_model == "llama-3.1-8b-instant"


def test_postgres_dsn_default():
    s = Settings(_env_file=None)
    assert s.postgres_dsn == "postgresql+psycopg://mtg:mtg@postgres:5432/mtg"


def test_postgres_dsn_env_override(monkeypatch):
    monkeypatch.setenv("MTG_API_POSTGRES_DSN", "postgresql://x:y@localhost:5432/z")
    s = Settings(_env_file=None)
    assert s.postgres_dsn == "postgresql://x:y@localhost:5432/z"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest mtg-api/tests/test_config.py -v`
Expected: FAIL — `AttributeError`/`ValidationError` mentioning `groq_api_key` / `postgres_dsn` not being valid fields.

- [ ] **Step 3: Add the settings**

In `mtg-api/src/mtg_api/config.py`, add three fields to the `Settings` class, after `hybrid_score_threshold`:

```python
    hybrid_score_threshold: float = 0.0
    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-120b"
    postgres_dsn: str = "postgresql+psycopg://mtg:mtg@postgres:5432/mtg"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest mtg-api/tests/test_config.py -v`
Expected: PASS, all tests including the pre-existing ones.

- [ ] **Step 5: Commit**

```bash
git add mtg-api/src/mtg_api/config.py mtg-api/tests/test_config.py
git commit -m "feat(mtg-api): add Groq and Postgres settings"
```

---

## Task 2: `history.py` persistence layer

**Files:**
- Create: `mtg-api/src/mtg_api/history.py`
- Create: `mtg-api/tests/conftest.py`
- Test: `mtg-api/tests/test_history.py`
- Modify: `mtg-api/pyproject.toml`

**Interfaces:**
- Produces: `mtg_api.history.metadata: sqlalchemy.MetaData`, `mtg_api.history.query_history: sqlalchemy.Table`, `save_history(engine, *, query: str, answer: str | None, results: list[dict], model: str, error: str | None) -> None`, `list_history(engine, *, limit: int = 50, offset: int = 0) -> list[dict]`. `tests/conftest.py`'s `memory_engine() -> sqlalchemy.engine.Engine` (schema already created) is reused by Tasks 5 and 6's tests.

- [ ] **Step 1: Add the `sqlalchemy` dependency**

In `mtg-api/pyproject.toml`, add to `dependencies` (after `"fastembed>=0.3",`):

```toml
    "sqlalchemy>=2.0",
```

`sqlalchemy` is already importable in this environment (transitive dependency), so no install step is needed to run the tests below — this just makes it an explicit, declared dependency for the Docker build.

- [ ] **Step 2: Write the shared test helper**

Create `mtg-api/tests/conftest.py`:

```python
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool

from mtg_api.history import metadata as history_metadata


def memory_engine() -> Engine:
    """A fresh in-memory SQLite engine with the query_history schema created.

    Uses StaticPool + check_same_thread=False because FastAPI runs sync path
    operations in a worker thread pool -- the default SQLite :memory: pooling
    ties a connection to the thread that created it, which would hand a
    request a different, schema-less database than the one a test set up on
    the main thread.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    history_metadata.create_all(engine)
    return engine
```

- [ ] **Step 3: Write the failing tests**

Create `mtg-api/tests/test_history.py`:

```python
from conftest import memory_engine

from mtg_api.history import list_history, save_history


def test_save_and_list_round_trips_a_row():
    engine = memory_engine()
    save_history(
        engine,
        query="how does trample work",
        answer="Trample lets excess damage carry over.",
        results=[
            {
                "source": "rule",
                "title": "702.19",
                "text": "...",
                "score": 0.9,
                "match_type": "vector_hit",
                "oracle_id": None,
            }
        ],
        model="openai/gpt-oss-120b",
        error=None,
    )
    rows = list_history(engine)
    assert len(rows) == 1
    assert rows[0]["query"] == "how does trample work"
    assert rows[0]["answer"] == "Trample lets excess damage carry over."
    assert rows[0]["results"][0]["title"] == "702.19"
    assert rows[0]["model"] == "openai/gpt-oss-120b"
    assert rows[0]["error"] is None


def test_save_history_persists_null_answer_and_error():
    engine = memory_engine()
    save_history(
        engine,
        query="what does bolt do",
        answer=None,
        results=[],
        model="openai/gpt-oss-120b",
        error="rate limited",
    )
    rows = list_history(engine)
    assert rows[0]["answer"] is None
    assert rows[0]["error"] == "rate limited"


def test_list_history_orders_newest_first():
    engine = memory_engine()
    save_history(engine, query="first", answer="a1", results=[], model="m", error=None)
    save_history(engine, query="second", answer="a2", results=[], model="m", error=None)
    rows = list_history(engine)
    assert [r["query"] for r in rows] == ["second", "first"]


def test_list_history_respects_limit_and_offset():
    engine = memory_engine()
    for i in range(3):
        save_history(engine, query=f"q{i}", answer=None, results=[], model="m", error=None)
    rows = list_history(engine, limit=1, offset=1)
    assert len(rows) == 1
    assert rows[0]["query"] == "q1"


def test_list_history_empty_table_returns_empty_list():
    assert list_history(memory_engine()) == []
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `pytest mtg-api/tests/test_history.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mtg_api.history'`.

- [ ] **Step 5: Write the implementation**

Create `mtg-api/src/mtg_api/history.py`:

```python
from __future__ import annotations

from sqlalchemy import Column, DateTime, Integer, JSON, MetaData, Table, Text, func, select
from sqlalchemy.engine import Engine

metadata = MetaData()

query_history = Table(
    "query_history",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("query", Text, nullable=False),
    Column("answer", Text, nullable=True),
    Column("results", JSON, nullable=False),
    Column("model", Text, nullable=False),
    Column("error", Text, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)


def save_history(
    engine: Engine,
    *,
    query: str,
    answer: str | None,
    results: list[dict],
    model: str,
    error: str | None,
) -> None:
    with engine.begin() as conn:
        conn.execute(
            query_history.insert().values(
                query=query,
                answer=answer,
                results=results,
                model=model,
                error=error,
            )
        )


def list_history(engine: Engine, *, limit: int = 50, offset: int = 0) -> list[dict]:
    stmt = (
        select(query_history)
        .order_by(query_history.c.created_at.desc(), query_history.c.id.desc())
        .limit(limit)
        .offset(offset)
    )
    with engine.connect() as conn:
        rows = conn.execute(stmt).mappings().all()
    return [dict(row) for row in rows]
```

`created_at.desc()` alone would tie-break arbitrarily when two rows land in the same timestamp tick (SQLite/Postgres timestamp resolution); ordering by `id.desc()` too makes "newest first" deterministic regardless of DB timestamp granularity.

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest mtg-api/tests/test_history.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add mtg-api/src/mtg_api/history.py mtg-api/tests/conftest.py mtg-api/tests/test_history.py mtg-api/pyproject.toml
git commit -m "feat(mtg-api): add query_history persistence layer"
```

---

## Task 3: `llm.py` Groq wrapper

**Files:**
- Create: `mtg-api/src/mtg_api/llm.py`
- Test: `mtg-api/tests/test_llm.py`
- Modify: `mtg-api/pyproject.toml`

**Interfaces:**
- Consumes: `mtg_api.models.QueryResult` (existing: `source`, `title`, `text`, `score`, `match_type`, `oracle_id` fields).
- Produces: `build_context(results: list[QueryResult]) -> str`, `GroqAnswerer(client, model: str)` with `.generate(query: str, context: str) -> str`, `load_groq_answerer(api_key: str, model: str) -> GroqAnswerer`. Task 5 wires `get_groq_answerer()` around `load_groq_answerer`.

- [ ] **Step 1: Add the `groq` dependency**

In `mtg-api/pyproject.toml`, add to `dependencies` (after the `"sqlalchemy>=2.0",` line added in Task 2):

```toml
    "groq>=0.11",
```

Install it locally so the real-factory smoke-import in Step 5 works: run `pip install "groq>=0.11"` (or `pip install -e "mtg-api[dev]"` from the repo root once all of this plan's dependencies have landed in `pyproject.toml`).

- [ ] **Step 2: Write the failing tests**

Create `mtg-api/tests/test_llm.py`:

```python
import pytest

from mtg_api.llm import GroqAnswerer, build_context
from mtg_api.models import QueryResult


def test_build_context_formats_each_result_as_a_block():
    results = [
        QueryResult(
            source="rule", title="702.19", text="Trample lets...", score=0.9, match_type="vector_hit"
        ),
        QueryResult(
            source="card",
            title="Craterhoof Behemoth",
            text="Trample. When...",
            score=1.0,
            match_type="card_name_match",
        ),
    ]
    context = build_context(results)
    assert "[rule] 702.19\nTrample lets..." in context
    assert "[card] Craterhoof Behemoth\nTrample. When..." in context


def test_build_context_empty_list_returns_empty_string():
    assert build_context([]) == ""


class _FakeMessage:
    def __init__(self, content):
        self.content = content


class _FakeChoice:
    def __init__(self, content):
        self.message = _FakeMessage(content)


class _FakeCompletionResponse:
    def __init__(self, content):
        self.choices = [_FakeChoice(content)]


class _FakeCompletions:
    def __init__(self, content):
        self._content = content
        self.calls = []

    def create(self, *, model, messages):
        self.calls.append({"model": model, "messages": messages})
        return _FakeCompletionResponse(self._content)


class _FakeChat:
    def __init__(self, content):
        self.completions = _FakeCompletions(content)


class _FakeGroqClient:
    def __init__(self, content):
        self.chat = _FakeChat(content)


def test_generate_returns_the_completion_text():
    client = _FakeGroqClient("Trample means excess damage carries over.")
    answerer = GroqAnswerer(client, "openai/gpt-oss-120b")
    answer = answerer.generate("how does trample work", "[rule] 702.19\nTrample text")
    assert answer == "Trample means excess damage carries over."


def test_generate_sends_system_and_user_messages_with_model():
    client = _FakeGroqClient("answer")
    answerer = GroqAnswerer(client, "openai/gpt-oss-120b")
    answerer.generate("q", "ctx")
    call = client.chat.completions.calls[0]
    assert call["model"] == "openai/gpt-oss-120b"
    assert call["messages"][0]["role"] == "system"
    assert call["messages"][1]["role"] == "user"
    assert "ctx" in call["messages"][1]["content"]
    assert "q" in call["messages"][1]["content"]


def test_generate_propagates_client_exceptions():
    class _RaisingCompletions:
        def create(self, *, model, messages):
            raise RuntimeError("rate limited")

    class _RaisingChat:
        completions = _RaisingCompletions()

    class _RaisingClient:
        chat = _RaisingChat()

    answerer = GroqAnswerer(_RaisingClient(), "openai/gpt-oss-120b")
    with pytest.raises(RuntimeError, match="rate limited"):
        answerer.generate("q", "ctx")
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest mtg-api/tests/test_llm.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mtg_api.llm'`.

- [ ] **Step 4: Write the implementation**

Create `mtg-api/src/mtg_api/llm.py`:

```python
from __future__ import annotations

from mtg_api.models import QueryResult

_SYSTEM_PROMPT = (
    "You are a Magic: The Gathering rules assistant. Answer the user's "
    "question using only the context below (card text, rulings, and "
    "Comprehensive Rules excerpts). If the context does not cover the "
    "question, say so plainly instead of guessing."
)


def build_context(results: list[QueryResult]) -> str:
    blocks = [f"[{r.source}] {r.title}\n{r.text}" for r in results]
    return "\n\n".join(blocks)


class GroqAnswerer:
    def __init__(self, client, model: str):
        self._client = client
        self._model = model

    def generate(self, query: str, context: str) -> str:
        response = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": f"Context:\n{context}\n\nQuestion: {query}"},
            ],
        )
        return response.choices[0].message.content


def load_groq_answerer(api_key: str, model: str) -> GroqAnswerer:
    """Real-client factory. Imports groq lazily so importing this module
    never requires that dependency unless this factory is actually called."""
    from groq import Groq

    return GroqAnswerer(Groq(api_key=api_key), model)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest mtg-api/tests/test_llm.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add mtg-api/src/mtg_api/llm.py mtg-api/tests/test_llm.py mtg-api/pyproject.toml
git commit -m "feat(mtg-api): add Groq answer-generation wrapper"
```

---

## Task 4: `answer` field on `QueryResponse`

**Files:**
- Modify: `mtg-api/src/mtg_api/models.py`
- Test: `mtg-api/tests/test_models.py`

**Interfaces:**
- Produces: `QueryResponse.answer: str | None = None`. Task 5 sets this on every `/api/v1/query` response.

- [ ] **Step 1: Write the failing tests**

Append to `mtg-api/tests/test_models.py`:

```python
def test_query_response_answer_defaults_to_none():
    resp = QueryResponse(query="trample", results=[])
    assert resp.answer is None


def test_query_response_holds_answer():
    resp = QueryResponse(query="trample", results=[], answer="Trample lets excess damage through.")
    assert resp.answer == "Trample lets excess damage through."
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest mtg-api/tests/test_models.py -v`
Expected: FAIL — `ValidationError`, `answer` is not a known field on `QueryResponse` (or the first test fails because `resp.answer` raises `AttributeError`).

- [ ] **Step 3: Add the field**

In `mtg-api/src/mtg_api/models.py`, change:

```python
class QueryResponse(BaseModel):
    query: str
    results: list[QueryResult]
```

to:

```python
class QueryResponse(BaseModel):
    query: str
    results: list[QueryResult]
    answer: str | None = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest mtg-api/tests/test_models.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mtg-api/src/mtg_api/models.py mtg-api/tests/test_models.py
git commit -m "feat(mtg-api): add answer field to QueryResponse"
```

---

## Task 5: Wire Groq generation + history persistence into `POST /api/v1/query`

**Files:**
- Modify: `mtg-api/src/mtg_api/main.py`
- Modify: `mtg-api/tests/test_query.py`
- Modify: `mtg-api/tests/test_lifespan.py`

**Interfaces:**
- Consumes: `mtg_api.llm.GroqAnswerer`, `load_groq_answerer` (Task 3); `mtg_api.history.save_history` (Task 2); `QueryResponse.answer` (Task 4); `tests/conftest.py`'s `memory_engine()` (Task 2).
- Produces: `main.get_db_engine() -> Engine`, `main.get_groq_answerer() -> GroqAnswerer`, both `@lru_cache`-wrapped FastAPI dependencies, overridable in tests exactly like `get_qdrant_client`. Task 6 depends on `get_db_engine` already existing.

- [ ] **Step 1: Write the failing tests**

In `mtg-api/tests/test_query.py`, replace the imports and `_override` helper:

```python
from fastapi.testclient import TestClient

from conftest import memory_engine
from mtg_api.card_matcher import CardMatcher
from mtg_api.embedder import Embedder
from mtg_api.history import list_history
from mtg_api.main import (
    app,
    get_card_matcher,
    get_db_engine,
    get_dense_embedder,
    get_groq_answerer,
    get_qdrant_client,
    get_sparse_embedder,
)
from mtg_api.sparse_embedder import SparseEmbedder
```

```python
class _FakeAnswerer:
    def __init__(self, answer="A generated answer.", raises=None):
        self._answer = answer
        self._raises = raises

    def generate(self, query, context):
        if self._raises:
            raise self._raises
        return self._answer


class _FailingEngine:
    def begin(self):
        raise RuntimeError("db unreachable")

    def connect(self):
        raise RuntimeError("db unreachable")


def _override(cards=None, dense_points=None, sparse_points=None, answerer=None, engine=None):
    app.dependency_overrides[get_card_matcher] = lambda: CardMatcher(cards or [])
    app.dependency_overrides[get_dense_embedder] = lambda: Embedder(_FakeDenseModel())
    app.dependency_overrides[get_sparse_embedder] = lambda: SparseEmbedder(_FakeSparseModel())
    app.dependency_overrides[get_qdrant_client] = lambda: _FakeQdrantClient(dense_points, sparse_points)
    app.dependency_overrides[get_groq_answerer] = lambda: answerer or _FakeAnswerer()
    app.dependency_overrides[get_db_engine] = lambda: engine or memory_engine()
```

(Everything above `_override` in the existing file — `_FakeDenseModel`, `_FakeSparseEmbedding`, `_FakeSparseModel`, `_FakeHit`, `_FakeQueryResult`, `_FakeQdrantClient` — stays exactly as-is.)

Append these new tests at the end of the file:

```python
def test_query_returns_generated_answer_on_success():
    _override(answerer=_FakeAnswerer(answer="Trample carries excess damage over."))
    try:
        resp = TestClient(app).post("/api/v1/query", json={"query": "how does trample work"})
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 200
    assert resp.json()["answer"] == "Trample carries excess damage over."


def test_query_returns_null_answer_when_groq_fails():
    _override(answerer=_FakeAnswerer(raises=RuntimeError("rate limited")))
    try:
        resp = TestClient(app).post("/api/v1/query", json={"query": "how does trample work"})
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 200
    assert resp.json()["answer"] is None
    assert resp.json()["results"] == []


def test_query_succeeds_even_when_history_write_fails():
    _override(answerer=_FakeAnswerer(answer="An answer."), engine=_FailingEngine())
    try:
        resp = TestClient(app).post("/api/v1/query", json={"query": "how does trample work"})
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 200
    assert resp.json()["answer"] == "An answer."


def test_query_persists_a_history_row():
    engine = memory_engine()
    _override(answerer=_FakeAnswerer(answer="An answer."), engine=engine)
    try:
        TestClient(app).post("/api/v1/query", json={"query": "how does trample work"})
    finally:
        app.dependency_overrides.clear()
    rows = list_history(engine)
    assert len(rows) == 1
    assert rows[0]["query"] == "how does trample work"
    assert rows[0]["answer"] == "An answer."
    assert rows[0]["error"] is None
```

In `mtg-api/tests/test_lifespan.py`, replace the whole test:

```python
import asyncio

from mtg_api.main import app, lifespan


def test_lifespan_warms_all_four_caches(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr("mtg_api.main.get_card_matcher", lambda: calls.append("card_matcher"))
    monkeypatch.setattr("mtg_api.main.get_dense_embedder", lambda: calls.append("dense_embedder"))
    monkeypatch.setattr("mtg_api.main.get_sparse_embedder", lambda: calls.append("sparse_embedder"))
    monkeypatch.setattr("mtg_api.main.get_groq_answerer", lambda: calls.append("groq_answerer"))

    async def _run():
        async with lifespan(app):
            pass

    asyncio.run(_run())

    assert set(calls) == {"card_matcher", "dense_embedder", "sparse_embedder", "groq_answerer"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest mtg-api/tests/test_query.py mtg-api/tests/test_lifespan.py -v`
Expected: FAIL — `ImportError: cannot import name 'get_db_engine' from 'mtg_api.main'` (and similarly for `get_groq_answerer`).

- [ ] **Step 3: Write the implementation**

In `mtg-api/src/mtg_api/main.py`, add imports (after the existing `from mtg_api.card_matcher import ...` block, alongside the other `mtg_api` imports):

```python
import logging

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from mtg_api.history import save_history
from mtg_api.llm import GroqAnswerer, build_context, load_groq_answerer
```

Add a module logger near the top, right after the imports:

```python
logger = logging.getLogger(__name__)
```

Add two new cached dependency providers, right after `get_sparse_embedder`:

```python
@lru_cache(maxsize=1)
def get_db_engine() -> Engine:
    return create_engine(settings.postgres_dsn)


@lru_cache(maxsize=1)
def get_groq_answerer() -> GroqAnswerer:
    return load_groq_answerer(settings.groq_api_key, settings.groq_model)
```

Update `lifespan` to also warm the Groq client:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Warm the card automaton and both models at container startup, not on
    # the first request -- moves the ~20s cold-load cost from the first
    # query to `docker compose up` instead.
    get_card_matcher()
    get_dense_embedder()
    get_sparse_embedder()
    get_groq_answerer()
    yield
```

Update the `query` endpoint's signature to accept the two new dependencies:

```python
@app.post("/api/v1/query", response_model=QueryResponse)
def query(
    request: QueryRequest,
    matcher: CardMatcher = Depends(get_card_matcher),
    dense_embedder: Embedder = Depends(get_dense_embedder),
    sparse_embedder: SparseEmbedder = Depends(get_sparse_embedder),
    client: QdrantClient = Depends(get_qdrant_client),
    answerer: GroqAnswerer = Depends(get_groq_answerer),
    engine: Engine = Depends(get_db_engine),
) -> QueryResponse:
```

Replace the function's final two lines (`return QueryResponse(query=request.query, results=card_results + vector_results)`, preceded by the `vector_results` loop) with:

```python
    all_results = card_results + vector_results
    context = build_context(all_results)
    try:
        answer = answerer.generate(request.query, context)
        error = None
    except Exception as exc:
        logger.exception("Groq answer generation failed")
        answer = None
        error = str(exc)

    try:
        save_history(
            engine,
            query=request.query,
            answer=answer,
            results=[r.model_dump() for r in all_results],
            model=settings.groq_model,
            error=error,
        )
    except Exception:
        logger.exception("Failed to persist query history")

    return QueryResponse(query=request.query, results=all_results, answer=answer)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest mtg-api/tests -v`
Expected: PASS — every test in the package, including all pre-existing `test_query.py` cases (they still call `_override()` with no new args and get working fake defaults for the two new dependencies).

- [ ] **Step 5: Commit**

```bash
git add mtg-api/src/mtg_api/main.py mtg-api/tests/test_query.py mtg-api/tests/test_lifespan.py
git commit -m "feat(mtg-api): generate a Groq answer and persist query history on /api/v1/query"
```

---

## Task 6: `GET /api/v1/queries` history endpoint

**Files:**
- Modify: `mtg-api/src/mtg_api/main.py`
- Create: `mtg-api/tests/test_queries_endpoint.py`

**Interfaces:**
- Consumes: `mtg_api.history.list_history` (Task 2), `main.get_db_engine` (Task 5), `tests/conftest.py`'s `memory_engine()` (Task 2).
- Produces: `GET /api/v1/queries?limit=&offset=` returning `list[dict]` (each dict shaped like a `query_history` row).

- [ ] **Step 1: Write the failing tests**

Create `mtg-api/tests/test_queries_endpoint.py`:

```python
from fastapi.testclient import TestClient

from conftest import memory_engine
from mtg_api.history import save_history
from mtg_api.main import app, get_db_engine


def test_returns_empty_list_when_no_history():
    engine = memory_engine()
    app.dependency_overrides[get_db_engine] = lambda: engine
    try:
        resp = TestClient(app).get("/api/v1/queries")
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 200
    assert resp.json() == []


def test_returns_saved_rows_newest_first():
    engine = memory_engine()
    save_history(engine, query="first", answer="a1", results=[], model="m", error=None)
    save_history(engine, query="second", answer="a2", results=[], model="m", error=None)
    app.dependency_overrides[get_db_engine] = lambda: engine
    try:
        resp = TestClient(app).get("/api/v1/queries")
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 200
    body = resp.json()
    assert [row["query"] for row in body] == ["second", "first"]


def test_respects_limit_and_offset_query_params():
    engine = memory_engine()
    for i in range(3):
        save_history(engine, query=f"q{i}", answer=None, results=[], model="m", error=None)
    app.dependency_overrides[get_db_engine] = lambda: engine
    try:
        resp = TestClient(app).get("/api/v1/queries?limit=1&offset=1")
    finally:
        app.dependency_overrides.clear()
    body = resp.json()
    assert len(body) == 1
    assert body[0]["query"] == "q1"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest mtg-api/tests/test_queries_endpoint.py -v`
Expected: FAIL with `404 Not Found` (the route doesn't exist yet).

- [ ] **Step 3: Write the implementation**

In `mtg-api/src/mtg_api/main.py`, change the `history` import to include `list_history`:

```python
from mtg_api.history import list_history, save_history
```

Append a new endpoint at the end of the file (after `get_task_status`):

```python
@app.get("/api/v1/queries")
def get_query_history(
    limit: int = 50,
    offset: int = 0,
    engine: Engine = Depends(get_db_engine),
) -> list[dict]:
    return list_history(engine, limit=limit, offset=offset)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest mtg-api/tests -v`
Expected: PASS — full suite.

- [ ] **Step 5: Commit**

```bash
git add mtg-api/src/mtg_api/main.py mtg-api/tests/test_queries_endpoint.py
git commit -m "feat(mtg-api): add GET /api/v1/queries history endpoint"
```

---

## Task 7: Alembic migration for `query_history`

**Files:**
- Create: `mtg-api/alembic.ini`
- Create: `mtg-api/alembic/env.py`
- Create: `mtg-api/alembic/script.py.mako`
- Create: `mtg-api/alembic/versions/0001_create_query_history.py`
- Modify: `mtg-api/pyproject.toml`

**Interfaces:**
- Consumes: `mtg_api.history.metadata` (Task 2), `mtg_api.config.settings.postgres_dsn` (Task 1).
- Produces: a real Postgres `query_history` table matching `history.py`'s definition, applied via `alembic upgrade head`. Task 8's Dockerfile change runs this at container startup; this task's own verification runs it manually against compose's Postgres.

- [ ] **Step 1: Add `alembic` and `psycopg[binary]` dependencies**

In `mtg-api/pyproject.toml`, add to `dependencies` (after the `"groq>=0.11",` line added in Task 3):

```toml
    "alembic>=1.13",
    "psycopg[binary]>=3.1",
```

Install locally: `pip install "alembic>=1.13" "psycopg[binary]>=3.1"`.

- [ ] **Step 2: Write `alembic.ini`**

Create `mtg-api/alembic.ini`:

```ini
[alembic]
script_location = alembic
prepend_sys_path = .

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARN
handlers = console
qualname =

[logger_sqlalchemy]
level = WARN
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
datefmt = %H:%M:%S
```

- [ ] **Step 3: Write `alembic/env.py`**

Create `mtg-api/alembic/env.py`:

```python
from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from mtg_api.config import settings
from mtg_api.history import metadata

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", settings.postgres_dsn)
target_metadata = metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

- [ ] **Step 4: Write `alembic/script.py.mako`**

Create `mtg-api/alembic/script.py.mako`:

```mako
"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}

"""
from alembic import op
import sqlalchemy as sa
${imports if imports else ""}

# revision identifiers, used by Alembic.
revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
branch_labels = ${repr(branch_labels)}
depends_on = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
```

- [ ] **Step 5: Write the migration**

Create `mtg-api/alembic/versions/0001_create_query_history.py`:

```python
"""create query_history table

Revision ID: 0001
Revises:
Create Date: 2026-09-04

"""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "query_history",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("results", sa.JSON(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("query_history")
```

- [ ] **Step 6: Commit**

```bash
git add mtg-api/alembic.ini mtg-api/alembic mtg-api/pyproject.toml
git commit -m "feat(mtg-api): add Alembic migration for query_history"
```

*(Migration correctness against a real Postgres is verified end-to-end in Task 9's final verification step, once the `postgres` compose service and the Dockerfile's `alembic upgrade head` startup step both exist — running it in isolation here would just repeat that same check twice.)*

---

## Task 8: Regenerate `requirements.lock`, run migration at container startup

**Files:**
- Modify: `mtg-api/requirements.lock`
- Modify: `mtg-api/Dockerfile`

**Interfaces:**
- Consumes: the final `mtg-api/pyproject.toml` dependency list (Tasks 2, 3, 7 all added entries to it).

- [ ] **Step 1: Regenerate the lock file**

From the repo root (requires `uv`; install with `pip install uv` if it's not already on PATH):

```bash
uv pip compile --python-version 3.12 mtg-api/pyproject.toml -o mtg-api/requirements.lock
```

Confirm `groq`, `sqlalchemy`, `alembic`, and `psycopg` (or `psycopg-binary`) now appear in `mtg-api/requirements.lock`.

- [ ] **Step 2: Update the Dockerfile to run the migration before starting the app**

In `mtg-api/Dockerfile`, the final stage currently ends with:

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY --from=builder /app/src ./src
ENV PYTHONPATH=/app/src
EXPOSE 8000
CMD ["uvicorn", "mtg_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

Replace it with:

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY --from=builder /app/src ./src
COPY alembic.ini ./
COPY alembic ./alembic
ENV PYTHONPATH=/app/src
EXPOSE 8000
CMD alembic upgrade head && uvicorn mtg_api.main:app --host 0.0.0.0 --port 8000
```

Switching from exec-form (`CMD [...]`) to shell-form (`CMD alembic ... && uvicorn ...`) is required to chain the two commands — shell-form runs under `/bin/sh -c`, which understands `&&`.

- [ ] **Step 3: Commit**

```bash
git add mtg-api/requirements.lock mtg-api/Dockerfile
git commit -m "build(mtg-api): run Alembic migrations at container startup"
```

*(Verified together with Task 9's compose changes — the migration needs a real `postgres` service and the `MTG_API_POSTGRES_DSN` env var to actually run.)*

---

## Task 9: `docker-compose.yml` Postgres service + `.env.example`

**Files:**
- Modify: `docker-compose.yml`
- Modify: `.env.example`

**Interfaces:**
- Consumes: `mtg_api.config.settings.postgres_dsn`/`groq_api_key`/`groq_model` (Task 1), the migrated `query_history` schema (Tasks 7-8).

- [ ] **Step 1: Add the `postgres` service**

In `docker-compose.yml`, add a new service after `redis:` and before `worker:`:

```yaml
  postgres:
    image: postgres:16
    environment:
      POSTGRES_USER: mtg
      POSTGRES_PASSWORD: mtg
      POSTGRES_DB: mtg
    ports:
      - "5433:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data
```

(Host port `5433`, not `5432` — this dev machine already has an unrelated `postgres_db` container bound to host `5432`. The container-to-container DSN below is unaffected either way; it addresses the service by name on the compose network.)

- [ ] **Step 2: Wire the `backend` service to it**

In `docker-compose.yml`'s `backend` service, add to `environment` (after `MTG_API_PARSED_DIR: /app/data/parsed`):

```yaml
      MTG_API_GROQ_API_KEY: ${MTG_API_GROQ_API_KEY:?Set MTG_API_GROQ_API_KEY in your .env file}
      MTG_API_GROQ_MODEL: openai/gpt-oss-120b
      MTG_API_POSTGRES_DSN: postgresql+psycopg://mtg:mtg@postgres:5432/mtg
```

and add `- postgres` to `backend`'s `depends_on` list.

- [ ] **Step 3: Add the named volume**

In `docker-compose.yml`'s top-level `volumes:` section, add:

```yaml
  postgres_data:
```

- [ ] **Step 4: Document the new required env var**

In `.env.example`, add:

```
MTG_API_GROQ_API_KEY=
```

- [ ] **Step 5: Verify end to end**

This is the combined smoke check for Tasks 7, 8, and 9 together — the migration, the Dockerfile startup step, and the compose wiring all need each other to prove out.

Copy `.env.example` to `.env` and fill in a real `MTG_API_GROQ_API_KEY`, then:

```bash
docker compose up --build
```

Confirm the `backend` container's logs show Alembic applying `0001_create_query_history` before uvicorn's startup line. Then:

```bash
docker compose exec postgres psql -U mtg -d mtg -c '\d query_history'
```

Confirm all six columns (`id`, `query`, `answer`, `results`, `model`, `error`, `created_at` — seven, including `id`) are listed. Then, with Qdrant already seeded per the existing "Seed the data" quick-start steps in `README.md`:

```bash
curl -X POST localhost:8000/api/v1/query \
  -H 'Content-Type: application/json' \
  -d '{"query": "how does trample work"}'
```

Confirm the JSON response has a non-null `"answer"` field. Then:

```bash
curl localhost:8000/api/v1/queries
```

Confirm the row from the previous request comes back.

- [ ] **Step 6: Commit**

```bash
git add docker-compose.yml .env.example
git commit -m "feat: add Postgres service for query history storage"
```

---

## Task 10: Frontend — render the generated answer

**Files:**
- Modify: `mtg-web/src/lib/api.ts`
- Modify: `mtg-web/src/routes/+page.svelte`

**Interfaces:**
- Consumes: `QueryResponse.answer` as returned by `POST /api/v1/query` (Task 5).

- [ ] **Step 1: Add `answer` to the `QueryResponse` type**

In `mtg-web/src/lib/api.ts`, change:

```ts
export interface QueryResponse {
  query: string;
  results: QueryResult[];
}
```

to:

```ts
export interface QueryResponse {
  query: string;
  results: QueryResult[];
  answer: string | null;
}
```

- [ ] **Step 2: Render the answer above the results list**

In `mtg-web/src/routes/+page.svelte`, replace the whole file with:

```svelte
<script lang="ts">
  import { submitQuery, type QueryResult } from '$lib/api';

  let query = '';
  let results: QueryResult[] = [];
  let answer: string | null = null;
  let error = '';

  async function onSubmit() {
    error = '';
    try {
      const resp = await submitQuery(query);
      results = resp.results;
      answer = resp.answer;
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

  {#if answer}
    <div class="answer">
      <h2>Answer</h2>
      <p>{answer}</p>
    </div>
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

- [ ] **Step 3: Verify in the browser**

With the full stack from Task 9's verification still running (`docker compose up`), open `http://localhost:3000`, submit a rules question, and confirm an "Answer" section renders above the existing results list with Groq's generated text.

- [ ] **Step 4: Commit**

```bash
git add mtg-web/src/lib/api.ts mtg-web/src/routes/+page.svelte
git commit -m "feat(mtg-web): render the generated answer above search results"
```

---

## Task 11: Update `README.md`

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Update the architecture diagram**

Replace:

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

with:

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
        Qdrant (6333)      |         |  redis    |  postgres (5432)
        vector store       |         |  (Celery  |  query_history
                           v         |   broker) |  (query/answer/
                      card matcher   |   + worker|   results, via Groq)
                      (Aho-Corasick) |           v
                                     |      celery tasks:
                                     v      mtg_worker.ingest -> mtg-ingestion
                              Groq API      mtg_worker.embed  -> mtg-embed
                              (openai/gpt-oss-120b)
```

- [ ] **Step 2: Update the Components table**

Replace:

```
| Directory | What it is |
|---|---|
| `mtg-web/` | SvelteKit SPA frontend. One page with a search box that POSTs to the backend and lists results. Static build served by nginx on port 3000. |
| `mtg-api/` | FastAPI backend. Query endpoint, Celery task triggers, task status. Port 8000. |
| `mtg-worker/` | Celery worker. Registers `mtg_worker.ingest` and `mtg_worker.embed`, which delegate to the two packages below. |
| `mtg-worker/mtg-ingestion/` | Fetch + parse stage. Pulls the Comprehensive Rules, Scryfall `oracle_cards` and `rulings` bulk data, writes JSONL to `data/parsed/`. |
| `mtg-worker/mtg-embed/` | Embedding stage. Reads parsed JSONL, embeds chunks (dense + sparse), upserts into the Qdrant `mtg_rules` collection. |
| `docs/superpowers/` | Design specs and subagent-driven-development records. |
```

with:

```
| Directory | What it is |
|---|---|
| `mtg-web/` | SvelteKit SPA frontend. One page with a search box that POSTs to the backend and shows the generated answer plus the retrieved results. Static build served by nginx on port 3000. |
| `mtg-api/` | FastAPI backend. Query endpoint (retrieval + Groq answer generation), history endpoint, Celery task triggers, task status. Port 8000. |
| `mtg-worker/` | Celery worker. Registers `mtg_worker.ingest` and `mtg_worker.embed`, which delegate to the two packages below. |
| `mtg-worker/mtg-ingestion/` | Fetch + parse stage. Pulls the Comprehensive Rules, Scryfall `oracle_cards` and `rulings` bulk data, writes JSONL to `data/parsed/`. |
| `mtg-worker/mtg-embed/` | Embedding stage. Reads parsed JSONL, embeds chunks (dense + sparse), upserts into the Qdrant `mtg_rules` collection. |
| `postgres` (Docker service) | Stores `query_history`: one row per `/api/v1/query` call (query, generated answer, retrieved results, error), managed by Alembic migrations in `mtg-api/alembic/`. |
| `docs/superpowers/` | Design specs and subagent-driven-development records. |
```

- [ ] **Step 3: Update the Data flow section**

Replace point 3:

```
3. **Query** — `POST /api/v1/query` finds exact card names in the query with an
   Aho-Corasick `CardMatcher`, embeds the query with both models, runs
   independent dense and sparse Qdrant searches, normalizes and fuses the two
   score lists (weighted sum), and returns card matches plus top vector hits.
```

with:

```
3. **Query** — `POST /api/v1/query` finds exact card names in the query with an
   Aho-Corasick `CardMatcher`, embeds the query with both models, runs
   independent dense and sparse Qdrant searches, normalizes and fuses the two
   score lists (weighted sum), builds a text context from the card matches
   plus top vector hits, and sends that context and the query to Groq
   (`openai/gpt-oss-120b`) for a synthesized answer. The query, the
   generated answer (or `null` if Groq failed), the retrieved results, and
   any error are persisted as one row in Postgres's `query_history` table,
   then the same data is returned to the caller.
4. **Review** — `GET /api/v1/queries` reads `query_history` back out
   (`?limit=&offset=`, newest first) for reviewing past operations.
```

- [ ] **Step 4: Update the Backend API table**

Replace:

```
| Endpoint | Method | Body | Description |
|---|---|---|---|
| `/health` | GET | — | Service health + Qdrant reachability |
| `/api/v1/query` | POST | `{"query": str}` | Hybrid search results |
| `/api/v1/ingest` | POST | — | Trigger `mtg_worker.ingest`; returns `{"task_id"}` |
| `/api/v1/embed` | POST | `{"limit": "all" \| int}` | Trigger `mtg_worker.embed`; returns `{"task_id"}` |
| `/api/v1/tasks/{id}` | GET | — | Celery task status (+ result when ready) |
```

with:

```
| Endpoint | Method | Body | Description |
|---|---|---|---|
| `/health` | GET | — | Service health + Qdrant reachability |
| `/api/v1/query` | POST | `{"query": str}` | Hybrid search results plus a Groq-generated answer (`null` if generation failed); persists a `query_history` row |
| `/api/v1/queries` | GET | — | Past query/answer/result records, newest first (`?limit=&offset=`, default `limit=50, offset=0`) |
| `/api/v1/ingest` | POST | — | Trigger `mtg_worker.ingest`; returns `{"task_id"}` |
| `/api/v1/embed` | POST | `{"limit": "all" \| int}` | Trigger `mtg_worker.embed`; returns `{"task_id"}` |
| `/api/v1/tasks/{id}` | GET | — | Celery task status (+ result when ready) |
```

- [ ] **Step 5: Update the Configuration table**

After the existing table row `| MTG_API_BROKER_URL / MTG_API_RESULT_BACKEND | redis://redis:6379/0 | mtg-api, mtg-worker |`, add three rows:

```
| `MTG_API_GROQ_API_KEY` | *(required, no default)* | mtg-api |
| `MTG_API_GROQ_MODEL` | `openai/gpt-oss-120b` | mtg-api |
| `MTG_API_POSTGRES_DSN` | `postgresql+psycopg://mtg:mtg@postgres:5432/mtg` | mtg-api |
```

- [ ] **Step 5: Re-read the file for consistency**

Read the whole updated `README.md` back and confirm the architecture diagram, components table, data flow, API table, and config table are all mutually consistent with the shipped code (no stale "no answer synthesis" language left over from before this feature).

- [ ] **Step 6: Commit**

```bash
git add README.md
git commit -m "docs: document Groq answer generation and query history"
```
