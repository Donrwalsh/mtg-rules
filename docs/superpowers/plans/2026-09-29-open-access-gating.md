# Open Access with Cost Gating Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Open the production site to anyone while holding Gemini spend to about $1 per UTC day, with per-IP quotas, a labelled answer cache, and a hidden admin login.

**Architecture:** nginx rejects floods before they reach the backend. The FastAPI query endpoint records the real token usage of every answer in a Postgres `llm_usage` table and checks per-IP counts and today's total spend before each Gemini call. Over a limit, it answers retrieval-only instead of failing. Successful answers are cached in `answer_cache`, keyed on the normalized question, prompt version, settings and a data-version marker that `deploy/sync_data.py` writes. An HMAC-signed admin cookie (ported from the Connections app) unlocks history, a usage panel, forced fresh answers, and exemption from all limits.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy Core, Alembic, pytest; SvelteKit 2 / Svelte 4 (static adapter), nginx; Docker Compose on Coolify.

**Spec:** [docs/superpowers/specs/2026-09-29-open-access-gating-design.md](../specs/2026-09-29-open-access-gating-design.md)

## Global Constraints

- Global cap: **$1.00 per UTC day** (`MTG_API_DAILY_BUDGET_USD`, default `1.0`). Check before each call and halt once today's recorded spend is ≥ the cap. No worst-case reservation.
- Per-IP LLM quotas: **20 answers per UTC day, ≤ 5 per 10 minutes**. IPv6 is bucketed by /64. Cache hits don't count.
- nginx: **1 r/s, burst 5, 2 concurrent** per address on `/api/`; **5 r/min** on `/api/v1/auth/login`; status 429.
- Queries longer than **500 characters** → 422.
- Production generation: `thinkingLevel` **low**, `maxOutputTokens` **2048**.
- The admin is exempt from per-IP limits and the global cap, but their usage is still recorded and counted in spend.
- Usage rows (with raw IP buckets) are kept indefinitely for now. There is no purge job.
- No CAPTCHA, no push alerts, no block list.
- Eval mode (`MTG_API_EVAL_MODE=true`, never in production) bypasses gating, the cache and usage recording. `source: "eval"` only means anything in eval mode.
- No new Python or npm dependencies.
- Python: ruff line length 100; `ruff check .` and `ruff format --check .` must pass in every touched package (CI lints `mtg-api`, `evals`, `deploy`).
- Frontend has no test runner; `npm run build` must pass, and UI changes are checked by hand in the dev stack.
- Commits: conventional prefixes (`feat:`, `fix:`, `docs:`, `test:`), ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Branch: `feature/open-access` (already created; the spec is committed there).

## File Structure

**Backend (`mtg-api/src/mtg_api/`)**
- `config.py`: new settings, `generation_thinking_level` in the override lists, and `generator_label()` includes the thinking level.
- `llm.py`: `Generation` result dataclass; `GeminiAnswerer` sends `thinkingConfig` and returns token usage.
- `usage.py` (new): the `llm_usage` table, IP bucketing, cost, recording, spend and quota queries, the gate, the usage summary, and the gating config check.
- `answer_cache.py` (new): the `answer_cache` table, query normalization, cache key, get/put, and reading the data version.
- `admin_auth.py` (new): session signing, `is_admin`, the `require_admin` dependency, and the `/api/v1/auth` router.
- `history.py`: `cached` column.
- `models.py`: `QueryRequest.fresh`; `QueryResponse.cached_at`, `degraded`, `answers_remaining`, `usage`.
- `main.py`: wires everything into `/api/v1/query`, gates `/api/v1/queries`, adds `/api/v1/admin/usage`, and extends the lifespan.
- `alembic/versions/0003_add_usage_and_answer_cache.py` (new).

**Tests (`mtg-api/tests/`)**: `test_config.py`, `test_llm.py`, `test_migrations.py`, `test_usage.py` (new), `test_answer_cache.py` (new), `test_admin_auth.py` (new), `test_gating_flow.py` (new), `test_admin_usage.py` (new), plus updates to `conftest.py`, `test_query.py`, `test_eval_mode.py`, `test_queries_endpoint.py`, `test_lifespan.py`.

**Deploy**: `deploy/sync_data.py` + `deploy/test_sync_data.py` (data_version marker).

**Frontend (`mtg-web/`)**: `nginx.conf`; `src/lib/api.ts`; `src/lib/admin.ts` (new); `src/routes/+layout.svelte` (new); `src/routes/+page.svelte`; `src/routes/login/+page.svelte` (new); `src/routes/admin/usage/+page.svelte` (new); `src/routes/history/+page.svelte`.

**Evals**: `evals/src/mtg_evals/runner.py`, `evals/tests/fakes.py`, `evals/tests/test_runner.py`, `evals/experiments/thinking-low.yaml` (new).

**Ops/docs**: `docker-compose.yml`, `docker-compose.prod.yml`, `.env.example`, `README.md`. Last task only: `mtg-web/40-basic-auth.sh`, `mtg-web/Dockerfile`.

Run backend tests from `mtg-api/`: `pytest` (or `python -m pytest`). Run lint from the same directory: `ruff check . && ruff format --check .`.

---

### Task 1: Settings for gating, cache, admin and thinking

**Files:**
- Modify: `mtg-api/src/mtg_api/config.py`
- Test: `mtg-api/tests/test_config.py`

**Interfaces:**
- Produces: `Settings` fields `generation_thinking_level: str | None = None`, `max_query_chars: int = 500`, `gating_enabled: bool = False`, `daily_budget_usd: float = 1.0`, `gemini_input_price_per_mtok: float = 0.0`, `gemini_output_price_per_mtok: float = 0.0`, `ip_daily_llm_limit: int = 20`, `ip_window_llm_limit: int = 5`, `ip_window_minutes: int = 10`, `answer_cache_enabled: bool = True`, `admin_password: SecretStr = SecretStr("")`. `"generation_thinking_level"` added to `OVERRIDABLE_SETTINGS` and `GENERATION_SETTINGS`. `generator_label(s)` returns `"gemini:<model>"`, or `"gemini:<model>:think=<level>"` when a level is set.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_config.py`)

```python
from mtg_api.config import GENERATION_SETTINGS, OVERRIDABLE_SETTINGS


def test_gating_defaults():
    s = Settings(_env_file=None)
    assert s.gating_enabled is False
    assert s.daily_budget_usd == 1.0
    assert s.gemini_input_price_per_mtok == 0.0
    assert s.gemini_output_price_per_mtok == 0.0
    assert s.ip_daily_llm_limit == 20
    assert s.ip_window_llm_limit == 5
    assert s.ip_window_minutes == 10
    assert s.max_query_chars == 500
    assert s.answer_cache_enabled is True
    assert s.admin_password.get_secret_value() == ""
    assert s.generation_thinking_level is None


def test_thinking_level_is_an_overridable_generation_setting():
    assert "generation_thinking_level" in OVERRIDABLE_SETTINGS
    assert "generation_thinking_level" in GENERATION_SETTINGS


def test_generator_label_without_thinking_level_is_unchanged():
    s = Settings(_env_file=None, gemini_model="gemini-3.5-flash")
    assert generator_label(s) == "gemini:gemini-3.5-flash"


def test_generator_label_includes_thinking_level():
    s = Settings(_env_file=None, gemini_model="gemini-3.5-flash", generation_thinking_level="low")
    assert generator_label(s) == "gemini:gemini-3.5-flash:think=low"
```

- [ ] **Step 2: Run to verify failure**

Run (in `mtg-api/`): `pytest tests/test_config.py -v`
Expected: FAIL (`AttributeError`/`ImportError` on the new fields).

- [ ] **Step 3: Implement**

In `config.py`, after `generation_max_tokens`:

```python
    # Gemini 3.x thinkingConfig.thinkingLevel ("low", "high", ...). None
    # means "don't send it": the model's default thinking applies.
    generation_thinking_level: str | None = None
```

After `task_endpoints`:

```python
    # Longest question accepted by POST /api/v1/query (422 above it).
    max_query_chars: int = 500
    # Per-IP quotas and the global daily cap on Gemini spend. Off in dev;
    # production turns it on (and must then set both prices).
    gating_enabled: bool = False
    daily_budget_usd: float = 1.0
    # USD per million tokens; thinking tokens bill as output.
    gemini_input_price_per_mtok: float = 0.0
    gemini_output_price_per_mtok: float = 0.0
    ip_daily_llm_limit: int = 20
    ip_window_llm_limit: int = 5
    ip_window_minutes: int = 10
    # Reuse answers to identical questions. Always bypassed in eval mode.
    answer_cache_enabled: bool = True
    # Unlocks the admin login. Empty disables it.
    admin_password: SecretStr = SecretStr("")
```

Add `"generation_thinking_level",` to the end of `OVERRIDABLE_SETTINGS`, and change `GENERATION_SETTINGS` to:

```python
GENERATION_SETTINGS: frozenset[str] = frozenset(
    {
        "gemini_model",
        "generation_temperature",
        "generation_max_tokens",
        "generation_thinking_level",
    }
)
```

Replace `generator_label`:

```python
def generator_label(s: Settings) -> str:
    """Provider-qualified model, e.g. "gemini:gemini-3.5-flash", plus the
    thinking level when one is set. Eval answer caches and the production
    answer cache are keyed on it."""
    label = f"gemini:{s.gemini_model}"
    if s.generation_thinking_level:
        label += f":think={s.generation_thinking_level}"
    return label
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_config.py tests/test_eval_mode.py -v`
Expected: PASS. `test_eval_fields_present_in_eval_mode` still passes because the default level is `None`.

- [ ] **Step 5: Commit**

```bash
git add mtg-api/src/mtg_api/config.py mtg-api/tests/test_config.py
git commit -m "feat(api): settings for gating, answer cache, admin and thinking level"
```

---

### Task 2: Gemini thinking level and token usage

**Files:**
- Modify: `mtg-api/src/mtg_api/llm.py`, `mtg-api/src/mtg_api/main.py` (`build_answerer`, the `generate` call site, eval fields), `mtg-api/src/mtg_api/models.py`
- Test: `mtg-api/tests/test_llm.py`, `mtg-api/tests/test_query.py` (fake), `mtg-api/tests/test_eval_mode.py` (fake + new test)

**Interfaces:**
- Consumes: `Settings.generation_thinking_level` (Task 1).
- Produces: `llm.Generation` (frozen dataclass: `text: str`, `input_tokens: int = 0`, `output_tokens: int = 0`, `thinking_tokens: int = 0`, method `usage() -> dict[str, int]`). `GeminiAnswerer(..., thinking_level: str | None = None)`. `GeminiAnswerer.generate(query, context) -> Generation`. `QueryResponse.usage: dict[str, int] | None` (eval mode only).

- [ ] **Step 1: Write the failing tests**

In `tests/test_llm.py`, change the import to also bring `Generation`, and update the existing assertions that compare `generate(...)` to a string to use `.text`:
- `test_gemini_answerer_posts_generate_content`: `assert answer.text == "Trample carries over [1]."`
- `test_gemini_answerer_skips_thought_parts`: `assert GeminiAnswerer("k", "m").generate("q", "ctx").text == "Yes [1]."`

Append:

```python
def test_gemini_answerer_sends_thinking_level(monkeypatch):
    sent = _capture_gemini_request(monkeypatch)
    GeminiAnswerer("k", "m", max_tokens=2048, thinking_level="low").generate("q", "ctx")
    assert sent["json"]["generationConfig"] == {
        "maxOutputTokens": 2048,
        "thinkingConfig": {"thinkingLevel": "low"},
    }


def test_gemini_answerer_returns_token_usage(monkeypatch):
    _capture_gemini_request(
        monkeypatch,
        {
            "candidates": [{"content": {"parts": [{"text": "ok"}]}}],
            "usageMetadata": {
                "promptTokenCount": 1200,
                "candidatesTokenCount": 150,
                "thoughtsTokenCount": 300,
                "totalTokenCount": 1650,
            },
        },
    )
    result = GeminiAnswerer("k", "m").generate("q", "ctx")
    assert result == Generation(
        text="ok", input_tokens=1200, output_tokens=150, thinking_tokens=300
    )
    assert result.usage() == {"input_tokens": 1200, "output_tokens": 150, "thinking_tokens": 300}


def test_gemini_answerer_usage_defaults_to_zero(monkeypatch):
    _capture_gemini_request(monkeypatch)  # no usageMetadata
    result = GeminiAnswerer("k", "m").generate("q", "ctx")
    assert (result.input_tokens, result.output_tokens, result.thinking_tokens) == (0, 0, 0)
```

In `tests/test_query.py`, update the fake answerer to return a `Generation`:

```python
from mtg_api.llm import Generation

class _FakeAnswerer:
    def __init__(self, answer="A generated answer.", raises=None):
        self._answer = answer
        self._raises = raises

    def generate(self, query, context):
        if self._raises:
            raise self._raises
        return Generation(
            text=self._answer, input_tokens=1000, output_tokens=100, thinking_tokens=200
        )
```

In `tests/test_eval_mode.py`, make `_RecordingAnswerer.generate` return `Generation(text="An answer.")` (import `Generation` from `mtg_api.llm`), and append:

```python
def test_eval_fields_include_token_usage(eval_mode):
    _override()
    body = _post({"query": "q"}).json()
    assert body["usage"] == {"input_tokens": 1000, "output_tokens": 100, "thinking_tokens": 200}


def test_usage_is_null_outside_eval_mode():
    _override()
    assert _post({"query": "q"}).json()["usage"] is None


def test_build_answerer_passes_the_thinking_level():
    s = main.settings.model_copy(
        update={"gemini_api_key": SecretStr("k"), "generation_thinking_level": "low"}
    )
    assert main.build_answerer(s)._thinking_level == "low"
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_llm.py tests/test_eval_mode.py tests/test_query.py -v`
Expected: FAIL (`ImportError: Generation`, then `AttributeError: 'str' object has no attribute 'text'` and similar).

- [ ] **Step 3: Implement**

In `llm.py`, add at the top (after the imports) `from dataclasses import dataclass` and:

```python
@dataclass(frozen=True)
class Generation:
    """An answer and what it cost. Thinking tokens bill as output tokens."""

    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    thinking_tokens: int = 0

    def usage(self) -> dict[str, int]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "thinking_tokens": self.thinking_tokens,
        }
```

In `GeminiAnswerer.__init__`, add a `thinking_level: str | None = None` parameter (after `max_tokens`) and `self._thinking_level = thinking_level`. In `generate`, change the return annotation to `-> Generation`, add after the `maxOutputTokens` block:

```python
        if self._thinking_level is not None:
            config["thinkingConfig"] = {"thinkingLevel": self._thinking_level}
```

and replace the final `return` with:

```python
        parts = candidates[0].get("content", {}).get("parts", [])
        usage = data.get("usageMetadata") or {}
        # Skip thought-summary parts; keep only the answer text.
        return Generation(
            text="".join(p.get("text", "") for p in parts if not p.get("thought")),
            input_tokens=usage.get("promptTokenCount", 0),
            output_tokens=usage.get("candidatesTokenCount", 0),
            thinking_tokens=usage.get("thoughtsTokenCount", 0),
        )
```

In `models.py`, add to `QueryResponse` after `generator`:

```python
    # Eval mode only: this request's Gemini token counts.
    usage: dict[str, int] | None = None
```

In `main.py`:
- `build_answerer`: pass `thinking_level=s.generation_thinking_level,`.
- The generation block becomes:

```python
    answer = None
    error = None
    generation = None
    if request.generate:
        try:
            generation = answerer.generate(request.query, context)
            answer = generation.text
        except Exception as exc:
            logger.exception("Answer generation failed (%s)", generator_label(s))
            error = str(exc)
```

- In the `eval_fields` dict, add `"usage": generation.usage() if generation else None,`.

- [ ] **Step 4: Run the whole suite**

Run: `pytest`
Expected: PASS. Fix any other test that fakes `generate()` with a bare string; `grep -rn "def generate" tests/` finds them.

- [ ] **Step 5: Confirm the API parameter against the real Gemini API**

This costs a fraction of a cent. From the repo root, with `MTG_API_GEMINI_API_KEY` taken from `.env`:

```bash
KEY=$(grep '^MTG_API_GEMINI_API_KEY=' .env | cut -d= -f2-)
curl -s "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash:generateContent" \
  -H "x-goog-api-key: $KEY" -H "Content-Type: application/json" \
  -d '{"contents":[{"role":"user","parts":[{"text":"What is trample? One sentence."}]}],
       "generationConfig":{"maxOutputTokens":2048,"thinkingConfig":{"thinkingLevel":"low"}}}' \
  | python -c "import json,sys; d=json.load(sys.stdin); print(d.get('error') or d['usageMetadata'])"
```

Expected: a `usageMetadata` dict with `promptTokenCount`, `candidatesTokenCount` and (usually) `thoughtsTokenCount`, and no `error`. If the API rejects `thinkingLevel`, check https://ai.google.dev/gemini-api/docs/thinking for the gemini-3.5-flash parameter name, fix `llm.py` and the test, and note the change in the commit message.

- [ ] **Step 6: Commit**

```bash
git add mtg-api/src/mtg_api/llm.py mtg-api/src/mtg_api/main.py mtg-api/src/mtg_api/models.py mtg-api/tests
git commit -m "feat(api): send a thinking level to Gemini and return token usage"
```

---

### Task 3: Schema: `llm_usage`, `answer_cache`, `query_history.cached`

**Files:**
- Create: `mtg-api/src/mtg_api/usage.py` (table only for now), `mtg-api/src/mtg_api/answer_cache.py` (table only for now), `mtg-api/alembic/versions/0003_add_usage_and_answer_cache.py`
- Modify: `mtg-api/src/mtg_api/history.py`, `mtg-api/tests/conftest.py`
- Test: `mtg-api/tests/test_migrations.py`, `mtg-api/tests/test_history.py`

**Interfaces:**
- Produces: `usage.llm_usage` and `answer_cache.answer_cache` SQLAlchemy `Table`s on the shared `history.metadata`. `save_history(..., cached: bool = False)`. `conftest.memory_engine()` creates all three tables.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_migrations.py`:

```python
def _tables(dsn: str) -> set[str]:
    engine = create_engine(dsn)
    try:
        return set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def test_0003_adds_usage_cache_and_cached_flag(tmp_path, monkeypatch):
    dsn = f"sqlite:///{(tmp_path / 'history.db').as_posix()}"
    monkeypatch.setattr(settings, "postgres_dsn", dsn)
    cfg = _config()

    command.upgrade(cfg, "0002")
    assert not {"llm_usage", "answer_cache"} & _tables(dsn)
    assert "cached" not in _columns(dsn)

    command.upgrade(cfg, "head")
    assert {"llm_usage", "answer_cache"} <= _tables(dsn)
    assert "cached" in _columns(dsn)

    command.downgrade(cfg, "0002")
    assert not {"llm_usage", "answer_cache"} & _tables(dsn)
    assert "cached" not in _columns(dsn)
```

Append to `tests/test_history.py` (it already imports `memory_engine`, `save_history` and `list_history`; add any that are missing):

```python
def test_cached_flag_defaults_false_and_round_trips():
    engine = memory_engine()
    save_history(engine, query="a", answer="x", results=[], model="m", error=None)
    save_history(engine, query="b", answer="x", results=[], model="m", error=None, cached=True)
    rows = {row["query"]: row["cached"] for row in list_history(engine)}
    assert rows == {"a": False, "b": True}


def test_memory_engine_creates_usage_and_cache_tables():
    from sqlalchemy import inspect

    names = set(inspect(memory_engine()).get_table_names())
    assert {"query_history", "llm_usage", "answer_cache"} <= names
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_migrations.py tests/test_history.py -v`
Expected: FAIL (no revision 0003; `save_history` has no `cached` argument).

- [ ] **Step 3: Implement**

`history.py`: add `Boolean` to the sqlalchemy import, add the column before `created_at`:

```python
    Column("cached", Boolean, nullable=False, server_default="0"),
```

and add a `cached: bool = False` keyword to `save_history`, passing `cached=cached` in `.values(...)`.

`src/mtg_api/usage.py`:

```python
"""Gemini usage accounting and the per-IP / global gate in front of it."""

from __future__ import annotations

from sqlalchemy import Boolean, Column, DateTime, Float, Index, Integer, Table, Text

from mtg_api.history import metadata

# One row per /api/v1/query call that asked for an answer (outside eval mode).
llm_usage = Table(
    "llm_usage",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    # Set explicitly (UTC) by the app, never by the database, so quota
    # windows compare like with like on Postgres and SQLite.
    Column("created_at", DateTime(timezone=True), nullable=False),
    # IPv4 address, or IPv6 /64 in CIDR form.
    Column("ip_bucket", Text, nullable=False),
    Column("is_admin", Boolean, nullable=False),
    # generated | cached | degraded_ip | degraded_global | error
    Column("outcome", Text, nullable=False),
    Column("model", Text, nullable=False),
    Column("input_tokens", Integer, nullable=False),
    Column("output_tokens", Integer, nullable=False),
    Column("thinking_tokens", Integer, nullable=False),
    Column("cost_usd", Float, nullable=False),
    Index("ix_llm_usage_created_at", "created_at"),
    Index("ix_llm_usage_ip_bucket_created_at", "ip_bucket", "created_at"),
)
```

`src/mtg_api/answer_cache.py`:

```python
"""Reuse of generated answers for repeated questions."""

from __future__ import annotations

from sqlalchemy import JSON, Column, DateTime, Integer, Table, Text

from mtg_api.history import metadata

answer_cache = Table(
    "answer_cache",
    metadata,
    # sha256 of the normalized question, prompt version, settings and data version.
    Column("key", Text, primary_key=True),
    Column("normalized_query", Text, nullable=False),
    # The whole QueryResponse, so [n] citations stay tied to their sources.
    Column("response", JSON, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("hit_count", Integer, nullable=False, server_default="0"),
)
```

`alembic/versions/0003_add_usage_and_answer_cache.py`:

```python
"""add llm_usage, answer_cache and query_history.cached

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-29

"""

import sqlalchemy as sa

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "query_history",
        sa.Column("cached", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_table(
        "llm_usage",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ip_bucket", sa.Text(), nullable=False),
        sa.Column("is_admin", sa.Boolean(), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("thinking_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Float(), nullable=False),
    )
    op.create_index("ix_llm_usage_created_at", "llm_usage", ["created_at"])
    op.create_index(
        "ix_llm_usage_ip_bucket_created_at", "llm_usage", ["ip_bucket", "created_at"]
    )
    op.create_table(
        "answer_cache",
        sa.Column("key", sa.Text(), primary_key=True),
        sa.Column("normalized_query", sa.Text(), nullable=False),
        sa.Column("response", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("hit_count", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_table("answer_cache")
    op.drop_index("ix_llm_usage_ip_bucket_created_at", table_name="llm_usage")
    op.drop_index("ix_llm_usage_created_at", table_name="llm_usage")
    op.drop_table("llm_usage")
    # Batch mode so the downgrade also runs on SQLite (the migration test).
    with op.batch_alter_table("query_history") as batch:
        batch.drop_column("cached")
```

`tests/conftest.py`: make sure the new tables register on the shared metadata before `create_all`. Add these imports under the existing one:

```python
import mtg_api.answer_cache  # noqa: F401 -- registers answer_cache on the metadata
import mtg_api.usage  # noqa: F401 -- registers llm_usage on the metadata
```

- [ ] **Step 4: Run tests**

Run: `pytest`
Expected: PASS (including the existing `test_0002_...`, which now downgrades through 0003).

- [ ] **Step 5: Commit**

```bash
git add mtg-api/src/mtg_api/usage.py mtg-api/src/mtg_api/answer_cache.py mtg-api/src/mtg_api/history.py mtg-api/alembic/versions/0003_add_usage_and_answer_cache.py mtg-api/tests
git commit -m "feat(api): schema for LLM usage, the answer cache and cached history rows"
```

---

### Task 4: Usage accounting and the gate

**Files:**
- Modify: `mtg-api/src/mtg_api/usage.py`
- Test: `mtg-api/tests/test_usage.py` (new)

**Interfaces:**
- Consumes: `llm_usage` (Task 3), `Generation` (Task 2), `Settings` (Task 1).
- Produces (all in `mtg_api.usage`):
  - `ANSWER_OUTCOMES = ("generated", "error")`
  - `as_utc(dt: datetime) -> datetime`: a naive value (SQLite) is taken as UTC.
  - `day_start(now: datetime) -> datetime`: UTC midnight of `now`'s day.
  - `ip_bucket(host: str) -> str`
  - `cost_usd(generation: Generation, s: Settings) -> float`
  - `record_usage(engine, *, now, ip_bucket, is_admin, outcome, model, generation=None, cost=0.0) -> None`
  - `spend_since(engine, since: datetime) -> float`
  - `answers_since(engine, bucket: str, since: datetime) -> int`: non-admin rows with an `ANSWER_OUTCOMES` outcome.
  - `Gate` (frozen dataclass: `degraded: str | None`, `answers_remaining: int`)
  - `check_gate(engine, s, bucket, now) -> Gate`: `degraded` is `None`, `"ip_quota"` or `"global_budget"`.
  - `usage_summary(engine, s, now, *, days=7, top=10) -> dict` (shape below).
  - `check_gating_config(s: Settings) -> None`: raises `RuntimeError` when gating is on and a price is ≤ 0.

- [ ] **Step 1: Write the failing tests** (`tests/test_usage.py`)

```python
from datetime import UTC, datetime, timedelta

import pytest
from conftest import memory_engine

from mtg_api.config import Settings
from mtg_api.llm import Generation
from mtg_api.usage import (
    Gate,
    answers_since,
    check_gate,
    check_gating_config,
    cost_usd,
    day_start,
    ip_bucket,
    record_usage,
    spend_since,
    usage_summary,
)

NOW = datetime(2026, 9, 29, 15, 0, tzinfo=UTC)


def _settings(**kw):
    base = {
        "gemini_input_price_per_mtok": 1.0,
        "gemini_output_price_per_mtok": 2.0,
        "daily_budget_usd": 1.0,
        "ip_daily_llm_limit": 3,
        "ip_window_llm_limit": 2,
        "ip_window_minutes": 10,
    }
    return Settings(_env_file=None, **{**base, **kw})


def _record(engine, *, at=NOW, bucket="203.0.113.7", outcome="generated", cost=0.0, admin=False):
    record_usage(
        engine,
        now=at,
        ip_bucket=bucket,
        is_admin=admin,
        outcome=outcome,
        model="gemini-3.5-flash",
        cost=cost,
    )


def test_ip_bucket_keeps_ipv4():
    assert ip_bucket("203.0.113.7") == "203.0.113.7"


def test_ip_bucket_groups_ipv6_by_64():
    assert ip_bucket("2001:db8:1:2:aaaa::1") == "2001:db8:1:2::/64"
    assert ip_bucket("2001:db8:1:2:ffff::9") == "2001:db8:1:2::/64"


def test_ip_bucket_unwraps_ipv4_mapped_ipv6():
    assert ip_bucket("::ffff:203.0.113.7") == "203.0.113.7"


def test_ip_bucket_passes_through_non_ip_hosts():
    # Starlette's TestClient reports its host as "testclient".
    assert ip_bucket("testclient") == "testclient"


def test_cost_bills_thinking_as_output():
    g = Generation("x", input_tokens=1_000_000, output_tokens=100_000, thinking_tokens=150_000)
    assert cost_usd(g, _settings()) == pytest.approx(1.0 + 0.5)


def test_day_start_is_utc_midnight():
    assert day_start(NOW) == datetime(2026, 9, 29, tzinfo=UTC)


def test_record_usage_stores_token_counts():
    engine = memory_engine()
    g = Generation("x", input_tokens=10, output_tokens=2, thinking_tokens=3)
    record_usage(
        engine,
        now=NOW,
        ip_bucket="b",
        is_admin=False,
        outcome="generated",
        model="m",
        generation=g,
        cost=0.25,
    )
    assert spend_since(engine, day_start(NOW)) == pytest.approx(0.25)


def test_spend_since_ignores_earlier_rows_and_counts_admin():
    engine = memory_engine()
    _record(engine, at=NOW - timedelta(days=1), cost=5.0)
    _record(engine, cost=0.25)
    _record(engine, cost=0.5, admin=True)
    assert spend_since(engine, day_start(NOW)) == pytest.approx(0.75)


def test_answers_since_counts_generated_and_errors_only_for_the_bucket():
    engine = memory_engine()
    _record(engine, outcome="generated")
    _record(engine, outcome="error")
    _record(engine, outcome="cached")
    _record(engine, outcome="degraded_ip")
    _record(engine, outcome="generated", bucket="other")
    _record(engine, outcome="generated", admin=True)
    assert answers_since(engine, "203.0.113.7", day_start(NOW)) == 2


def test_gate_open_reports_remaining():
    engine = memory_engine()
    _record(engine, at=NOW - timedelta(hours=2))
    assert check_gate(engine, _settings(), "203.0.113.7", NOW) == Gate(None, 2)


def test_gate_closes_on_global_budget_first():
    engine = memory_engine()
    _record(engine, bucket="someone-else", cost=1.0)
    assert check_gate(engine, _settings(), "203.0.113.7", NOW) == Gate("global_budget", 3)


def test_gate_closes_on_daily_ip_quota():
    engine = memory_engine()
    for hours in (5, 4, 3):
        _record(engine, at=NOW - timedelta(hours=hours))
    assert check_gate(engine, _settings(), "203.0.113.7", NOW) == Gate("ip_quota", 0)


def test_gate_closes_on_window_quota_with_answers_left_today():
    engine = memory_engine()
    _record(engine, at=NOW - timedelta(minutes=3))
    _record(engine, at=NOW - timedelta(minutes=1))
    assert check_gate(engine, _settings(), "203.0.113.7", NOW) == Gate("ip_quota", 1)


def test_gate_ignores_yesterdays_answers():
    engine = memory_engine()
    for _ in range(3):
        _record(engine, at=NOW - timedelta(days=1))
    assert check_gate(engine, _settings(), "203.0.113.7", NOW) == Gate(None, 3)


def test_usage_summary_shape():
    engine = memory_engine()
    _record(engine, at=NOW - timedelta(days=2), cost=0.1)
    _record(engine, cost=0.2)
    _record(engine, outcome="cached")
    _record(engine, outcome="degraded_ip", bucket="198.51.100.1")
    summary = usage_summary(engine, _settings(), NOW)

    assert summary["budget_usd"] == 1.0
    assert [d["date"] for d in summary["days"]][-1] == "2026-09-29"
    assert len(summary["days"]) == 7
    today = summary["days"][-1]
    assert today["spend_usd"] == pytest.approx(0.2)
    assert today["outcomes"] == {"generated": 1, "cached": 1, "degraded_ip": 1}
    assert summary["days"][-3]["spend_usd"] == pytest.approx(0.1)
    assert summary["cache_hit_rate"] == pytest.approx(0.5)
    top = summary["top_ip_buckets"]
    assert top[0] == {
        "ip_bucket": "203.0.113.7",
        "requests": 2,
        "answers": 1,
        "spend_usd": pytest.approx(0.2),
    }
    assert top[1]["ip_bucket"] == "198.51.100.1"


def test_usage_summary_cache_hit_rate_is_none_without_answers():
    assert usage_summary(memory_engine(), _settings(), NOW)["cache_hit_rate"] is None


def test_check_gating_config_requires_prices_when_enabled():
    check_gating_config(Settings(_env_file=None))  # gating off: fine
    with pytest.raises(RuntimeError, match="PRICE"):
        check_gating_config(Settings(_env_file=None, gating_enabled=True))
    check_gating_config(_settings(gating_enabled=True))
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_usage.py -v`
Expected: FAIL (`ImportError`).

- [ ] **Step 3: Implement** (append to `usage.py`; extend its imports)

Replace the import block with:

```python
from __future__ import annotations

import ipaddress
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    Index,
    Integer,
    Table,
    Text,
    func,
    select,
)
from sqlalchemy.engine import Engine

from mtg_api.config import Settings
from mtg_api.history import metadata
from mtg_api.llm import Generation
```

Append after the table:

```python
# Outcomes that called Gemini, and so use up a visitor's quota.
ANSWER_OUTCOMES = ("generated", "error")


def as_utc(dt: datetime) -> datetime:
    """SQLite hands back naive datetimes; every value here is stored as UTC."""
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def day_start(now: datetime) -> datetime:
    return now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


def ip_bucket(host: str) -> str:
    """The unit quotas count against: an IPv4 address, or an IPv6 /64 (one
    subscriber usually holds a whole /64). Anything else passes through."""
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return host
    if addr.version == 6:
        if addr.ipv4_mapped:
            return str(addr.ipv4_mapped)
        return str(ipaddress.ip_network(f"{addr}/64", strict=False))
    return str(addr)


def cost_usd(generation: Generation, s: Settings) -> float:
    output = generation.output_tokens + generation.thinking_tokens
    return (
        generation.input_tokens * s.gemini_input_price_per_mtok
        + output * s.gemini_output_price_per_mtok
    ) / 1_000_000


def record_usage(
    engine: Engine,
    *,
    now: datetime,
    ip_bucket: str,
    is_admin: bool,
    outcome: str,
    model: str,
    generation: Generation | None = None,
    cost: float = 0.0,
) -> None:
    g = generation or Generation("")
    with engine.begin() as conn:
        conn.execute(
            llm_usage.insert().values(
                created_at=now,
                ip_bucket=ip_bucket,
                is_admin=is_admin,
                outcome=outcome,
                model=model,
                input_tokens=g.input_tokens,
                output_tokens=g.output_tokens,
                thinking_tokens=g.thinking_tokens,
                cost_usd=cost,
            )
        )


def spend_since(engine: Engine, since: datetime) -> float:
    stmt = select(func.coalesce(func.sum(llm_usage.c.cost_usd), 0.0)).where(
        llm_usage.c.created_at >= since
    )
    with engine.connect() as conn:
        return float(conn.execute(stmt).scalar_one())


def answers_since(engine: Engine, bucket: str, since: datetime) -> int:
    stmt = select(func.count()).where(
        llm_usage.c.ip_bucket == bucket,
        llm_usage.c.created_at >= since,
        llm_usage.c.is_admin.is_(False),
        llm_usage.c.outcome.in_(ANSWER_OUTCOMES),
    )
    with engine.connect() as conn:
        return int(conn.execute(stmt).scalar_one())


@dataclass(frozen=True)
class Gate:
    degraded: str | None  # None | "ip_quota" | "global_budget"
    answers_remaining: int


def check_gate(engine: Engine, s: Settings, bucket: str, now: datetime) -> Gate:
    """Whether this visitor may have an LLM answer now. The global cap is
    checked first so its message wins when both apply."""
    today = day_start(now)
    used_today = answers_since(engine, bucket, today)
    remaining = max(0, s.ip_daily_llm_limit - used_today)
    if spend_since(engine, today) >= s.daily_budget_usd:
        return Gate("global_budget", remaining)
    if used_today >= s.ip_daily_llm_limit:
        return Gate("ip_quota", 0)
    window_start = now - timedelta(minutes=s.ip_window_minutes)
    if answers_since(engine, bucket, window_start) >= s.ip_window_llm_limit:
        return Gate("ip_quota", remaining)
    return Gate(None, remaining)


def usage_summary(
    engine: Engine, s: Settings, now: datetime, *, days: int = 7, top: int = 10
) -> dict:
    """The admin usage panel: spend and outcomes per UTC day (oldest first,
    today last), today's cache hit rate and today's busiest IP buckets.
    Aggregated in Python: a week of rows is small, and it keeps the SQL
    portable between Postgres and the SQLite tests."""
    today = day_start(now)
    first = today - timedelta(days=days - 1)
    with engine.connect() as conn:
        rows = conn.execute(select(llm_usage).where(llm_usage.c.created_at >= first)).mappings()
        rows = [dict(r) for r in rows]

    per_day = {
        (first + timedelta(days=i)).date(): {"spend_usd": 0.0, "outcomes": Counter()}
        for i in range(days)
    }
    buckets: dict[str, dict] = {}
    for row in rows:
        created = as_utc(row["created_at"])
        day = per_day.get(created.date())
        if day is None:
            continue
        day["spend_usd"] += row["cost_usd"]
        day["outcomes"][row["outcome"]] += 1
        if created >= today:
            b = buckets.setdefault(
                row["ip_bucket"],
                {"ip_bucket": row["ip_bucket"], "requests": 0, "answers": 0, "spend_usd": 0.0},
            )
            b["requests"] += 1
            b["answers"] += row["outcome"] in ANSWER_OUTCOMES
            b["spend_usd"] += row["cost_usd"]

    today_outcomes = per_day[today.date()]["outcomes"]
    asked = today_outcomes["cached"] + sum(today_outcomes[o] for o in ANSWER_OUTCOMES)
    return {
        "budget_usd": s.daily_budget_usd,
        "days": [
            {
                "date": date.isoformat(),
                "spend_usd": round(day["spend_usd"], 6),
                "outcomes": dict(day["outcomes"]),
            }
            for date, day in per_day.items()
        ],
        "cache_hit_rate": today_outcomes["cached"] / asked if asked else None,
        "top_ip_buckets": sorted(buckets.values(), key=lambda b: -b["requests"])[:top],
    }


def check_gating_config(s: Settings) -> None:
    """Gating counts dollars, so it needs both prices. Fail at startup, not
    by silently treating every answer as free."""
    if s.gating_enabled and (
        s.gemini_input_price_per_mtok <= 0 or s.gemini_output_price_per_mtok <= 0
    ):
        raise RuntimeError(
            "MTG_API_GATING_ENABLED needs MTG_API_GEMINI_INPUT_PRICE_PER_MTOK and "
            "MTG_API_GEMINI_OUTPUT_PRICE_PER_MTOK"
        )
```

- [ ] **Step 4: Run tests and lint**

Run: `pytest tests/test_usage.py -v && ruff check . && ruff format --check .`
Expected: PASS. If `llm.py` → `usage.py` → `config.py` creates an import cycle, it won't: `llm.py` imports only `models`.

- [ ] **Step 5: Commit**

```bash
git add mtg-api/src/mtg_api/usage.py mtg-api/tests/test_usage.py
git commit -m "feat(api): record Gemini usage and gate answers on per-IP quotas and a daily cap"
```

---

### Task 5: Answer cache module and data version

**Files:**
- Modify: `mtg-api/src/mtg_api/answer_cache.py`
- Test: `mtg-api/tests/test_answer_cache.py` (new)

**Interfaces:**
- Consumes: `answer_cache` table (Task 3), `as_utc` (Task 4), `OVERRIDABLE_SETTINGS` (Task 1), `PROMPT_VERSION` (`llm.py`).
- Produces (in `mtg_api.answer_cache`):
  - `DATA_VERSION_FILE = "data_version"`
  - `normalize_query(query: str) -> str`
  - `cache_key(query: str, s: Settings, data_version: str) -> str`
  - `get_cached(engine, key) -> tuple[dict, datetime] | None` (bumps `hit_count`)
  - `put_cached(engine, key, *, normalized_query: str, response: dict, now: datetime) -> None` (replaces any existing entry)
  - `read_data_version(parsed_dir: Path) -> str`

- [ ] **Step 1: Write the failing tests** (`tests/test_answer_cache.py`)

```python
from datetime import UTC, datetime

from conftest import memory_engine

from mtg_api.answer_cache import (
    DATA_VERSION_FILE,
    cache_key,
    get_cached,
    normalize_query,
    put_cached,
    read_data_version,
)
from mtg_api.config import Settings

NOW = datetime(2026, 9, 29, 15, 0, tzinfo=UTC)


def test_normalize_query_folds_case_space_and_trailing_punctuation():
    assert normalize_query("  How does   TRAMPLE work?? ") == "how does trample work"
    assert normalize_query("Deathtouch + trample.") == "deathtouch + trample"


def test_cache_key_is_stable_across_normalization():
    s = Settings(_env_file=None)
    assert cache_key("Trample?", s, "v1") == cache_key("  trample ", s, "v1")


def test_cache_key_changes_with_data_version_and_settings():
    s = Settings(_env_file=None)
    base = cache_key("trample", s, "v1")
    assert cache_key("trample", s, "v2") != base
    assert cache_key("trample", s.model_copy(update={"hybrid_top_k": 3}), "v1") != base
    thinking = s.model_copy(update={"generation_thinking_level": "low"})
    assert cache_key("trample", thinking, "v1") != base


def test_put_then_get_round_trips_and_counts_hits():
    engine = memory_engine()
    put_cached(engine, "k", normalized_query="trample", response={"answer": "Yes [1]."}, now=NOW)
    assert get_cached(engine, "k") == ({"answer": "Yes [1]."}, NOW)
    get_cached(engine, "k")
    with engine.connect() as conn:
        from mtg_api.answer_cache import answer_cache

        assert conn.execute(answer_cache.select()).mappings().one()["hit_count"] == 2


def test_get_cached_misses():
    assert get_cached(memory_engine(), "nope") is None


def test_put_cached_replaces_an_existing_entry():
    engine = memory_engine()
    put_cached(engine, "k", normalized_query="q", response={"answer": "old"}, now=NOW)
    put_cached(engine, "k", normalized_query="q", response={"answer": "new"}, now=NOW)
    assert get_cached(engine, "k")[0] == {"answer": "new"}


def test_read_data_version_prefers_the_marker(tmp_path):
    (tmp_path / "rules_2026-08-25.jsonl").write_text("")
    (tmp_path / DATA_VERSION_FILE).write_text("2026-09-29T15:00:00+00:00\n")
    assert read_data_version(tmp_path) == "2026-09-29T15:00:00+00:00"


def test_read_data_version_falls_back_to_latest_file_names(tmp_path):
    for name in ("cards_2026-09-01.jsonl", "cards_2026-09-26.jsonl", "rules_2026-08-25.jsonl"):
        (tmp_path / name).write_text("")
    assert read_data_version(tmp_path) == "cards_2026-09-26.jsonl|rules_2026-08-25.jsonl"


def test_read_data_version_without_any_files(tmp_path):
    assert read_data_version(tmp_path / "missing") == "none|none"
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_answer_cache.py -v`
Expected: FAIL (`ImportError`).

- [ ] **Step 3: Implement** (`answer_cache.py`, full file)

```python
"""Reuse of generated answers for repeated questions."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

from sqlalchemy import JSON, Column, DateTime, Integer, Table, Text, select
from sqlalchemy.engine import Engine

from mtg_api.config import OVERRIDABLE_SETTINGS, Settings
from mtg_api.history import metadata
from mtg_api.llm import PROMPT_VERSION
from mtg_api.usage import as_utc

# Written by deploy/sync_data.py next to the parsed JSONL; any sync is a new
# data version, so no cached answer outlives a rules, card or ruling update.
DATA_VERSION_FILE = "data_version"

answer_cache = Table(
    "answer_cache",
    metadata,
    # sha256 of the normalized question, prompt version, settings and data version.
    Column("key", Text, primary_key=True),
    Column("normalized_query", Text, nullable=False),
    # The whole QueryResponse, so [n] citations stay tied to their sources.
    Column("response", JSON, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("hit_count", Integer, nullable=False, server_default="0"),
)


def normalize_query(query: str) -> str:
    return " ".join(query.lower().split()).rstrip("?!. ")


def cache_key(query: str, s: Settings, data_version: str) -> str:
    # Every overridable setting shapes either the context or the answer.
    parts = {
        "query": normalize_query(query),
        "prompt_version": PROMPT_VERSION,
        "data_version": data_version,
        "settings": {name: getattr(s, name) for name in OVERRIDABLE_SETTINGS},
    }
    encoded = json.dumps(parts, sort_keys=True, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def get_cached(engine: Engine, key: str) -> tuple[dict, datetime] | None:
    with engine.begin() as conn:
        row = conn.execute(
            select(answer_cache.c.response, answer_cache.c.created_at).where(
                answer_cache.c.key == key
            )
        ).first()
        if row is None:
            return None
        conn.execute(
            answer_cache.update()
            .where(answer_cache.c.key == key)
            .values(hit_count=answer_cache.c.hit_count + 1)
        )
    return row.response, as_utc(row.created_at)


def put_cached(
    engine: Engine, key: str, *, normalized_query: str, response: dict, now: datetime
) -> None:
    # Delete + insert rather than a dialect-specific upsert: an admin "fresh"
    # answer replaces the old entry.
    with engine.begin() as conn:
        conn.execute(answer_cache.delete().where(answer_cache.c.key == key))
        conn.execute(
            answer_cache.insert().values(
                key=key,
                normalized_query=normalized_query,
                response=response,
                created_at=now,
                hit_count=0,
            )
        )


def read_data_version(parsed_dir: Path) -> str:
    marker = parsed_dir / DATA_VERSION_FILE
    if marker.is_file():
        return marker.read_text(encoding="utf-8").strip()
    # Dev stack (no sync): the latest parsed files stand in for the version.
    names = []
    for kind in ("cards", "rules"):
        matches = sorted(parsed_dir.glob(f"{kind}_*.jsonl")) if parsed_dir.is_dir() else []
        names.append(matches[-1].name if matches else "none")
    return "|".join(names)
```

(The table definition is unchanged from Task 3; only the imports and functions are new.)

- [ ] **Step 4: Run tests and lint**

Run: `pytest tests/test_answer_cache.py -v && ruff check . && ruff format --check .`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mtg-api/src/mtg_api/answer_cache.py mtg-api/tests/test_answer_cache.py
git commit -m "feat(api): answer cache keyed on question, settings and data version"
```

---

### Task 6: Admin auth (port of Connections) and admin-only history

**Files:**
- Create: `mtg-api/src/mtg_api/admin_auth.py`
- Modify: `mtg-api/src/mtg_api/main.py` (include the router; gate `/api/v1/queries`), `mtg-api/tests/conftest.py`, `docker-compose.yml`, `.env.example`
- Test: `mtg-api/tests/test_admin_auth.py` (new), `mtg-api/tests/test_queries_endpoint.py`

**Interfaces:**
- Consumes: `Settings.admin_password` (Task 1).
- Produces (in `mtg_api.admin_auth`): `ADMIN_SESSION_COOKIE = "admin_session"`, `ADMIN_REQUEST_HEADER = "x-admin-request"`, `SESSION_MAX_AGE_SECONDS`, `session_secret(password: str) -> bytes`, `sign_session(secret: bytes, now: float | None = None) -> str`, `verify_session(value: str | None, secret: bytes, now: float | None = None) -> bool`, `is_admin(request: Request) -> bool`, `has_admin_marker(request: Request) -> bool`, `require_admin(request: Request) -> None` (FastAPI dependency: 401 with no valid cookie, 403 with no marker header), and `router` (`POST /api/v1/auth/login` `{password}`, `POST /api/v1/auth/logout`, `GET /api/v1/auth/me` → `{"is_admin": bool}`).
- Produces (in `tests/conftest.py`): `admin_client(monkeypatch, password="pw") -> TestClient`, logged in, sending `X-Admin-Request: 1` on every request.

- [ ] **Step 1: Write the failing tests**

Append to `tests/conftest.py`:

```python
def admin_client(monkeypatch, password: str = "pw"):
    """A TestClient logged in as the admin, sending the X-Admin-Request
    marker on every request."""
    from fastapi.testclient import TestClient
    from pydantic import SecretStr

    from mtg_api.config import settings
    from mtg_api.main import app

    monkeypatch.setattr(settings, "admin_password", SecretStr(password))
    client = TestClient(app, headers={"X-Admin-Request": "1"})
    assert client.post("/api/v1/auth/login", json={"password": password}).status_code == 200
    return client
```

`tests/test_admin_auth.py`:

```python
import pytest
from conftest import admin_client, memory_engine
from fastapi.testclient import TestClient
from pydantic import SecretStr

from mtg_api.admin_auth import (
    ADMIN_SESSION_COOKIE,
    session_secret,
    sign_session,
    verify_session,
)
from mtg_api.config import settings
from mtg_api.main import app, get_db_engine

SECRET = session_secret("pw")


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def password(monkeypatch):
    monkeypatch.setattr(settings, "admin_password", SecretStr("pw"))


def test_session_round_trips():
    assert verify_session(sign_session(SECRET, now=1000.0), SECRET, now=1001.0)


def test_session_expires():
    value = sign_session(SECRET, now=1000.0)
    assert not verify_session(value, SECRET, now=1000.0 + 91 * 24 * 3600)


@pytest.mark.parametrize("value", [None, "", "garbage", "123.", ".abc", "12x.abc"])
def test_malformed_sessions_are_rejected(value):
    assert not verify_session(value, SECRET, now=0.0)


def test_session_signed_with_another_password_is_rejected():
    assert not verify_session(sign_session(session_secret("other"), now=0.0), SECRET, now=1.0)


def test_tampered_expiry_is_rejected():
    expires, sig = sign_session(SECRET, now=0.0).split(".")
    assert not verify_session(f"{int(expires) + 1}.{sig}", SECRET, now=1.0)


def test_login_with_wrong_password_is_403(password):
    resp = TestClient(app).post("/api/v1/auth/login", json={"password": "nope"})
    assert resp.status_code == 403


def test_login_is_disabled_without_a_configured_password(monkeypatch):
    monkeypatch.setattr(settings, "admin_password", SecretStr(""))
    resp = TestClient(app).post("/api/v1/auth/login", json={"password": ""})
    assert resp.status_code == 403


def test_login_sets_a_strict_httponly_cookie(password):
    resp = TestClient(app).post("/api/v1/auth/login", json={"password": "pw"})
    assert resp.status_code == 200
    cookie = resp.headers["set-cookie"].lower()
    assert cookie.startswith(f"{ADMIN_SESSION_COOKIE}=")
    assert "httponly" in cookie
    assert "samesite=strict" in cookie


def test_me_reflects_login_and_logout(password):
    client = TestClient(app)
    assert client.get("/api/v1/auth/me").json() == {"is_admin": False}
    client.post("/api/v1/auth/login", json={"password": "pw"})
    assert client.get("/api/v1/auth/me").json() == {"is_admin": True}
    client.post("/api/v1/auth/logout")
    assert client.get("/api/v1/auth/me").json() == {"is_admin": False}


def test_history_requires_login(password):
    app.dependency_overrides[get_db_engine] = memory_engine
    assert TestClient(app).get("/api/v1/queries").status_code == 401


def test_history_requires_the_marker_header(password):
    app.dependency_overrides[get_db_engine] = memory_engine
    client = TestClient(app)
    client.post("/api/v1/auth/login", json={"password": "pw"})
    assert client.get("/api/v1/queries").status_code == 403


def test_history_with_admin_session(monkeypatch):
    app.dependency_overrides[get_db_engine] = memory_engine
    assert admin_client(monkeypatch).get("/api/v1/queries").status_code == 200
```

In `tests/test_queries_endpoint.py`: import `admin_client` from `conftest`, give every test a `monkeypatch` parameter, and replace each `TestClient(app).get(...)` with `admin_client(monkeypatch).get(...)`.

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_admin_auth.py tests/test_queries_endpoint.py -v`
Expected: FAIL (`ModuleNotFoundError: mtg_api.admin_auth`).

- [ ] **Step 3: Implement**

`src/mtg_api/admin_auth.py`:

```python
"""The site's one admin identity, gated by MTG_API_ADMIN_PASSWORD.

Ported from the Connections app: an HMAC-signed session cookie
("<expiresAt>.<hex signature>") keyed on sha256("admin:" + password), so
there is no separate secret to configure and changing the password logs
every session out. Cookie-authenticated admin calls also need an
X-Admin-Request: 1 header, as defense in depth alongside SameSite=Strict.
"""

from __future__ import annotations

import hashlib
import hmac
import time

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from mtg_api.config import settings

ADMIN_SESSION_COOKIE = "admin_session"
ADMIN_REQUEST_HEADER = "x-admin-request"
# 90 days: log in once per device.
SESSION_MAX_AGE_SECONDS = 90 * 24 * 60 * 60


def session_secret(password: str) -> bytes:
    return hashlib.sha256(f"admin:{password}".encode()).digest()


def _signature(secret: bytes, expires_at: str) -> str:
    return hmac.new(secret, expires_at.encode(), hashlib.sha256).hexdigest()


def sign_session(secret: bytes, now: float | None = None) -> str:
    expires_at = str(int((time.time() if now is None else now) + SESSION_MAX_AGE_SECONDS))
    return f"{expires_at}.{_signature(secret, expires_at)}"


def verify_session(value: str | None, secret: bytes, now: float | None = None) -> bool:
    if not value:
        return False
    expires_at, _, signature = value.partition(".")
    if not expires_at.isdigit() or not signature:
        return False
    if int(expires_at) < (time.time() if now is None else now):
        return False
    return hmac.compare_digest(_signature(secret, expires_at), signature)


def _password() -> str:
    return settings.admin_password.get_secret_value()


def is_admin(request: Request) -> bool:
    password = _password()
    return bool(password) and verify_session(
        request.cookies.get(ADMIN_SESSION_COOKIE), session_secret(password)
    )


def has_admin_marker(request: Request) -> bool:
    return request.headers.get(ADMIN_REQUEST_HEADER) == "1"


def require_admin(request: Request) -> None:
    if not is_admin(request):
        raise HTTPException(status_code=401, detail="admin login required")
    if not has_admin_marker(request):
        raise HTTPException(status_code=403, detail="missing X-Admin-Request header")


router = APIRouter(prefix="/api/v1/auth")


class LoginRequest(BaseModel):
    password: str


@router.post("/login")
def login(body: LoginRequest, request: Request, response: Response) -> dict:
    password = _password()
    if not password or not hmac.compare_digest(body.password.encode(), password.encode()):
        raise HTTPException(status_code=403, detail="Incorrect password.")
    response.set_cookie(
        ADMIN_SESSION_COOKIE,
        sign_session(session_secret(password)),
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        samesite="strict",
        # uvicorn --proxy-headers takes the scheme from X-Forwarded-Proto.
        secure=request.url.scheme == "https",
    )
    return {"ok": True}


@router.post("/logout")
def logout(response: Response) -> dict:
    response.delete_cookie(ADMIN_SESSION_COOKIE)
    return {"ok": True}


@router.get("/me")
def me(request: Request) -> dict:
    return {"is_admin": is_admin(request)}
```

`main.py`: import `from mtg_api.admin_auth import require_admin, router as auth_router` (ruff/isort may prefer `from mtg_api.admin_auth import router as auth_router` on its own line; let `ruff check --fix` order them). After `app.add_middleware(...)` add `app.include_router(auth_router)`. Change the history route decorator to:

```python
@app.get("/api/v1/queries", dependencies=[Depends(require_admin)])
```

`docker-compose.yml` backend `environment`: add

```yaml
      # Admin login for /history and /admin/usage (unlinked page: /login).
      MTG_API_ADMIN_PASSWORD: ${MTG_API_ADMIN_PASSWORD:-admin}
```

`.env.example`: add

```
# Admin login (/login) for history and usage. The dev stack defaults to "admin".
MTG_API_ADMIN_PASSWORD=
```

- [ ] **Step 4: Run tests and lint**

Run: `pytest && ruff check . && ruff format --check .`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mtg-api/src/mtg_api/admin_auth.py mtg-api/src/mtg_api/main.py mtg-api/tests docker-compose.yml .env.example
git commit -m "feat(api): admin login with a signed session cookie; history is admin-only"
```

---

### Task 7: Wire gating, the cache and usage into `/api/v1/query`

**Files:**
- Modify: `mtg-api/src/mtg_api/main.py`, `mtg-api/src/mtg_api/models.py`
- Test: `mtg-api/tests/test_gating_flow.py` (new), `mtg-api/tests/test_eval_mode.py` (one test updated)

**Interfaces:**
- Consumes: everything from Tasks 1–6.
- Produces: `main.get_data_version() -> str` (lru-cached dependency). `QueryRequest.fresh: bool = False`. `QueryResponse.cached_at: datetime | None`, `degraded: str | None`, `answers_remaining: int | None`.

Behavior (from the spec):
1. Query longer than `max_query_chars` → 422.
2. `fresh: true` without an admin session and marker → 403.
3. "Tracked" = `request.generate` and not `settings.eval_mode`. Only tracked requests are gated, cached and recorded.
4. When tracked, gating is enabled and the caller isn't the admin: `check_gate`. A database error here fails closed (degraded `"global_budget"`).
5. Cache hit (tracked, cache enabled, not fresh): return the stored response with `cached_at`, record `cached`, save history with `cached=True`. No retrieval, no LLM.
6. Otherwise retrieve. Generate only if not degraded. Record `generated` / `error` / `degraded_ip` / `degraded_global` with real tokens and cost. Store successful answers in the cache.
7. History is skipped only for `source == "eval"` **in eval mode**.
8. Cache and usage database errors are logged and never fail the request.

- [ ] **Step 1: Write the failing tests** (`tests/test_gating_flow.py`)

```python
from datetime import UTC, datetime

import pytest
from conftest import admin_client, memory_engine
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_query import _FailingEngine, _FakeHit, _override

from mtg_api import main
from mtg_api.history import list_history
from mtg_api.llm import Generation
from mtg_api.main import app, get_data_version
from mtg_api.usage import llm_usage, record_usage


class _CountingAnswerer:
    def __init__(self, raises=None):
        self.calls = 0
        self._raises = raises

    def generate(self, query, context):
        self.calls += 1
        if self._raises:
            raise self._raises
        return Generation(
            text="Yes [1].", input_tokens=1_000_000, output_tokens=0, thinking_tokens=0
        )


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def gated(monkeypatch):
    for name, value in {
        "gating_enabled": True,
        "gemini_input_price_per_mtok": 0.1,  # 1M input tokens = $0.10 per answer
        "gemini_output_price_per_mtok": 1.0,
        "daily_budget_usd": 1.0,
        "ip_daily_llm_limit": 3,
        "ip_window_llm_limit": 10,
    }.items():
        monkeypatch.setattr(main.settings, name, value)


def _setup(answerer=None, engine=None, data_version="v1"):
    engine = engine or memory_engine()
    answerer = answerer or _CountingAnswerer()
    hits = [_FakeHit("p1", 1.0, {"source_type": "rule", "rule_id": "702.19b", "text": "T."})]
    _override(dense_points=hits, answerer=answerer, engine=engine)
    app.dependency_overrides[get_data_version] = lambda: data_version
    return engine, answerer


def _post(json, client=None):
    return (client or TestClient(app)).post("/api/v1/query", json=json)


def _outcomes(engine):
    with engine.connect() as conn:
        return [r.outcome for r in conn.execute(select(llm_usage).order_by(llm_usage.c.id))]


def test_overlong_query_is_422():
    _setup()
    assert _post({"query": "x" * 501}).status_code == 422
    assert _post({"query": "x" * 500}).status_code == 200


def test_generated_answer_records_usage_and_remaining(gated):
    engine, _ = _setup()
    body = _post({"query": "trample"}).json()
    assert body["answer"] == "Yes [1]."
    assert body["degraded"] is None
    assert body["cached_at"] is None
    assert body["answers_remaining"] == 2
    with engine.connect() as conn:
        row = conn.execute(select(llm_usage)).mappings().one()
    assert row["outcome"] == "generated"
    assert row["input_tokens"] == 1_000_000
    assert row["cost_usd"] == pytest.approx(0.1)
    assert row["ip_bucket"] == "testclient"


def test_ip_quota_degrades_to_retrieval_only(gated, monkeypatch):
    monkeypatch.setattr(main.settings, "ip_daily_llm_limit", 1)
    engine, answerer = _setup()
    _post({"query": "first"})
    body = _post({"query": "second"}).json()
    assert answerer.calls == 1
    assert body["answer"] is None
    assert body["degraded"] == "ip_quota"
    assert body["answers_remaining"] == 0
    assert len(body["results"]) == 1
    assert _outcomes(engine) == ["generated", "degraded_ip"]


def test_global_budget_degrades_everyone(gated):
    engine, answerer = _setup()
    record_usage(
        engine,
        now=datetime.now(UTC),
        ip_bucket="elsewhere",
        is_admin=False,
        outcome="generated",
        model="m",
        cost=1.0,
    )
    body = _post({"query": "trample"}).json()
    assert body["degraded"] == "global_budget"
    assert answerer.calls == 0
    assert _outcomes(engine)[-1] == "degraded_global"


def test_admin_is_exempt_but_recorded(gated, monkeypatch):
    engine, answerer = _setup()
    record_usage(
        engine,
        now=datetime.now(UTC),
        ip_bucket="elsewhere",
        is_admin=False,
        outcome="generated",
        model="m",
        cost=1.0,
    )
    body = _post({"query": "trample"}, admin_client(monkeypatch)).json()
    assert body["answer"] == "Yes [1]."
    assert body["answers_remaining"] is None
    with engine.connect() as conn:
        last = conn.execute(select(llm_usage).order_by(llm_usage.c.id.desc())).mappings().first()
    assert (last["outcome"], last["is_admin"]) == ("generated", True)


def test_repeat_question_is_served_from_cache(gated):
    engine, answerer = _setup()
    first = _post({"query": "How does trample work?"}).json()
    second = _post({"query": "how does TRAMPLE work"}).json()
    assert answerer.calls == 1
    assert second["cached_at"] is not None
    assert second["query"] == "how does TRAMPLE work"
    assert second["answer"] == first["answer"]
    assert second["citations"] == first["citations"]
    assert second["answers_remaining"] == 2  # cache hits are free
    assert _outcomes(engine) == ["generated", "cached"]
    assert [row["cached"] for row in list_history(engine)] == [True, False]


def test_failed_answers_are_not_cached():
    engine, answerer = _setup(answerer=_CountingAnswerer(raises=RuntimeError("boom")))
    _post({"query": "trample"})
    _post({"query": "trample"})
    assert answerer.calls == 2
    assert _outcomes(engine) == ["error", "error"]


def test_new_data_version_misses_the_cache():
    engine, answerer = _setup()
    _post({"query": "trample"})
    app.dependency_overrides[get_data_version] = lambda: "v2"
    assert _post({"query": "trample"}).json()["cached_at"] is None
    assert answerer.calls == 2


def test_fresh_is_admin_only(monkeypatch):
    _setup()
    assert _post({"query": "trample", "fresh": True}).status_code == 403


def test_admin_fresh_bypasses_and_replaces_the_cache(monkeypatch):
    engine, answerer = _setup()
    client = admin_client(monkeypatch)
    _post({"query": "trample"}, client)
    body = _post({"query": "trample", "fresh": True}, client).json()
    assert answerer.calls == 2
    assert body["cached_at"] is None
    assert _post({"query": "trample"}).json()["cached_at"] is not None


def test_eval_source_outside_eval_mode_is_still_gated_and_recorded(gated):
    engine, _ = _setup()
    _post({"query": "trample", "source": "eval"})
    assert _outcomes(engine) == ["generated"]
    assert len(list_history(engine)) == 1


def test_eval_mode_skips_gating_cache_and_usage(gated, monkeypatch):
    monkeypatch.setattr(main.settings, "eval_mode", True)
    monkeypatch.setattr(main.settings, "ip_daily_llm_limit", 0)
    engine, answerer = _setup()
    _post({"query": "trample", "source": "eval"})
    body = _post({"query": "trample", "source": "eval"}).json()
    assert answerer.calls == 2
    assert body["degraded"] is None
    assert body["cached_at"] is None
    assert _outcomes(engine) == []


def test_ungated_dev_stack_still_records_usage():
    engine, _ = _setup()
    body = _post({"query": "trample"}).json()
    assert body["answers_remaining"] is None
    assert _outcomes(engine) == ["generated"]


def test_retrieval_only_requests_are_not_recorded(gated):
    engine, answerer = _setup()
    _post({"query": "trample", "generate": False})
    assert answerer.calls == 0
    assert _outcomes(engine) == []


def test_database_failure_fails_closed_when_gated(gated):
    _, answerer = _setup(engine=_FailingEngine())
    body = _post({"query": "trample"}).json()
    assert body["degraded"] == "global_budget"
    assert answerer.calls == 0


def test_database_failure_does_not_break_ungated_answers():
    _, answerer = _setup(engine=_FailingEngine())
    resp = _post({"query": "trample"})
    assert resp.status_code == 200
    assert resp.json()["answer"] == "Yes [1]."
```

In `tests/test_eval_mode.py`, add the `eval_mode` fixture to `test_eval_source_is_not_saved_to_history` (history skipping now requires eval mode):

```python
def test_eval_source_is_not_saved_to_history(eval_mode):
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_gating_flow.py -v`
Expected: FAIL (`ImportError: get_data_version`).

- [ ] **Step 3: Implement**

`models.py`: add `from datetime import datetime`, then in `QueryRequest`:

```python
    # Admin only: skip the answer cache and replace its entry.
    fresh: bool = False
```

and in `QueryResponse` (before the eval-only fields):

```python
    # Set when this answer came from the answer cache: when it was generated.
    cached_at: datetime | None = None
    # Why no answer was generated: "ip_quota" or "global_budget".
    degraded: str | None = None
    # LLM answers this visitor has left today (null when not gated, or admin).
    answers_remaining: int | None = None
```

`main.py`:
- Imports: add `from datetime import UTC, datetime`; `Request` to the fastapi import; `from mtg_api.admin_auth import has_admin_marker, is_admin, require_admin`; `from mtg_api.answer_cache import cache_key, get_cached, normalize_query, put_cached, read_data_version`; `from mtg_api.usage import Gate, check_gate, cost_usd, ip_bucket, record_usage`.
- Add the dependency next to the other `lru_cache` getters:

```python
@lru_cache(maxsize=1)
def get_data_version() -> str:
    return read_data_version(settings.parsed_dir)
```

- Add these helpers above the route:

```python
_DEGRADED_OUTCOMES = {"ip_quota": "degraded_ip", "global_budget": "degraded_global"}
# Fields that describe one request, not the cached answer.
_PER_REQUEST_FIELDS = {
    "query",
    "cached_at",
    "degraded",
    "answers_remaining",
    "context_hash",
    "prompt_version",
    "generator",
    "usage",
}


def _gate(engine: Engine, s: Settings, bucket: str, now: datetime) -> Gate:
    try:
        return check_gate(engine, s, bucket, now)
    except Exception:
        # Fail closed: without the usage table there's no way to know the spend.
        logger.exception("Usage gate unavailable; answering retrieval-only")
        return Gate("global_budget", 0)


def _record(engine: Engine, **fields) -> None:
    try:
        record_usage(engine, **fields)
    except Exception:
        logger.exception("Failed to record LLM usage")


def _cache_get(engine: Engine, key: str) -> tuple[dict, datetime] | None:
    try:
        return get_cached(engine, key)
    except Exception:
        logger.exception("Answer cache lookup failed")
        return None


def _cache_put(engine: Engine, key: str, query: str, response: QueryResponse, now: datetime):
    try:
        put_cached(
            engine,
            key,
            normalized_query=normalize_query(query),
            response=response.model_dump(mode="json", exclude=_PER_REQUEST_FIELDS),
            now=now,
        )
    except Exception:
        logger.exception("Failed to store answer in cache")


def _save(engine: Engine, s: Settings, request: QueryRequest, **fields) -> None:
    # Eval runs are not user queries, but only eval mode may say so.
    if settings.eval_mode and request.source == "eval":
        return
    try:
        save_history(engine, query=request.query, model=s.gemini_model, **fields)
    except Exception:
        logger.exception("Failed to persist query history")
```

- Change the `query` signature: add `http_request: Request,` right after `request: QueryRequest,` and `data_version: str = Depends(get_data_version),` as the last parameter.
- At the top of the body, after `s = resolve_settings(request.overrides)` and the `build_answerer` override lines, insert:

```python
    if len(request.query) > s.max_query_chars:
        raise HTTPException(
            status_code=422, detail=f"query is longer than {s.max_query_chars} characters"
        )
    admin = is_admin(http_request)
    if request.fresh and not (admin and has_admin_marker(http_request)):
        raise HTTPException(status_code=403, detail="fresh answers are admin-only")

    now = datetime.now(UTC)
    bucket = ip_bucket(http_request.client.host if http_request.client else "unknown")
    tracked = request.generate and not settings.eval_mode
    record = dict(now=now, ip_bucket=bucket, is_admin=admin, model=s.gemini_model)

    gate = Gate(None, 0)
    remaining = None
    if tracked and s.gating_enabled and not admin:
        gate = _gate(engine, s, bucket, now)
        remaining = gate.answers_remaining

    key = cache_key(request.query, s, data_version) if tracked and s.answer_cache_enabled else None
    hit = _cache_get(engine, key) if key and not request.fresh else None
    if hit is not None:
        stored, generated_at = hit
        _record(engine, outcome="cached", **record)
        _save(
            engine,
            s,
            request,
            answer=stored["answer"],
            results=stored["results"],
            error=None,
            citations=stored["citations"],
            citation_stats=stored["citation_stats"],
            rule_references=stored["rule_references"],
            cached=True,
        )
        return QueryResponse(
            **stored, query=request.query, cached_at=generated_at, answers_remaining=remaining
        )
```

- Change the generation condition to skip degraded requests:

```python
    if request.generate and gate.degraded is None:
```

- After the generation `try/except` block, insert:

```python
    if tracked:
        if gate.degraded:
            _record(engine, outcome=_DEGRADED_OUTCOMES[gate.degraded], **record)
        else:
            _record(
                engine,
                outcome="error" if error else "generated",
                generation=generation,
                cost=cost_usd(generation, s) if generation else 0.0,
                **record,
            )
            if remaining is not None:
                remaining = max(0, remaining - 1)
```

- Replace the existing `if request.source != "eval": try: save_history(...)` block with:

```python
    _save(
        engine,
        s,
        request,
        answer=answer,
        results=[r.model_dump() for r in all_results],
        error=error,
        citations=[c.model_dump() for c in citations],
        citation_stats=citation_stats.model_dump(),
        rule_references=rule_references,
    )
```

- Replace the final `return QueryResponse(...)` with:

```python
    response = QueryResponse(
        query=request.query,
        results=all_results,
        answer=answer,
        citations=citations,
        rule_references=rule_references,
        citation_stats=citation_stats,
        degraded=gate.degraded,
        answers_remaining=remaining,
        **eval_fields,
    )
    if key and answer:
        _cache_put(engine, key, request.query, response, now)
    return response
```

- [ ] **Step 4: Run the whole suite and lint**

Run: `pytest && ruff check . && ruff format --check .`
Expected: PASS. If an older test in `test_query.py` posts the same question twice to one shared engine and now gets a cached second answer, check whether the test's intent was two independent generations. If it was, set `monkeypatch.setattr(main.settings, "answer_cache_enabled", False)` in that test instead of weakening the gating tests.

- [ ] **Step 5: Commit**

```bash
git add mtg-api/src/mtg_api/main.py mtg-api/src/mtg_api/models.py mtg-api/tests
git commit -m "feat(api): gate, cache and meter answers in /api/v1/query"
```

---

### Task 8: Admin usage endpoint and startup checks

**Files:**
- Modify: `mtg-api/src/mtg_api/main.py`
- Test: `mtg-api/tests/test_admin_usage.py` (new), `mtg-api/tests/test_lifespan.py`

**Interfaces:**
- Consumes: `usage_summary`, `check_gating_config` (Task 4); `require_admin` (Task 6); `get_data_version` (Task 7).
- Produces: `GET /api/v1/admin/usage` (admin) → the `usage_summary` dict. The lifespan calls `check_gating_config(settings)` first and warms `get_data_version()`.

- [ ] **Step 1: Write the failing tests**

`tests/test_admin_usage.py`:

```python
from datetime import UTC, datetime

from conftest import admin_client, memory_engine
from fastapi.testclient import TestClient

from mtg_api.main import app, get_db_engine
from mtg_api.usage import record_usage


def test_usage_requires_admin():
    app.dependency_overrides[get_db_engine] = memory_engine
    try:
        assert TestClient(app).get("/api/v1/admin/usage").status_code == 401
    finally:
        app.dependency_overrides.clear()


def test_usage_returns_the_summary(monkeypatch):
    engine = memory_engine()
    record_usage(
        engine,
        now=datetime.now(UTC),
        ip_bucket="203.0.113.7",
        is_admin=False,
        outcome="generated",
        model="m",
        cost=0.02,
    )
    app.dependency_overrides[get_db_engine] = lambda: engine
    try:
        body = admin_client(monkeypatch).get("/api/v1/admin/usage").json()
    finally:
        app.dependency_overrides.clear()
    assert body["days"][-1]["spend_usd"] == 0.02
    assert body["top_ip_buckets"][0]["ip_bucket"] == "203.0.113.7"
    assert set(body) == {"budget_usd", "days", "cache_hit_rate", "top_ip_buckets"}
```

In `tests/test_lifespan.py`, extend `test_lifespan_warms_all_caches`: add

```python
    monkeypatch.setattr("mtg_api.main.get_data_version", lambda: calls.append("data_version"))
```

and add `"data_version"` to the expected set. Append:

```python
def test_lifespan_refuses_gating_without_prices(monkeypatch):
    import pytest

    from mtg_api import main

    monkeypatch.setattr(main.settings, "gating_enabled", True)
    monkeypatch.setattr(main.settings, "gemini_input_price_per_mtok", 0.0)

    async def _run():
        async with lifespan(app):
            pass

    with pytest.raises(RuntimeError, match="PRICE"):
        asyncio.run(_run())
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_admin_usage.py tests/test_lifespan.py -v`
Expected: FAIL (404 for the usage route; `data_version` missing from the calls; no RuntimeError).

- [ ] **Step 3: Implement** (`main.py`)

Import `check_gating_config` and `usage_summary` from `mtg_api.usage`. At the start of `lifespan`, before the warm-up calls:

```python
    # Refuse to serve with gating on but no prices: every answer would look free.
    check_gating_config(settings)
```

and add `get_data_version()` to the warm-up list. Add the route after `get_query_history`:

```python
@app.get("/api/v1/admin/usage", dependencies=[Depends(require_admin)])
def get_usage(engine: Engine = Depends(get_db_engine)) -> dict:
    return usage_summary(engine, settings, datetime.now(UTC))
```

- [ ] **Step 4: Run tests and lint**

Run: `pytest && ruff check . && ruff format --check .`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mtg-api/src/mtg_api/main.py mtg-api/tests
git commit -m "feat(api): admin usage summary endpoint and a startup check for gating prices"
```

---

### Task 9: `sync_data.py` writes the data-version marker

**Files:**
- Modify: `deploy/sync_data.py`
- Test: `deploy/test_sync_data.py`

**Interfaces:**
- Produces: `write_data_version(directory: Path, now: datetime) -> Path`, which writes `<directory>/data_version` holding `now` as ISO-8601 UTC (seconds) and returns the path. The name must equal `mtg_api.answer_cache.DATA_VERSION_FILE` (`"data_version"`).

- [ ] **Step 1: Write the failing test** (append to `deploy/test_sync_data.py`; match its existing import style for `sync_data`)

```python
def test_write_data_version_writes_an_iso_timestamp(tmp_path):
    from datetime import UTC, datetime

    path = sync_data.write_data_version(tmp_path, datetime(2026, 9, 29, 15, 0, 7, tzinfo=UTC))
    assert path == tmp_path / "data_version"
    assert path.read_text(encoding="utf-8") == "2026-09-29T15:00:07+00:00\n"
```

(If the test file imports names directly instead of the module, import `write_data_version` the same way.)

- [ ] **Step 2: Run to verify failure**

Run (in `deploy/`): `python -m pytest test_sync_data.py -v`
Expected: FAIL (`AttributeError: write_data_version`).

- [ ] **Step 3: Implement**

Add near `latest_parsed_files` (ensure `from datetime import UTC, datetime` is imported):

```python
# Must match mtg_api.answer_cache.DATA_VERSION_FILE: the backend keys its
# answer cache on this file, so every sync invalidates cached answers.
DATA_VERSION_FILE = "data_version"


def write_data_version(directory: Path, now: datetime) -> Path:
    path = directory / DATA_VERSION_FILE
    path.write_text(now.astimezone(UTC).isoformat(timespec="seconds") + "\n", encoding="utf-8")
    return path
```

In `sync()`, replace the "Copying ..." block with:

```python
    print(f"Copying {', '.join(p.name for p in files)} and a new {DATA_VERSION_FILE}...")
    with tempfile.TemporaryDirectory() as tmp:
        marker = write_data_version(Path(tmp), datetime.now(UTC))
        server.run_with_tar(
            [
                "docker", "run", "-i", "--rm", "-v", f"{target.parsed_volume}:/d", "alpine",
                "sh", "-c",
                f"rm -f /d/cards_*.jsonl /d/rules_*.jsonl /d/{DATA_VERSION_FILE} && tar x -C /d",
            ],
            [*files, marker],
        )  # fmt: skip
```

Update the module docstring's list of what gets copied to mention the marker.

- [ ] **Step 4: Run tests and lint**

Run (in `deploy/`): `python -m pytest -v && ruff check . && ruff format --check .`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add deploy/sync_data.py deploy/test_sync_data.py
git commit -m "feat(deploy): write a data_version marker with every sync"
```

---

### Task 10: nginx real client IPs and flood limits

**Files:**
- Modify: `mtg-web/nginx.conf`

**Interfaces:**
- Produces: `$remote_addr` = the visitor, even behind Traefik. The backend gets `X-Forwarded-For: <visitor>` (a single address, so uvicorn can't be fooled by a client-supplied header). 429 on floods.

- [ ] **Step 1: Edit `nginx.conf`**

After the existing `map` block (top level of the file, which is inside nginx's `http` context), add:

```nginx
# Behind Coolify's Traefik (and Docker's networks), take the visitor's
# address from X-Forwarded-For. real_ip_recursive skips trusted hops from
# the right, so a client-supplied X-Forwarded-For can't pose as the visitor.
set_real_ip_from 10.0.0.0/8;
set_real_ip_from 172.16.0.0/12;
set_real_ip_from 192.168.0.0/16;
real_ip_header X-Forwarded-For;
real_ip_recursive on;

# Flood protection per address; the backend enforces the LLM quotas.
limit_req_zone $binary_remote_addr zone=api:10m rate=1r/s;
limit_req_zone $binary_remote_addr zone=login:1m rate=5r/m;
limit_conn_zone $binary_remote_addr zone=api_conn:10m;
limit_req_status 429;
limit_conn_status 429;
```

Replace the `location /api/` block with:

```nginx
    # The SPA calls /api on its own origin; forward it to the backend.
    location /api/ {
        limit_req zone=api burst=5 nodelay;
        limit_conn api_conn 2;
        proxy_pass http://backend:8000;
        proxy_set_header Host $host;
        # Just the resolved visitor: uvicorn trusts this header as-is.
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto $forwarded_proto;
        # Answer generation can take up to MTG_API_GEMINI_TIMEOUT_SECONDS (60).
        proxy_read_timeout 90s;
    }

    # Password guessing gets a much tighter limit.
    location = /api/v1/auth/login {
        limit_req zone=login burst=3 nodelay;
        proxy_pass http://backend:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto $forwarded_proto;
    }
```

- [ ] **Step 2: Check the config parses and the limits bite**

From the repo root:

```bash
docker compose up -d --build frontend backend
docker compose exec frontend nginx -t
for i in $(seq 1 12); do curl -s -o /dev/null -w "%{http_code} " http://localhost:3000/api/v1/rules/702.19; done; echo
```

Expected: `nginx -t` reports `syntax is ok` / `test is successful`, and the loop prints some `200`s followed by `429`s (burst 5 plus about 1/s).

```bash
for i in $(seq 1 6); do curl -s -o /dev/null -w "%{http_code} " -X POST -H 'Content-Type: application/json' -d '{"password":"x"}' http://localhost:3000/api/v1/auth/login; done; echo
```

Expected: `403`s, then `429`s.

- [ ] **Step 3: Commit**

```bash
git add mtg-web/nginx.conf
git commit -m "feat(web): real client IPs behind Traefik and per-IP flood limits in nginx"
```

---

### Task 11: Frontend: search page shows cache, limits and remaining answers

**Files:**
- Create: `mtg-web/src/lib/admin.ts`
- Modify: `mtg-web/src/lib/api.ts`, `mtg-web/src/routes/+page.svelte`

**Interfaces:**
- Consumes: the `QueryResponse` fields from Task 7; 429 from Task 10.
- Produces (in `api.ts`): `MAX_QUERY_CHARS = 500`; `ADMIN_HEADER = 'X-Admin-Request'`; `class RateLimitedError extends Error`; `QueryResponse` gains `cached_at: string | null`, `degraded: 'ip_quota' | 'global_budget' | null`, `answers_remaining: number | null`; `submitQuery(query: string, opts?: { fresh?: boolean })`; `fetchAdminStatus(): Promise<boolean>`. In `src/lib/admin.ts` (new): `isAdmin` (writable store, default `false`), `refreshAdmin(): Promise<void>`. Nothing calls `refreshAdmin` until Task 12's layout, so admin controls stay hidden until then.

- [ ] **Step 1: Update `api.ts`**

After `const API_URL = '';` add:

```ts
// Keep in step with the backend's MTG_API_MAX_QUERY_CHARS.
export const MAX_QUERY_CHARS = 500;
// Required on cookie-authenticated admin calls (see mtg_api/admin_auth.py).
export const ADMIN_HEADER = 'X-Admin-Request';

export class RateLimitedError extends Error {}
```

Extend `QueryResponse`:

```ts
  // Set when the answer came from the cache: when it was first generated.
  cached_at: string | null;
  // Why there's no answer: this visitor's quota, or the site's daily budget.
  degraded: 'ip_quota' | 'global_budget' | null;
  // AI answers this visitor has left today; null when unlimited.
  answers_remaining: number | null;
```

Replace `submitQuery`:

```ts
export async function submitQuery(
  query: string,
  { fresh = false }: { fresh?: boolean } = {}
): Promise<QueryResponse> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (fresh) headers[ADMIN_HEADER] = '1';
  const resp = await fetch(`${API_URL}/api/v1/query`, {
    method: 'POST',
    headers,
    body: JSON.stringify(fresh ? { query, fresh } : { query })
  });
  if (resp.status === 429) {
    throw new RateLimitedError('Too many requests. Wait a few seconds and try again.');
  }
  if (!resp.ok) {
    throw new Error(`query failed: ${resp.status}`);
  }
  return resp.json();
}

export async function fetchAdminStatus(): Promise<boolean> {
  try {
    const resp = await fetch(`${API_URL}/api/v1/auth/me`);
    return resp.ok && (await resp.json()).is_admin === true;
  } catch {
    return false;
  }
}
```

Create `src/lib/admin.ts`:

```ts
import { writable } from 'svelte/store';
import { fetchAdminStatus } from './api';

// Whether this browser holds an admin session. Admin links and controls
// render only when true; the backend enforces access either way.
export const isAdmin = writable(false);

export async function refreshAdmin(): Promise<void> {
  isAdmin.set(await fetchAdminStatus());
}
```

- [ ] **Step 2: Update `+page.svelte`**

Replace the `<script>` block:

```svelte
<script lang="ts">
  import {
    MAX_QUERY_CHARS,
    RateLimitedError,
    submitQuery,
    type QueryResponse
  } from '$lib/api';
  import { isAdmin } from '$lib/admin';
  import CitedAnswer from '$lib/CitedAnswer.svelte';
  import SourcesList from '$lib/SourcesList.svelte';

  let query = '';
  let response: QueryResponse | null = null;
  let error = '';
  let loading = false;

  async function ask(fresh = false) {
    error = '';
    loading = true;
    try {
      response = await submitQuery(query, { fresh });
    } catch (e) {
      error = e instanceof RateLimitedError ? e.message : String(e);
    } finally {
      loading = false;
    }
  }

  function formatDate(iso: string): string {
    return new Date(iso).toLocaleDateString([], { month: 'short', day: 'numeric' });
  }

  // Quotas and the budget reset at UTC midnight; show it in local time.
  function resetTime(): string {
    const now = new Date();
    const next = new Date(
      Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() + 1)
    );
    return next.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  }
</script>
```

Replace the markup inside `<main>` (dropping the public "View query history" link):

```svelte
<main>
  <h1>MTG Rules Search (prototype)</h1>
  <form on:submit|preventDefault={() => ask()}>
    <input
      type="text"
      bind:value={query}
      maxlength={MAX_QUERY_CHARS}
      placeholder="Ask a rules question"
    />
    <button type="submit" disabled={loading}>{loading ? 'Searching…' : 'Search'}</button>
  </form>
  {#if query.length > MAX_QUERY_CHARS - 100}
    <p class="note">{query.length}/{MAX_QUERY_CHARS} characters</p>
  {/if}

  {#if error}
    <p style="color: red">{error}</p>
  {/if}

  {#if response}
    {#if response.degraded === 'global_budget'}
      <p class="notice">
        AI answers are paused for today. They resume at {resetTime()}. Here are the matching
        rules, rulings and cards.
      </p>
    {:else if response.degraded === 'ip_quota'}
      <p class="notice">
        {#if response.answers_remaining}
          You're asking quickly, so AI answers pause for a few minutes. Here are the matching
          rules, rulings and cards.
        {:else}
          You've used today's AI answers. They reset at {resetTime()}. Here are the matching
          rules, rulings and cards.
        {/if}
      </p>
    {/if}

    {#if response.answer}
      <div class="answer">
        <h2>Answer</h2>
        {#if response.cached_at}
          <p class="badge">
            Cached answer · first generated {formatDate(response.cached_at)}
            {#if $isAdmin}
              <button type="button" on:click={() => ask(true)} disabled={loading}>
                Get a fresh answer
              </button>
            {/if}
          </p>
        {/if}
        <CitedAnswer
          answer={response.answer}
          citations={response.citations}
          ruleReferences={response.rule_references}
        />
        {#if response.citation_stats.uncited_answer}
          <p class="note">No sources cited</p>
        {/if}
      </div>
    {/if}

    {#if response.answers_remaining !== null && !response.degraded}
      <p class="note">
        {response.answers_remaining} AI answer{response.answers_remaining === 1 ? '' : 's'} left
        today
      </p>
    {/if}

    <SourcesList
      citations={response.citations}
      results={response.results}
      expanded={!response.answer}
    />
  {/if}
</main>
```

Add to `<style>`:

```css
  .badge {
    display: inline-block;
    background: #eef3fb;
    border: 1px solid #c9d8f0;
    border-radius: 4px;
    padding: 0.2rem 0.5rem;
    font-size: 0.85rem;
  }
  .notice {
    background: #fff8e1;
    border: 1px solid #f0d98c;
    border-radius: 4px;
    padding: 0.5rem 0.75rem;
  }
```

- [ ] **Step 3: Build**

Run (in `mtg-web/`): `npm run build`
Expected: build succeeds with no Svelte type errors.

- [ ] **Step 4: Check by hand in the dev stack**

With the stack up (`docker compose up -d --build`), open http://localhost:3000:
- Ask a question twice (the second time with different capitalization). The second answer shows the "Cached answer · first generated …" badge.
- Set `MTG_API_GATING_ENABLED=true`, both `MTG_API_GEMINI_*_PRICE_PER_MTOK` (any positive values) and `MTG_API_IP_DAILY_LLM_LIMIT=1` in `.env`, then run `docker compose up -d backend`. Ask two *different* questions. The first shows "0 AI answers left today"; the second shows the quota notice with sources. Remove those `.env` lines afterwards and run `docker compose up -d backend` again.

- [ ] **Step 5: Commit**

```bash
git add mtg-web/src/lib/api.ts mtg-web/src/lib/admin.ts mtg-web/src/routes/+page.svelte
git commit -m "feat(web): show cached-answer badges, quota notices and answers left"
```

---

### Task 12: Frontend: hidden admin login, nav, history and usage pages

**Files:**
- Create: `mtg-web/src/routes/+layout.svelte`, `mtg-web/src/routes/login/+page.svelte`, `mtg-web/src/routes/admin/usage/+page.svelte`
- Modify: `mtg-web/src/lib/api.ts`, `mtg-web/src/routes/history/+page.svelte`

**Interfaces:**
- Consumes: `/api/v1/auth/*` (Task 6), `/api/v1/admin/usage` (Task 8), `ADMIN_HEADER`, `RateLimitedError`, `isAdmin` and `refreshAdmin` (Task 11).
- Produces: `api.ts`: `login(password): Promise<boolean>`, `logout(): Promise<void>`, `fetchUsage(): Promise<UsageSummary>`, `UsageSummary` type; `fetchHistory` sends the admin header.

- [ ] **Step 1: `api.ts` additions**

Add a helper above `fetchHistory` and change `fetchHistory` to use it:

```ts
async function adminGet<T>(path: string): Promise<T> {
  const resp = await fetch(`${API_URL}${path}`, { headers: { [ADMIN_HEADER]: '1' } });
  if (!resp.ok) {
    throw new Error(`${path} failed: ${resp.status}`);
  }
  return resp.json();
}
```

```ts
export async function fetchHistory(limit: number, offset: number): Promise<QueryHistoryRow[]> {
  return adminGet(`/api/v1/queries?limit=${limit}&offset=${offset}`);
}
```

Add `cached: boolean;` to `QueryHistoryRow`. Append:

```ts
export async function login(password: string): Promise<boolean> {
  const resp = await fetch(`${API_URL}/api/v1/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ password })
  });
  if (resp.status === 429) throw new RateLimitedError('Too many attempts. Wait a minute.');
  if (resp.status === 403) return false;
  if (!resp.ok) throw new Error(`login failed: ${resp.status}`);
  return true;
}

export async function logout(): Promise<void> {
  await fetch(`${API_URL}/api/v1/auth/logout`, { method: 'POST' });
}

export interface UsageDay {
  date: string;
  spend_usd: number;
  outcomes: Record<string, number>;
}

export interface UsageBucket {
  ip_bucket: string;
  requests: number;
  answers: number;
  spend_usd: number;
}

export interface UsageSummary {
  budget_usd: number;
  days: UsageDay[]; // oldest first; the last entry is today (UTC)
  cache_hit_rate: number | null;
  top_ip_buckets: UsageBucket[];
}

export async function fetchUsage(): Promise<UsageSummary> {
  return adminGet('/api/v1/admin/usage');
}
```

- [ ] **Step 2: `src/routes/+layout.svelte`**

```svelte
<script lang="ts">
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import { isAdmin, refreshAdmin } from '$lib/admin';
  import { logout } from '$lib/api';

  onMount(refreshAdmin);

  async function onLogout() {
    await logout();
    await refreshAdmin();
    goto('/');
  }
</script>

{#if $isAdmin}
  <nav class="admin-nav">
    <a href="/">Search</a>
    <a href="/history">History</a>
    <a href="/admin/usage">Usage</a>
    <button type="button" on:click={onLogout}>Log out</button>
  </nav>
{/if}

<slot />

<style>
  .admin-nav {
    display: flex;
    gap: 1rem;
    align-items: center;
    padding: 0.5rem 0;
    border-bottom: 1px solid #ddd;
    font-size: 0.9rem;
  }
</style>
```

- [ ] **Step 3: `src/routes/login/+page.svelte`** (not linked from anywhere)

```svelte
<script lang="ts">
  import { goto } from '$app/navigation';
  import { login, RateLimitedError } from '$lib/api';
  import { refreshAdmin } from '$lib/admin';

  let password = '';
  let error = '';
  let busy = false;

  async function onSubmit() {
    error = '';
    busy = true;
    try {
      if (await login(password)) {
        await refreshAdmin();
        goto('/');
      } else {
        error = 'Incorrect password.';
      }
    } catch (e) {
      error = e instanceof RateLimitedError ? e.message : String(e);
    } finally {
      busy = false;
    }
  }
</script>

<main>
  <h1>Log in</h1>
  <form on:submit|preventDefault={onSubmit}>
    <input type="password" bind:value={password} autocomplete="current-password" />
    <button type="submit" disabled={busy}>Log in</button>
  </form>
  {#if error}
    <p style="color: red">{error}</p>
  {/if}
</main>
```

- [ ] **Step 4: `src/routes/admin/usage/+page.svelte`**

```svelte
<script lang="ts">
  import { fetchUsage, type UsageSummary } from '$lib/api';

  const OUTCOMES = ['generated', 'cached', 'degraded_ip', 'degraded_global', 'error'];

  let usage: UsageSummary | null = null;
  let error = '';

  fetchUsage()
    .then((u) => (usage = u))
    .catch((e) => (error = String(e)));

  const dollars = (n: number) => `$${n.toFixed(4)}`;
  $: today = usage ? usage.days[usage.days.length - 1] : null;
</script>

<main>
  <h1>Usage</h1>
  {#if error}
    <p style="color: red">{error}</p>
  {/if}

  {#if usage && today}
    <p>
      Today (UTC): <strong>{dollars(today.spend_usd)}</strong> of {dollars(usage.budget_usd)}
      ({Math.round((today.spend_usd / usage.budget_usd) * 100)}%). Cache hit rate:
      {usage.cache_hit_rate === null ? 'n/a' : `${Math.round(usage.cache_hit_rate * 100)}%`}
    </p>

    <h2>Last 7 days</h2>
    <table>
      <thead>
        <tr>
          <th>Date</th>
          <th>Spend</th>
          {#each OUTCOMES as o}<th>{o}</th>{/each}
        </tr>
      </thead>
      <tbody>
        {#each [...usage.days].reverse() as day}
          <tr>
            <td>{day.date}</td>
            <td>{dollars(day.spend_usd)}</td>
            {#each OUTCOMES as o}<td>{day.outcomes[o] ?? 0}</td>{/each}
          </tr>
        {/each}
      </tbody>
    </table>

    <h2>Top IP buckets today</h2>
    {#if usage.top_ip_buckets.length === 0}
      <p>No requests yet today.</p>
    {:else}
      <table>
        <thead>
          <tr><th>IP bucket</th><th>Requests</th><th>AI answers</th><th>Spend</th></tr>
        </thead>
        <tbody>
          {#each usage.top_ip_buckets as b}
            <tr>
              <td>{b.ip_bucket}</td>
              <td>{b.requests}</td>
              <td>{b.answers}</td>
              <td>{dollars(b.spend_usd)}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    {/if}
  {/if}
</main>

<style>
  table {
    border-collapse: collapse;
    font-size: 0.9rem;
  }
  th,
  td {
    border-bottom: 1px solid #ddd;
    padding: 0.25rem 0.6rem;
    text-align: left;
  }
</style>
```

- [ ] **Step 5: Label cached history rows**

In `history/+page.svelte`, next to where each row's query is rendered in the list, add a marker for cached rows:

```svelte
{#if row.cached}<span class="note">(cached)</span>{/if}
```

(Place it inside the existing `{#each rows as row}` markup, right after the query text; add a `.note { color: #666; font-size: 0.85rem; }` style if the page doesn't have one.)

- [ ] **Step 6: Build and check by hand**

Run (in `mtg-web/`): `npm run build`. Expected: success.

In the dev stack (`docker compose up -d --build`), check each of these:
- http://localhost:3000 shows no admin nav and no history link.
- http://localhost:3000/history shows an error (`/api/v1/queries failed: 401`).
- http://localhost:3000/login with the password `admin` (the dev default) sends you back to `/`, and the nav now shows Search, History, Usage and Log out.
- History lists queries, with repeats marked "(cached)".
- Usage shows today's spend, the 7-day table and IP buckets.
- A cached answer shows the "Get a fresh answer" button, and clicking it produces a new answer without the badge.
- Log out hides the nav.

- [ ] **Step 7: Commit**

```bash
git add mtg-web/src
git commit -m "feat(web): hidden admin login with history and usage pages"
```

---

### Task 13: Production config, pricing and docs

**Files:**
- Modify: `docker-compose.prod.yml`, `README.md`, `.env.example`

**Interfaces:**
- Consumes: every setting from Task 1.

- [ ] **Step 1: Look up current gemini-3.5-flash pricing**

Fetch https://ai.google.dev/gemini-api/docs/pricing (WebFetch) and find the **paid tier, standard (non-batch) text** input and output prices per 1M tokens for `gemini-3.5-flash`. Output prices include thinking tokens. If input pricing depends on prompt length, use the price for prompts under the threshold (ours are a few thousand tokens). Write both numbers down with today's date for the commit message.

- [ ] **Step 2: `docker-compose.prod.yml`**

Backend `environment`, after `MTG_API_TASK_ENDPOINTS`, using the two prices from Step 1 as the defaults:

```yaml
      # Cost gating (see README "Access"): per-IP quotas and a daily cap on
      # Gemini spend. Prices are USD per 1M tokens (thinking bills as output),
      # checked on <date> at ai.google.dev/gemini-api/docs/pricing.
      MTG_API_GATING_ENABLED: "true"
      MTG_API_DAILY_BUDGET_USD: ${MTG_API_DAILY_BUDGET_USD:-1.0}
      MTG_API_GEMINI_INPUT_PRICE_PER_MTOK: ${MTG_API_GEMINI_INPUT_PRICE_PER_MTOK:-<input price>}
      MTG_API_GEMINI_OUTPUT_PRICE_PER_MTOK: ${MTG_API_GEMINI_OUTPUT_PRICE_PER_MTOK:-<output price>}
      MTG_API_IP_DAILY_LLM_LIMIT: ${MTG_API_IP_DAILY_LLM_LIMIT:-20}
      MTG_API_IP_WINDOW_LLM_LIMIT: ${MTG_API_IP_WINDOW_LLM_LIMIT:-5}
      # Low thinking and a total output cap bound the cost of each answer.
      MTG_API_GENERATION_THINKING_LEVEL: ${MTG_API_GENERATION_THINKING_LEVEL:-low}
      MTG_API_GENERATION_MAX_TOKENS: ${MTG_API_GENERATION_MAX_TOKENS:-2048}
      # Admin login at /login (history, usage). Generated by Coolify.
      MTG_API_ADMIN_PASSWORD: ${SERVICE_PASSWORD_ADMIN}
```

Replace `<date>`, `<input price>` and `<output price>` with the Step 1 values (for example `0.30`). No angle brackets may remain; check with `grep -n "<" docker-compose.prod.yml`. Update the header comment's list of Coolify magic variables to include `SERVICE_PASSWORD_ADMIN`.

- [ ] **Step 3: README**

- In "Coolify setup" step 3: mention `SERVICE_PASSWORD_ADMIN` (generated) and the optional `MTG_API_DAILY_BUDGET_USD`, the `MTG_API_IP_*` limits and the price overrides.
- Replace the paragraph that begins "To open the site to the public…" with a new subsection:

```markdown
### Cost gating

Every AI answer is a paid Gemini call, so the backend meters them
(`MTG_API_GATING_ENABLED=true` in production):

- **Global cap:** once today's recorded spend (UTC day) reaches
  `MTG_API_DAILY_BUDGET_USD` (default $1), answers pause until UTC midnight.
  Spend is Gemini's real token counts × the configured prices, stored per
  call in the `llm_usage` table.
- **Per visitor:** 20 AI answers per UTC day and at most 5 per 10 minutes
  per IP (IPv6 grouped by /64). nginx also limits `/api` to about 1 request
  per second per address (burst 5, 2 at once) and the login to 5 per minute.
- **Over a limit** the visitor still gets the matching rules, rulings and
  cards; only the AI answer is skipped, with a note saying why.
- **Answer cache:** a repeated question (ignoring case, spacing and
  trailing punctuation) is served from `answer_cache` with a "Cached
  answer" badge and costs nothing. Every `deploy/sync_data.py` run writes a
  new `data_version` marker, so no cached answer survives a data update.
- **Admin:** `/login` (not linked anywhere) with `SERVICE_PASSWORD_ADMIN`
  shows History and Usage, offers "Get a fresh answer" on cached answers,
  and is exempt from the limits (admin usage still counts toward the day's
  spend).
- **Backstop:** a Google Cloud billing budget alert (~$30/month) on the
  Gemini project. It alerts but does not stop spending.

Usage rows keep raw IP buckets and have no retention limit yet.
```

- In "Seeding the data": mention that the sync also writes the `data_version` marker.

- [ ] **Step 4: `.env.example`**

Under the Gemini block, add:

```
# Cost gating (off in dev; see README "Cost gating"). Enabling it needs both prices.
# MTG_API_GATING_ENABLED=true
# MTG_API_GEMINI_INPUT_PRICE_PER_MTOK=
# MTG_API_GEMINI_OUTPUT_PRICE_PER_MTOK=
# MTG_API_GENERATION_THINKING_LEVEL=low
```

- [ ] **Step 5: Validate the compose file**

Run: `docker compose -f docker-compose.prod.yml config --quiet`, with dummy values for the required variables if it complains: `SERVICE_PASSWORD_POSTGRES=x SERVICE_PASSWORD_WEB=x SERVICE_PASSWORD_ADMIN=x MTG_API_GEMINI_API_KEY=x docker compose -f docker-compose.prod.yml config --quiet`.
Expected: exit 0.

- [ ] **Step 6: Commit**

```bash
git add docker-compose.prod.yml README.md .env.example
git commit -m "feat(deploy): turn on cost gating, low thinking and admin login in production"
```

---

### Task 14: Evals: thinking-low experiment, then run it

**Files:**
- Create: `evals/experiments/thinking-low.yaml`
- Modify: `evals/src/mtg_evals/runner.py`, `evals/tests/fakes.py`, `evals/tests/test_runner.py`

**Interfaces:**
- Consumes: the eval-mode `usage` response field (Task 2) and the `generation_thinking_level` override (Task 1).
- Produces: each full-mode case record carries `"usage"` (the token dict, or `None` when the fake or older cache entries lack it).

- [ ] **Step 1: Write the failing test**

In `evals/tests/fakes.py`, in `FakeApi.query`'s `body`, add:

```python
            "usage": (
                {"input_tokens": 100, "output_tokens": 20, "thinking_tokens": 30}
                if generate
                else None
            ),
```

Append to `evals/tests/test_runner.py`:

```python
def test_full_run_records_token_usage_and_keeps_it_through_the_cache(tmp_path, eval_file):
    expected = {"input_tokens": 100, "output_tokens": 20, "thinking_tokens": 30}
    first, _ = _run(tmp_path, eval_file, FakeApi(RESULTS), mode="full", judge=FakeJudge())
    assert first["cases"]["trample"]["full"]["usage"] == expected

    second, _ = _run(tmp_path, eval_file, FakeApi(RESULTS), mode="full", judge=FakeJudge())
    assert second["cases"]["trample"]["full"]["answer_cached"] is True
    assert second["cases"]["trample"]["full"]["usage"] == expected


def test_thinking_level_is_a_generation_key():
    from mtg_evals.runner import GENERATION_KEYS

    assert "generation_thinking_level" in GENERATION_KEYS
```

- [ ] **Step 2: Run to verify failure**

Run (in `evals/`): `python -m pytest tests/test_runner.py -v`
Expected: FAIL (`KeyError: 'usage'` and the missing key).

- [ ] **Step 3: Implement**

In `runner.py`, change `GENERATION_KEYS` to:

```python
GENERATION_KEYS = frozenset(
    {
        "gemini_model",
        "generation_temperature",
        "generation_max_tokens",
        "generation_thinking_level",
    }
)
```

In `_full`, add `"usage": response.get("usage"),` to the fresh `entry` dict, and `"usage": entry.get("usage"),` to the returned record (after `"generate_ms"`).

Create `evals/experiments/thinking-low.yaml`:

```yaml
description: "Low Gemini thinking level, as production runs (full mode only; retrieval is unaffected)."
overrides:
  generation_thinking_level: low
```

- [ ] **Step 4: Run tests and lint**

Run (in `evals/`): `python -m pytest && ruff check . && ruff format --check .`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add evals
git commit -m "feat(evals): thinking-low experiment and per-case token usage"
```

- [ ] **Step 6: Run the comparison (informational; it does not gate shipping)**

These runs call Gemini and cost a small amount. The local stack needs eval mode: set `MTG_API_EVAL_MODE=true` in `.env`, run `docker compose up -d --build backend`, and make sure the judge configured in `.env` (`EVAL_JUDGE_*`) is reachable (README "Evals").

```bash
make eval-full
make eval-full EXP=thinking-low
make eval-compare A=<first run file> B=<second run file>
```

(Run files are printed by each command and live under the evals runs directory.) Then total the tokens per run from the run JSONs:

```bash
python -c "
import json, sys
for path in sys.argv[1:]:
    run = json.load(open(path, encoding='utf-8'))
    usages = [c['full']['usage'] for c in run['cases'].values() if c.get('full') and c['full'].get('usage')]
    tot = {k: sum(u[k] for u in usages) for k in ('input_tokens', 'output_tokens', 'thinking_tokens')}
    print(path, len(usages), 'answers', tot)
" <baseline run file> <thinking-low run file>
```

Note: baseline answers served from the eval answer cache were generated before `usage` existed and carry `None`, so they aren't totaled. If the baseline shows 0 answers with usage, run it once more with a fresh cache for the comparison: rename `evals/.cache/answers` aside, rerun, then restore it.

Report back to the user: the answer-quality deltas from `eval-compare`, and the thinking tokens and estimated cost per answer for each run (using the prices from Task 13). Set `MTG_API_EVAL_MODE=false` again afterwards.

---

### Task 15: Deploy behind basic auth and verify in production

This task is done together with the user: it touches the live server and Coolify. Don't merge or deploy without their go-ahead.

- [ ] **Step 1:** Push the branch and open a PR (user approval first). Once CI is green and the user merges or points Coolify at the branch, Coolify redeploys. Basic auth stays on.
- [ ] **Step 2:** In Coolify, confirm that `SERVICE_PASSWORD_ADMIN` was generated and that the backend is healthy. The migration to 0003 runs on start.
- [ ] **Step 3:** Run `deploy/sync_data.py` once (README "Seeding the data") so production gets a `data_version` marker.
- [ ] **Step 4:** Walk the checklist from the spec's Rollout section, logged in through basic auth:
  - An answer records a `generated` row with non-zero tokens and cost (Usage page).
  - The same question again shows the cached badge, and a `cached` row appears with no new spend.
  - Set `MTG_API_DAILY_BUDGET_USD=0.01` in Coolify and redeploy the backend. A new question in a private window (not the admin) shows the "paused for today" notice. As the admin, it's still answered. Restore `1.0`.
  - Lower `MTG_API_IP_WINDOW_LLM_LIMIT` to 1 temporarily. The second quick question shows the quota notice. Restore it.
  - Send `curl -u admin:<basic-auth pw> -H 'X-Forwarded-For: 1.2.3.4' -H 'Content-Type: application/json' -d '{"query":"xff test"}' https://<domain>/api/v1/query`. The new row's IP bucket on the Usage page is your real IP, not `1.2.3.4`.
  - `/api/v1/queries` and `/api/v1/admin/usage` without the admin cookie return 401.
  - A 501-character query returns 422. Rapid-fire curls return 429.
  - After another `sync_data.py` run, a previously cached question is generated fresh.
- [ ] **Step 5:** The user creates the Google Cloud billing budget alert (~$30/month) on the Gemini project.

---

### Task 16: Open the site: remove basic auth (only after the user confirms Task 15)

**Files:**
- Modify: `docker-compose.prod.yml`, `mtg-web/nginx.conf`, `mtg-web/Dockerfile`, `README.md`
- Delete: `mtg-web/40-basic-auth.sh`

- [ ] **Step 1:** In `docker-compose.prod.yml`, remove `MTG_WEB_AUTH_USER` / `MTG_WEB_AUTH_PASSWORD` from the frontend and the basic-auth bullet (and `SERVICE_PASSWORD_WEB`) from the header comment.
- [ ] **Step 2:** In `nginx.conf`, remove the `include /etc/nginx/auth.conf;` line with its comment, and `auth_basic off;` in `location = /health`.
- [ ] **Step 3:** In `mtg-web/Dockerfile`, remove the `COPY --chmod=755 40-basic-auth.sh …` line. Run `git rm mtg-web/40-basic-auth.sh`.
- [ ] **Step 4:** README: rewrite "Access" to say the site is public, gated as described in "Cost gating", with admin access at `/login`. Drop the basic-auth instructions and the `SERVICE_PASSWORD_WEB` mention in Coolify setup.
- [ ] **Step 5:** Verify: `cd mtg-web && docker build .` succeeds; `docker compose up -d --build frontend && docker compose exec frontend nginx -t` reports ok; http://localhost:3000 loads with no password prompt.
- [ ] **Step 6:** Commit (`feat: open the site to the public`), push, and PR with user approval. After deploy, confirm the site loads without a prompt and the Usage page keeps counting.
