# Answer Allowance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the gate, slot and reservation code that `query_pipeline.start()` and `_AnswerWriter` coordinate by hand with one `allowance` module whose interface checks its own ordering, and make the daily budget exact within the process.

**Architecture:** New `mtg_api/allowance.py` owns `GenerationSlots` (moved from `streaming.py`), a process-wide lock, and three classes: `Allowances.admit()` returns an `Admission` with exactly one exit (`served_from_cache`, `retrieval_only`, `start_answer`); `start_answer` re-checks the gate, takes a slot and reserves worst-case spend under the lock and returns a `Spend` (`settle` / `cancel` / `close`). `usage.py` is unchanged and stays the ledger's storage layer. The pipeline gets an `Allowances` through `QueryDeps.allowances`.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy Core, pytest, ruff. Run everything from `mtg-api/` with `uv run`.

**Spec:** [docs/superpowers/specs/2026-10-02-answer-allowance-design.md](../specs/2026-10-02-answer-allowance-design.md). Background: [review doc 01](../../architecture/2026-09-30-streaming-review/01-answer-allowance.md).

## Global Constraints

- Branch `refactor/answer-allowance`, cut from `main` at `0c9f185`.
- Names: `Allowances`, `Admission`, `Spend`, `GenerationsBusy`, `GenerationSlots`, `LOCK_TIMEOUT_S = 5.0` (module constant, not a setting).
- `admit` takes plain `ip_bucket` and `is_admin`: `allowance.py` must not import `query_pipeline`.
- tracked = `generate and not s.eval_mode`; gated = tracked and `s.gating_enabled` and not admin. Decided inside the allowance only.
- Gate read failure fails closed (`Gate("global_budget", 0)`); reservation failure fails open; ledger write failures are logged and swallowed.
- `cancel()` settles as outcome `error` at cost 0 and frees the slot. `settle` / `cancel` / `close` are idempotent; the first ending wins.
- A second exit on an `Admission` raises `RuntimeError`, except `retrieval_only()` after `start_answer` returned `None`.
- The 429 detail text stays exactly `"Too many answers in progress. Try again in a moment."`.
- `usage.py` function signatures don't change.
- Every commit leaves `uv run pytest`, `uv run ruff check` and `uv run ruff format --check` green.
- No eval run (spec decision 8).

## File Structure

| File | Change |
|---|---|
| `mtg-api/src/mtg_api/allowance.py` | **Create.** `GenerationSlots`, `GenerationsBusy`, `Allowances`, `Admission`, `Spend`. |
| `mtg-api/tests/test_allowance.py` | **Create.** The interface is the test surface; also takes the slot tests from `test_streaming.py`. |
| `mtg-api/src/mtg_api/streaming.py` | Remove `GenerationSlots`; docstring no longer mentions caps. |
| `mtg-api/tests/test_streaming.py` | Remove the two slot tests. |
| `mtg-api/src/mtg_api/query_pipeline.py` | `QueryDeps.allowances`; `start()` and `_AnswerWriter` use the allowance; delete `_gate`, `_record`, `_reserve`, `_acquire_slot`, `_DEGRADED_OUTCOMES`, `generation_slots`. |
| `mtg-api/src/mtg_api/main.py` | `get_query_deps` builds `Allowances(engine)`. |
| `mtg-api/tests/conftest.py` | `make_deps(..., slots=None)` builds a fresh `Allowances`. |
| `mtg-api/tests/test_query_pipeline.py` | Busy test without the global; new cancel test. |
| `mtg-api/tests/test_query_stream.py` | Remove the two tests that hold the global slots. |
| `mtg-api/tests/test_gating_flow.py` | Remove the DB-peeking reservation test. |
| `mtg-api/tests/test_usage.py` | Remove the pending / finalize tests and their helper. |
| `docs/superpowers/specs/2026-09-30-streaming-answers-design.md` | Note in *Spend reservation*; mark the L153–156 gap note superseded. |
| `GLOSSARY.md` | **Create** at the repo root. |
| `docs/architecture/2026-09-30-streaming-review/README.md`, `01-answer-allowance.md` | Mark doc 01 done. |

---

### Task 1: The `allowance` module

**Files:**
- Create: `mtg-api/src/mtg_api/allowance.py`
- Create: `mtg-api/tests/test_allowance.py`
- Modify: `mtg-api/src/mtg_api/streaming.py` (remove `GenerationSlots`, lines 69–106, and the `Counter` import)
- Modify: `mtg-api/tests/test_streaming.py` (remove `test_slots_*`, the `GenerationSlots` import)
- Modify: `mtg-api/src/mtg_api/query_pipeline.py:53` (import `GenerationSlots` from `allowance`, only so this commit stays green; Task 2 removes the use)

**Interfaces:**
- Consumes: `usage.check_gate`, `usage.Gate`, `usage.record_usage`, `usage.reserve_usage`, `usage.finalize_usage`, `usage.worst_case_cost`, `usage.cost_usd`, `llm.Generation`, `config.Settings` (all unchanged).
- Produces:
  - `GenerationSlots()` with `.acquire(bucket, *, total_limit, ip_limit) -> Callable[[], None] | None` (moved, unchanged)
  - `class GenerationsBusy(Exception)`
  - `Allowances(engine, slots=<module singleton>, lock=<module singleton>, *, clock=<utc now>, lock_timeout=LOCK_TIMEOUT_S)`
  - `Allowances.admit(s, *, ip_bucket: str, is_admin: bool, generate: bool, model: str) -> Admission`
  - `Admission.degraded: str | None`, `Admission.answers_remaining: int | None`
  - `Admission.served_from_cache() -> int | None`, `.retrieval_only() -> int | None`, `.start_answer(prompt_chars: int) -> Spend | None` (raises `GenerationsBusy`)
  - `Spend.answers_remaining: int | None`, `.settle(generation, *, failed: bool)`, `.cancel()`, `.close()`

- [ ] **Step 1: Write the failing tests**

Create `mtg-api/tests/test_allowance.py`:

```python
"""The answer allowance through its interface: in-memory SQLite, fresh
slots and lock per test, a fixed clock."""

import logging
import threading
from datetime import UTC, datetime

import pytest
from conftest import FailingEngine, memory_engine
from sqlalchemy import select

from mtg_api.allowance import Allowances, GenerationsBusy, GenerationSlots
from mtg_api.config import Settings
from mtg_api.llm import Generation
from mtg_api.usage import llm_usage, record_usage, worst_case_cost

NOW = datetime(2026, 9, 29, 15, 0, tzinfo=UTC)
BUCKET = "203.0.113.7"


def _settings(**kw):
    base = {
        "gating_enabled": True,
        "gemini_input_price_per_mtok": 1.0,
        "gemini_output_price_per_mtok": 2.0,
        "daily_budget_usd": 1.0,
        "ip_daily_llm_limit": 3,
        "ip_window_llm_limit": 100,
        "max_concurrent_generations": 4,
        "max_concurrent_generations_per_ip": 1,
    }
    return Settings(_env_file=None, **{**base, **kw})


def _allowances(engine=None, *, slots=None, lock=None, **kw):
    return Allowances(
        engine or memory_engine(),
        slots or GenerationSlots(),
        lock or threading.Lock(),
        clock=lambda: NOW,
        **kw,
    )


def _admit(a, s=None, *, bucket=BUCKET, admin=False, generate=True):
    return a.admit(
        s or _settings(), ip_bucket=bucket, is_admin=admin, generate=generate, model="m"
    )


def _rows(engine):
    with engine.connect() as conn:
        return conn.execute(select(llm_usage).order_by(llm_usage.c.id)).mappings().all()


def _spent(engine, cost, *, bucket="198.51.100.1"):
    record_usage(
        engine, now=NOW, ip_bucket=bucket, is_admin=False, outcome="generated", model="m", cost=cost
    )


# -- GenerationSlots (moved from test_streaming.py) --------------------------


def test_slots_cap_per_ip_and_release():
    slots = GenerationSlots()
    release = slots.acquire("a", total_limit=4, ip_limit=1)
    assert release is not None
    assert slots.acquire("a", total_limit=4, ip_limit=1) is None
    assert slots.acquire("b", total_limit=4, ip_limit=1) is not None
    release()
    release()  # idempotent: doesn't free a slot it doesn't hold
    again = slots.acquire("a", total_limit=4, ip_limit=1)
    assert again is not None
    assert slots.acquire("a", total_limit=4, ip_limit=1) is None


def test_slots_cap_the_total_even_without_an_ip_limit():
    slots = GenerationSlots()
    assert slots.acquire("admin", total_limit=2, ip_limit=None) is not None
    assert slots.acquire("admin", total_limit=2, ip_limit=None) is not None
    assert slots.acquire("admin", total_limit=2, ip_limit=None) is None
    assert slots.acquire("visitor", total_limit=2, ip_limit=1) is None


# -- Writing an answer --------------------------------------------------------


def test_open_gate_start_settle_records_the_real_cost():
    engine = memory_engine()
    admission = _admit(_allowances(engine))
    assert admission.degraded is None
    assert admission.answers_remaining == 3
    spend = admission.start_answer(0)
    assert spend.answers_remaining == 2
    assert [r["outcome"] for r in _rows(engine)] == ["pending"]
    assert _rows(engine)[0]["cost_usd"] == pytest.approx(worst_case_cost(0, _settings()))

    spend.settle(Generation("x", input_tokens=10, output_tokens=2), failed=False)
    spend.close()
    (row,) = _rows(engine)
    assert row["outcome"] == "generated"
    assert row["cost_usd"] == pytest.approx((10 * 1.0 + 2 * 2.0) / 1_000_000)


def test_first_ending_wins():
    engine = memory_engine()
    spend = _admit(_allowances(engine)).start_answer(0)
    spend.settle(Generation("x"), failed=True)
    spend.settle(Generation("x", output_tokens=5), failed=False)
    spend.cancel()
    spend.close()
    spend.close()
    assert [r["outcome"] for r in _rows(engine)] == ["error"]


def test_close_without_settle_frees_the_slot_and_keeps_the_worst_case(caplog):
    engine = memory_engine()
    a = _allowances(engine)
    spend = _admit(a).start_answer(0)
    with caplog.at_level(logging.WARNING, logger="mtg_api.allowance"):
        spend.close()
    assert "worst-case" in caplog.text
    (row,) = _rows(engine)
    assert row["outcome"] == "pending"
    assert row["cost_usd"] == pytest.approx(worst_case_cost(0, _settings()))
    # The slot is free again (ip quota still has room: 2 left).
    assert _admit(a).start_answer(0) is not None


def test_cancel_is_a_zero_cost_error_and_frees_the_slot():
    engine = memory_engine()
    a = _allowances(engine)
    _admit(a).start_answer(0).cancel()
    (row,) = _rows(engine)
    assert (row["outcome"], row["cost_usd"]) == ("error", 0.0)
    assert _admit(a).start_answer(0) is not None


class _FirstWriteFails:
    """A real engine whose first write transaction fails."""

    def __init__(self, engine):
        self._engine = engine
        self._failed = False

    def connect(self):
        return self._engine.connect()

    def begin(self):
        if not self._failed:
            self._failed = True
            raise RuntimeError("insert failed")
        return self._engine.begin()


def test_failed_reservation_still_answers_and_settle_inserts():
    engine = memory_engine()
    spend = _admit(_allowances(_FirstWriteFails(engine))).start_answer(0)
    assert spend is not None  # fail open
    assert _rows(engine) == []
    spend.settle(Generation("x", output_tokens=2), failed=False)
    assert [r["outcome"] for r in _rows(engine)] == ["generated"]


# -- Degraded and late-degraded -------------------------------------------------


def test_gate_read_failure_fails_closed():
    admission = _admit(_allowances(FailingEngine()))
    assert (admission.degraded, admission.answers_remaining) == ("global_budget", 0)


def test_ip_quota_spent_records_degraded_ip():
    engine = memory_engine()
    for _ in range(3):
        _spent(engine, 0.0, bucket=BUCKET)
    admission = _admit(_allowances(engine))
    assert admission.degraded == "ip_quota"
    assert admission.retrieval_only() == 0
    assert _rows(engine)[-1]["outcome"] == "degraded_ip"


def test_cache_hit_under_a_spent_budget_promises_nothing():
    engine = memory_engine()
    _spent(engine, 1.0)
    admission = _admit(_allowances(engine))
    assert admission.degraded == "global_budget"
    assert admission.served_from_cache() == 0
    assert _rows(engine)[-1]["outcome"] == "cached"


def test_budget_closing_during_retrieval_degrades_late():
    engine = memory_engine()
    slots = GenerationSlots()
    admission = _admit(_allowances(engine, slots=slots))
    _spent(engine, 1.0)  # someone else's answer lands meanwhile
    assert admission.start_answer(0) is None
    assert admission.degraded == "global_budget"
    assert admission.retrieval_only() == 3
    assert [r["outcome"] for r in _rows(engine)] == ["generated", "degraded_global"]
    # No slot is held.
    assert slots.acquire(BUCKET, total_limit=4, ip_limit=1) is not None


def test_racing_answers_never_overshoot_the_budget():
    engine = memory_engine()
    worst = worst_case_cost(0, _settings())
    s = _settings(
        daily_budget_usd=worst * 2.5,  # room for exactly 3 reservations
        max_concurrent_generations=8,
    )
    a = _allowances(engine)
    admissions = [_admit(a, s, bucket=f"10.0.0.{i}") for i in range(8)]
    assert all(adm.degraded is None for adm in admissions)  # all saw an open gate
    barrier = threading.Barrier(8)
    spends = []

    def go(adm):
        barrier.wait()
        spends.append(adm.start_answer(0))

    threads = [threading.Thread(target=go, args=(adm,)) for adm in admissions]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(sp is not None for sp in spends) == 3


# -- Slots ---------------------------------------------------------------------


def test_per_ip_slot_taken_is_busy_but_not_for_an_admin():
    a = _allowances()
    _admit(a).start_answer(0)
    with pytest.raises(GenerationsBusy):
        _admit(a).start_answer(0)
    assert _admit(a, admin=True).start_answer(0) is not None


def test_global_slots_full_is_busy_even_for_an_admin():
    a = _allowances()
    with pytest.raises(GenerationsBusy):
        _admit(a, _settings(max_concurrent_generations=0), admin=True).start_answer(0)


def test_lock_timeout_is_busy():
    lock = threading.Lock()
    lock.acquire()
    a = _allowances(lock=lock, lock_timeout=0.01)
    with pytest.raises(GenerationsBusy):
        _admit(a).start_answer(0)


# -- Untracked and ungated -----------------------------------------------------


def test_eval_mode_is_unmetered_and_takes_no_slot():
    engine = memory_engine()
    s = _settings(eval_mode=True, max_concurrent_generations=0)
    admission = _admit(_allowances(engine), s)
    assert (admission.degraded, admission.answers_remaining) == (None, None)
    spend = admission.start_answer(0)
    assert spend.answers_remaining is None
    spend.settle(Generation("x"), failed=False)
    spend.close()
    assert _rows(engine) == []


def test_retrieval_only_without_generate_records_nothing():
    engine = memory_engine()
    assert _admit(_allowances(engine), generate=False).retrieval_only() is None
    assert _rows(engine) == []


def test_ungated_answers_are_still_recorded():
    engine = memory_engine()
    _spent(engine, 5.0)  # over budget, but gating is off
    spend = _admit(_allowances(engine), _settings(gating_enabled=False)).start_answer(0)
    assert spend.answers_remaining is None
    spend.settle(Generation("x"), failed=False)
    assert [r["outcome"] for r in _rows(engine)] == ["generated", "generated"]


def test_admin_is_not_gated_but_is_recorded():
    engine = memory_engine()
    _spent(engine, 5.0)
    admission = _admit(_allowances(engine), admin=True)
    assert (admission.degraded, admission.answers_remaining) == (None, None)
    admission.start_answer(0).settle(Generation("x"), failed=False)
    assert _rows(engine)[-1]["is_admin"] is True


# -- One exit per admission -------------------------------------------------------


@pytest.mark.parametrize(
    "first, second",
    [
        ("served_from_cache", "retrieval_only"),
        ("retrieval_only", "served_from_cache"),
        ("retrieval_only", "start_answer"),
        ("start_answer", "retrieval_only"),
    ],
)
def test_a_second_exit_raises(first, second):
    admission = _admit(_allowances())
    call = {
        "served_from_cache": admission.served_from_cache,
        "retrieval_only": admission.retrieval_only,
        "start_answer": lambda: admission.start_answer(0),
    }
    call[first]()
    with pytest.raises(RuntimeError):
        call[second]()


def test_a_degraded_admission_cannot_start_an_answer():
    engine = memory_engine()
    _spent(engine, 1.0)
    with pytest.raises(RuntimeError):
        _admit(_allowances(engine)).start_answer(0)
```

In `mtg-api/tests/test_streaming.py`, delete `test_slots_cap_per_ip_and_release` and `test_slots_cap_the_total_even_without_an_ip_limit` (lines 47–67), and change line 4 to:

```python
from mtg_api.streaming import AnswerJob, join_all, sse_event
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_allowance.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'mtg_api.allowance'`.

- [ ] **Step 3: Write `allowance.py`**

Create `mtg-api/src/mtg_api/allowance.py`:

```python
"""The answer allowance: whether one request may have an LLM answer, and
paying for it. Owns the per-IP and global gate, the concurrent-generation
slots and the usage rows (the ledger, stored by `usage.py`).

    admission = allowances.admit(...)        # the gate decision; takes nothing
    admission.served_from_cache()            # exactly one of these three
    admission.retrieval_only()
    spend = admission.start_answer(chars)    # re-check, slot, reserve: atomic
    spend.settle(generation, failed=...)     # on the worker
    spend.close()                            # always, in finally

Policy:
- Untracked requests (generate=False, or eval mode) are never gated, take
  no slot and write no rows.
- The gate fails closed: if the ledger can't be read, the request is
  treated as over the global budget.
- The reservation fails open: if its row can't be written, the answer is
  still written and settle() inserts the row at the end. Only a database
  failing between a successful gate read and the insert reaches this.
- The budget is exact within this process: start_answer re-checks the gate
  and reserves under one lock. Across processes it would need a database
  lock; the API runs as one process."""

from __future__ import annotations

import logging
import threading
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.engine import Engine

from mtg_api.config import Settings
from mtg_api.llm import Generation
from mtg_api.usage import (
    Gate,
    check_gate,
    cost_usd,
    finalize_usage,
    record_usage,
    reserve_usage,
    worst_case_cost,
)

logger = logging.getLogger(__name__)

# How long start_answer waits for another request's check-and-reserve before
# answering 429. That step is a few small queries; waiting longer means the
# database is stuck.
LOCK_TIMEOUT_S = 5.0

_DEGRADED_OUTCOMES = {"ip_quota": "degraded_ip", "global_budget": "degraded_global"}


class GenerationSlots:
    """How many answers are being written, in total and per IP bucket. In
    memory: the API runs as one process."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._total = 0
        self._by_bucket: Counter[str] = Counter()

    def acquire(
        self, bucket: str, *, total_limit: int, ip_limit: int | None
    ) -> Callable[[], None] | None:
        """A release function, or None when a cap is full. ip_limit=None
        leaves this request out of the per-IP count."""
        with self._lock:
            if self._total >= total_limit:
                return None
            if ip_limit is not None and self._by_bucket[bucket] >= ip_limit:
                return None
            self._total += 1
            if ip_limit is not None:
                self._by_bucket[bucket] += 1

        released = False

        def release() -> None:
            nonlocal released
            with self._lock:
                if released:
                    return
                released = True
                self._total -= 1
                if ip_limit is not None:
                    self._by_bucket[bucket] -= 1
                    if not self._by_bucket[bucket]:
                        del self._by_bucket[bucket]

        return release


# One of each per process. Doc 06 moves them into the app's lifespan.
_SLOTS = GenerationSlots()
_LOCK = threading.Lock()


class GenerationsBusy(Exception):
    """Every generation slot this caller may use is taken. Maps to HTTP 429."""


@dataclass(frozen=True)
class _Asker:
    """What every ledger row of one request records."""

    now: datetime
    ip_bucket: str
    is_admin: bool
    model: str


class Allowances:
    """The answer allowance for one process. Thread-safe."""

    def __init__(
        self,
        engine: Engine,
        slots: GenerationSlots = _SLOTS,
        lock: threading.Lock = _LOCK,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        lock_timeout: float = LOCK_TIMEOUT_S,
    ) -> None:
        self._engine = engine
        self._slots = slots
        self._lock = lock
        self._clock = clock
        self._lock_timeout = lock_timeout

    def admit(
        self, s: Settings, *, ip_bucket: str, is_admin: bool, generate: bool, model: str
    ) -> Admission:
        """Decide whether this request may have an LLM answer. Takes nothing."""
        tracked = generate and not s.eval_mode
        gated = tracked and s.gating_enabled and not is_admin
        asker = _Asker(self._clock(), ip_bucket, is_admin, model)
        gate = self._gate(s, asker) if gated else Gate(None, 0)
        return Admission(self, s, asker, tracked=tracked, gated=gated, gate=gate)

    def _gate(self, s: Settings, asker: _Asker) -> Gate:
        try:
            return check_gate(self._engine, s, asker.ip_bucket, asker.now)
        except Exception:
            # Fail closed: without the usage table there's no way to know the spend.
            logger.exception("Usage gate unavailable; answering retrieval-only")
            return Gate("global_budget", 0)

    def _record(self, asker: _Asker, **fields) -> None:
        try:
            record_usage(
                self._engine,
                now=asker.now,
                ip_bucket=asker.ip_bucket,
                is_admin=asker.is_admin,
                model=asker.model,
                **fields,
            )
        except Exception:
            logger.exception("Failed to record LLM usage")

    def _reserve(self, asker: _Asker, cost: float) -> int | None:
        try:
            return reserve_usage(
                self._engine,
                now=asker.now,
                ip_bucket=asker.ip_bucket,
                is_admin=asker.is_admin,
                model=asker.model,
                cost=cost,
            )
        except Exception:
            # Fail open (see the module docstring).
            logger.exception("Failed to reserve LLM usage")
            return None

    def _finalize(self, row_id: int, **fields) -> None:
        try:
            finalize_usage(self._engine, row_id, **fields)
        except Exception:
            logger.exception("Failed to finalize LLM usage")


class Admission:
    """One request's gate decision. Ends in exactly one exit:
    served_from_cache, retrieval_only or start_answer. The one exception:
    start_answer returning None (the gate closed while this request was
    retrieving) must be followed by retrieval_only."""

    def __init__(
        self,
        allowances: Allowances,
        s: Settings,
        asker: _Asker,
        *,
        tracked: bool,
        gated: bool,
        gate: Gate,
    ) -> None:
        self._a = allowances
        self._s = s
        self._asker = asker
        self._tracked = tracked
        self._gated = gated
        self.degraded: str | None = gate.degraded
        # Before this request uses one. None unless gated.
        self.answers_remaining: int | None = gate.answers_remaining if gated else None
        self._exit: str | None = None

    def _take(self, name: str) -> None:
        late = self._exit == "late_degraded" and name == "retrieval_only"
        if self._exit is not None and not late:
            raise RuntimeError(f"admission already ended with {self._exit}; {name} not allowed")
        self._exit = name

    def served_from_cache(self) -> int | None:
        """Records `cached`. Returns answers_remaining to show: 0 when the
        global budget is spent, since a cache hit doesn't promise new answers."""
        self._take("served_from_cache")
        if self._tracked:
            self._a._record(self._asker, outcome="cached")
        return 0 if self.degraded == "global_budget" else self.answers_remaining

    def retrieval_only(self) -> int | None:
        """Records degraded_ip / degraded_global when degraded, nothing
        otherwise. Returns answers_remaining to show."""
        self._take("retrieval_only")
        if self._tracked and self.degraded:
            self._a._record(self._asker, outcome=_DEGRADED_OUTCOMES[self.degraded])
        return self.answers_remaining

    def start_answer(self, prompt_chars: int) -> Spend | None:
        """Atomically: re-check the gate, take a slot, reserve the worst-case
        cost. Raises GenerationsBusy when no slot is free (or another
        request holds the lock too long). Returns None when the gate closed
        since admit(); then call retrieval_only()."""
        self._take("start_answer")
        if self.degraded:
            raise RuntimeError("a degraded admission can't start an answer")
        if not self._tracked:
            return Spend(self._a, self._s, self._asker, tracked=False)
        a, s, asker = self._a, self._s, self._asker
        if not a._lock.acquire(timeout=a._lock_timeout):
            raise GenerationsBusy
        try:
            remaining = None
            if self._gated:
                gate = a._gate(s, asker)
                if gate.degraded:
                    self.degraded = gate.degraded
                    self.answers_remaining = gate.answers_remaining
                    self._exit = "late_degraded"
                    return None
                remaining = max(0, gate.answers_remaining - 1)
            release = a._slots.acquire(
                asker.ip_bucket,
                total_limit=s.max_concurrent_generations,
                ip_limit=None if asker.is_admin else s.max_concurrent_generations_per_ip,
            )
            if release is None:
                raise GenerationsBusy
            row = a._reserve(asker, worst_case_cost(prompt_chars, s))
        finally:
            a._lock.release()
        return Spend(
            a, s, asker, tracked=True, row=row, release=release, answers_remaining=remaining
        )


class Spend:
    """One answer being written: its slot and its reservation. End it with
    settle() (written) or cancel() (never started), then close()."""

    def __init__(
        self,
        allowances: Allowances,
        s: Settings,
        asker: _Asker,
        *,
        tracked: bool,
        row: int | None = None,
        release: Callable[[], None] | None = None,
        answers_remaining: int | None = None,
    ) -> None:
        self._a = allowances
        self._s = s
        self._asker = asker
        self._tracked = tracked
        self._row = row
        self._release = release
        # After this answer. None unless gated.
        self.answers_remaining = answers_remaining
        self._ended = False
        self._closed = False

    def settle(self, generation: Generation, *, failed: bool) -> None:
        """Replace the worst-case reservation with the real (or estimated) cost."""
        self._end("error" if failed else "generated", generation, cost_usd(generation, self._s))

    def cancel(self) -> None:
        """The answer never started: no cost, and the slot is freed."""
        self._end("error", Generation(""), 0.0)
        self.close()

    def close(self) -> None:
        """Free the slot. If the answer was never settled, its reservation
        keeps the worst-case cost: the safe failure."""
        if self._closed:
            return
        self._closed = True
        if self._release:
            self._release()
        if self._tracked and not self._ended:
            logger.warning("Answer closed without settling; its reservation keeps the worst-case cost")

    def _end(self, outcome: str, generation: Generation, cost: float) -> None:
        if self._ended:
            return
        self._ended = True
        if not self._tracked:
            return
        if self._row is None:
            # The reservation insert failed; record the answer instead.
            self._a._record(self._asker, outcome=outcome, generation=generation, cost=cost)
        else:
            self._a._finalize(self._row, outcome=outcome, generation=generation, cost=cost)
```

Note: line length. If `ruff format` wraps the `logger.warning(...)` line, accept its output.

In `mtg-api/src/mtg_api/streaming.py`: delete the `GenerationSlots` class (lines 69–106) and `from collections import Counter`. Change the docstring to:

```python
"""Plumbing for streamed answers: SSE framing, and a worker thread whose
events can be relayed to a client (or to no one). Knows nothing about
queries."""
```

In `mtg-api/src/mtg_api/query_pipeline.py`, change line 53 so the suite stays green until Task 2:

```python
from mtg_api.allowance import GenerationSlots
from mtg_api.streaming import AnswerJob, Emit, sse_event
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_allowance.py tests/test_streaming.py -q`
Expected: all pass.

Run: `uv run pytest -q && uv run ruff check && uv run ruff format --check`
Expected: all green (run `uv run ruff format` first if only formatting differs).

- [ ] **Step 5: Commit**

```bash
git add mtg-api/src/mtg_api/allowance.py mtg-api/tests/test_allowance.py mtg-api/src/mtg_api/streaming.py mtg-api/tests/test_streaming.py mtg-api/src/mtg_api/query_pipeline.py
git commit -m "feat(api): the answer allowance, with slots moved out of streaming"
```

---

### Task 2: The pipeline uses the allowance

**Files:**
- Modify: `mtg-api/src/mtg_api/query_pipeline.py` (imports L53–63; delete L138, L153–159, L162–167, L219, L250–255, L403–411; rewrite `start()` L258–347, `_cached()`, `_AnswerWriter`)
- Modify: `mtg-api/src/mtg_api/main.py:145-166` (`get_query_deps`)
- Modify: `mtg-api/tests/conftest.py:227-245` (`make_deps`)
- Modify: `mtg-api/tests/test_query_pipeline.py:84-93` and add a test
- Modify: `mtg-api/tests/test_query_stream.py` (delete `test_second_answer_from_one_ip_is_429_while_one_is_in_progress`, `test_admin_skips_the_per_ip_cap_but_not_the_global_one`)
- Modify: `mtg-api/tests/test_gating_flow.py` (delete `test_reservation_is_pending_while_the_answer_is_written`)
- Modify: `mtg-api/tests/test_usage.py` (delete `_reserve`, `test_pending_reservation_counts_against_quota_and_budget`, `test_finalize_replaces_the_reservation_in_place`)

**Interfaces:**
- Consumes (from Task 1): `Allowances(engine, slots, lock)`, `Allowances.admit(s, *, ip_bucket, is_admin, generate, model) -> Admission`, `Admission.degraded`, `.served_from_cache()`, `.retrieval_only()`, `.start_answer(prompt_chars) -> Spend | None`, `GenerationsBusy`, `Spend.answers_remaining`, `.settle(generation, *, failed)`, `.cancel()`, `.close()`, `GenerationSlots`.
- Produces: `QueryDeps.allowances: Allowances` (last field); `make_deps(..., slots: GenerationSlots | None = None)` in conftest.

- [ ] **Step 1: Update the tests first**

In `mtg-api/tests/conftest.py`, replace `make_deps` with:

```python
def make_deps(
    *,
    hits=None,
    answerer=None,
    engine=None,
    cards=None,
    rules=None,
    data_version="v1",
    slots=None,
):
    """QueryDeps built directly, with no FastAPI involved: one trample rule
    hit unless `hits` says otherwise. Its own generation slots and lock, so
    tests don't share the app's."""
    import threading

    from mtg_api.allowance import Allowances, GenerationSlots
    from mtg_api.card_matcher import CardMatcher
    from mtg_api.embedder import Embedder
    from mtg_api.keyword_matcher import KeywordMatcher
    from mtg_api.query_pipeline import QueryDeps
    from mtg_api.rules_index import RulesIndex
    from mtg_api.sparse_embedder import SparseEmbedder

    engine = engine or memory_engine()
    return QueryDeps(
        matcher=CardMatcher(cards or []),
        keyword_matcher=KeywordMatcher(rules or []),
        dense_embedder=Embedder(FakeDenseModel()),
        sparse_embedder=SparseEmbedder(FakeSparseModel()),
        client=FakeQdrantClient(trample_hits() if hits is None else hits),
        answerer=answerer or CountingAnswerer(),
        engine=engine,
        rules_index=RulesIndex(rules or []),
        data_version=data_version,
        allowances=Allowances(engine, slots or GenerationSlots(), threading.Lock()),
    )
```

In `mtg-api/tests/test_query_pipeline.py`, replace `test_busy_is_429_and_starts_nothing` with the two tests below, and add `FakeQdrantClient`, `trample_hits` to the `conftest` import and `from mtg_api.allowance import GenerationSlots` to the imports. Remove `from mtg_api import query_pipeline` if nothing else uses it (ruff will say).

```python
class _CountingClient(FakeQdrantClient):
    def __init__(self, *args):
        super().__init__(*args)
        self.searches = 0

    def query_points(self, *args, **kwargs):
        self.searches += 1
        return super().query_points(*args, **kwargs)


def test_busy_is_429_after_retrieval_and_starts_nothing():
    answerer = CountingAnswerer()
    slots = GenerationSlots()
    slots.acquire(CALLER.ip_bucket, total_limit=4, ip_limit=1)
    deps = make_deps(answerer=answerer, slots=slots)
    deps.client = _CountingClient(trample_hits())
    with pytest.raises(Refused) as exc:
        _ask(deps=deps)
    assert exc.value.status == 429
    assert exc.value.detail == "Too many answers in progress. Try again in a moment."
    assert deps.client.searches > 0  # the slot is taken after retrieval now
    assert answerer.calls == 0


def test_an_answer_that_never_starts_costs_nothing_and_frees_its_slot(monkeypatch):
    class _Unstartable:
        def __init__(self, work):
            pass

        def start(self):
            raise RuntimeError("no threads left")

    deps = make_deps()
    monkeypatch.setattr(query_pipeline, "AnswerJob", _Unstartable)
    with pytest.raises(RuntimeError):
        _ask(deps=deps)
    with deps.engine.connect() as conn:
        rows = conn.execute(select(llm_usage)).mappings().all()
    assert [(r["outcome"], r["cost_usd"]) for r in rows] == [("error", 0.0)]
    monkeypatch.undo()
    assert [type(e) for e in _ask(query="deathtouch", deps=deps).events][-1] is Done
```

(The second test needs `from mtg_api import query_pipeline` to stay.)

In `mtg-api/tests/test_query_stream.py`, delete `test_second_answer_from_one_ip_is_429_while_one_is_in_progress` and `test_admin_skips_the_per_ip_cap_but_not_the_global_one`; `test_allowance.py` covers both. Keep `test_global_cap_is_429` as the HTTP check that busy maps to 429. Remove the `query_pipeline` / `admin_client` imports if ruff reports them unused.

In `mtg-api/tests/test_gating_flow.py`, delete `test_reservation_is_pending_while_the_answer_is_written` (`test_allowance.py::test_open_gate_start_settle_records_the_real_cost` covers it). Remove imports ruff then reports unused.

In `mtg-api/tests/test_usage.py`, delete `_reserve`, `test_pending_reservation_counts_against_quota_and_budget` and `test_finalize_replaces_the_reservation_in_place`, and drop `finalize_usage` and `reserve_usage` from the import list (plus `select` / `Generation` if ruff reports them unused).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_query_pipeline.py -q`
Expected: FAIL. `QueryDeps.__init__() got an unexpected keyword argument 'allowances'`.

- [ ] **Step 3: Rewrite the pipeline**

In `mtg-api/src/mtg_api/query_pipeline.py`:

Imports: replace the Task 1 `GenerationSlots` import line and the `usage` import block with:

```python
from mtg_api.allowance import Allowances, GenerationsBusy, Spend
from mtg_api.streaming import AnswerJob, Emit, sse_event
from mtg_api.usage import estimate_generation
```

and drop `Generation` from the `mtg_api.llm` import, and `Callable` from `collections.abc`, if ruff reports them unused.

Delete: `_DEGRADED_OUTCOMES`, `_gate`, `_record`, `generation_slots = GenerationSlots()`, `_reserve`, `_acquire_slot`.

Add the 429 text as a constant beside `Refused`:

```python
BUSY = "Too many answers in progress. Try again in a moment."
```

Add `allowances` as the last field of `QueryDeps`:

```python
    rules_index: RulesIndex
    data_version: str
    allowances: Allowances
```

Replace `start()` with:

```python
def start(request: QueryRequest, caller: Caller, deps: QueryDeps) -> AnswerStream:
    """Answer one question. Raises Refused (422 too long or a bad override,
    403 fresh or overrides not allowed, 429 too many answers in progress)
    before any work that costs money. Otherwise returns at once with the
    head; when an answer is being written, `events` relays it from a worker
    that finishes, records and caches it whether or not anyone keeps
    reading."""
    s, answerer = _resolve(request, caller, deps)
    now = datetime.now(UTC)
    admission = deps.allowances.admit(
        s,
        ip_bucket=caller.ip_bucket,
        is_admin=caller.is_admin,
        generate=request.generate,
        model=answer_model(s),
    )

    key = (
        cache_key(request.query, s, deps.data_version)
        if request.generate and not s.eval_mode and s.answer_cache_enabled
        else None
    )
    if key and not request.fresh:
        hit = _cached(request, deps, key)
        if hit is not None:
            hit.answers_remaining = admission.served_from_cache()
            return _finished(deps, s, request, hit, cached=True)

    spend: Spend | None = None
    try:
        results = retrieve(request.query, s, deps.retrieval())
        context, sources = build_context(results)
        # After build_context: display data must never reach the LLM.
        enrich_results(results, deps.matcher, deps.rules_index)
        eval_fields = _eval_fields(s, context)

        if request.generate and admission.degraded is None:
            try:
                spend = admission.start_answer(prompt_chars(request.query, context))
            except GenerationsBusy:
                raise Refused(429, BUSY) from None
        remaining = spend.answers_remaining if spend else admission.retrieval_only()

        head = QueryResponse(
            query=request.query,
            results=results,
            degraded=admission.degraded,
            answers_remaining=remaining,
            **eval_fields,
        )
        if spend is None:
            return _finished(deps, s, request, head)

        live_sources = [citation_for(n, r) for n, r in sources.items()]
        enrich_citations(live_sources, deps.matcher, deps.rules_index)
        writer = _AnswerWriter(
            s=s,
            request=request,
            answerer=answerer,
            deps=deps,
            context=context,
            sources=sources,
            results=results,
            now=now,
            spend=spend,
            key=key,
            answers_remaining=remaining,
            eval_fields=eval_fields,
        )
        job = AnswerJob(writer.run).start()
    except BaseException:
        if spend:
            spend.cancel()
        raise
    return AnswerStream(StreamHead.of(head, live_sources), job.events())
```

Replace `_cached` (it no longer takes `remaining`; `start()` sets it from `served_from_cache()`):

```python
def _cached(request: QueryRequest, deps: QueryDeps, key: str) -> QueryResponse | None:
    """The cached answer to this question, or None (a miss, or a row that no
    longer parses, which the new answer will overwrite)."""
    hit = _cache_get(deps.engine, key)
    if hit is None:
        return None
    stored, generated_at = hit
    try:
        response = QueryResponse(**stored, query=request.query, cached_at=generated_at)
    except Exception:
        logger.exception("Malformed answer-cache row for key %s; treating as a miss", key)
        return None
    # Only complete answers are cached; rows from before the field.
    if response.answer_complete is None and response.answer:
        response.answer_complete = True
    # Rows cached before enrichment existed lack card/heading.
    enrich_results(response.results, deps.matcher, deps.rules_index)
    enrich_citations(response.citations, deps.matcher, deps.rules_index)
    return response
```

In `_AnswerWriter`:
- Fields: remove `record`, `tracked`, `reservation`, `release`; add `now: datetime` (after `results`) and `spend: Spend` (after `now`).
- Docstring stays.
- In `run`, replace `self._finalize("error" if failure else "generated", generation)` with:

```python
            self.spend.settle(generation, failed=failure is not None)
```

- Replace `self.record["now"]` in the `_cache_put` call with `self.now`.
- Replace the `finally` block with:

```python
        finally:
            self.spend.close()
```

- Delete `_finalize`.

In `mtg-api/src/mtg_api/main.py`, add `from mtg_api.allowance import Allowances` and pass it as the last positional argument in `get_query_deps`:

```python
    return QueryDeps(
        matcher,
        keyword_matcher,
        dense_embedder,
        sparse_embedder,
        client,
        answerer,
        engine,
        rules_index,
        data_version,
        Allowances(engine),
    )
```

`Allowances(engine)` uses the module's process-wide slots and lock, so every request shares them; tests that override `get_db_engine` get an allowance over their engine.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest -q`
Expected: all pass. In particular, unchanged and passing: `test_gating_flow.py` (IP quota, global budget, admin exempt, cache hits, DB failure fails closed, ungated DB failure still answers), `test_query_stream.py::test_cache_hits_and_retrieval_only_need_no_slot`, `::test_global_cap_is_429`, `::test_eval_mode_skips_the_caps`, `test_query_pipeline.py::test_closing_the_relay_still_finishes_the_answer`.

Run: `uv run ruff check && uv run ruff format --check`
Expected: clean. Fix any unused-import reports in the edited files.

Run: `grep -rn "generation_slots\|_reserve\|_finalize\|_DEGRADED_OUTCOMES\|tracked" mtg-api/src/mtg_api/query_pipeline.py`
Expected: no matches.

- [ ] **Step 5: Commit**

```bash
git add mtg-api/src/mtg_api/query_pipeline.py mtg-api/src/mtg_api/main.py mtg-api/tests/
git commit -m "refactor(api): the query pipeline asks the answer allowance

The gate, slot and reservation now go through one interface that checks
its own order. The slot is taken after retrieval, under the same lock as
a fresh gate check and the reservation, so the daily budget is exact
within the process. An answer that never starts settles as a zero-cost
error instead of keeping its worst-case reservation."
```

---

### Task 3: Docs and glossary

**Files:**
- Modify: `docs/superpowers/specs/2026-09-30-streaming-answers-design.md` (L153–156 and the *Spend reservation* section, L215–229)
- Create: `GLOSSARY.md`
- Modify: `docs/architecture/2026-09-30-streaming-review/README.md`
- Modify: `docs/architecture/2026-09-30-streaming-review/01-answer-allowance.md`

- [ ] **Step 1: Streaming spec**

After the step-6 paragraph at L153–156 ("Reserve a `pending` usage row …"), add:

```markdown
   *Superseded 2026-10-02:* the answer allowance
   ([spec](2026-10-02-answer-allowance-design.md)) takes the slot after
   retrieval and re-checks the gate, takes the slot and reserves under one
   lock, so there is no gap any more.
```

At the end of the *Spend reservation* section (after the "A row left `pending` …" bullet), add:

```markdown
- **Answer allowance (2026-10-02).** `allowance.py` owns the gate, the
  slots and these rows behind one interface (`admit` → `start_answer` →
  `settle` / `cancel` / `close`). The check and the reservation happen
  under one process-wide lock, so `daily_budget_usd` is exact within the
  process. An answer whose job never starts is settled as `error` at zero
  cost instead of staying `pending`.
```

- [ ] **Step 2: Glossary**

Create `GLOSSARY.md` at the repo root:

```markdown
# Glossary

Domain terms as the code and the docs use them. The full glossary is
tracked in [#24](https://github.com/Donrwalsh/mtg-rules/issues/24).

**Answer allowance**: the permission for one request to have an LLM
answer written, and paying for it: the gate, a generation slot and a
usage row. Lives in `mtg-api/src/mtg_api/allowance.py`.

**Admission**: one request's gate decision. It ends in exactly one way:
served from the cache, retrieval-only, or an answer started.

**Spend**: one answer being written. Holds its generation slot and its
worst-case reservation until it is settled (real cost), cancelled (never
started, no cost) and closed (slot freed).

**Generation slot**: one of the in-memory places for an answer being
written right now. Capped in total (`max_concurrent_generations`) and per
IP bucket (`max_concurrent_generations_per_ip`; admins aren't counted per
IP). No free slot means HTTP 429.
```

- [ ] **Step 3: Review docs**

In `docs/architecture/2026-09-30-streaming-review/README.md`, change the doc 1 table row's Strength cell from `Strong` to `Strong · **done** (2026-10-02)`, and the doc 2 row's the same way with its own date, `Strong · **done** (2026-10-01)`.

At the top of `01-answer-allowance.md`, under the first quote block, add:

```markdown
> **Status: done.** Decisions on the open questions:
> [spec 2026-10-02](../../superpowers/specs/2026-10-02-answer-allowance-design.md).
```

- [ ] **Step 4: Commit**

```bash
git add GLOSSARY.md docs/
git commit -m "docs: answer allowance in the streaming spec, glossary and review"
```

---

### Task 4: Verify

**Files:** none changed.

- [ ] **Step 1: Full suite and lint**

Run from `mtg-api/`: `uv run pytest -q && uv run ruff check && uv run ruff format --check`
Expected: all green. Paste the summary line in the PR description.

- [ ] **Step 2: Manual budget check on the dev stack (Ollama, no Gemini spend)**

Dev compose doesn't forward the gating variables, so start the backend with them set. From the repo root, with the rest of the stack up (`docker compose up -d qdrant postgres redis`), and the local `.env` choosing `MTG_API_ANSWER_PROVIDER=ollama`:

```bash
docker compose stop backend
docker compose run --rm --service-ports \
  -e MTG_API_GATING_ENABLED=true \
  -e MTG_API_GEMINI_INPUT_PRICE_PER_MTOK=1.0 \
  -e MTG_API_GEMINI_OUTPUT_PRICE_PER_MTOK=2.0 \
  -e MTG_API_DAILY_BUDGET_USD=0.015 \
  -e MTG_API_IP_DAILY_LLM_LIMIT=100 \
  -e MTG_API_IP_WINDOW_LLM_LIMIT=100 \
  -e MTG_API_MAX_CONCURRENT_GENERATIONS_PER_IP=4 \
  backend
```

In another shell, fire four different questions at once:

```bash
for q in trample deathtouch lifelink flying; do
  curl -sN -X POST localhost:8000/api/v1/query/stream \
    -H 'content-type: application/json' -d "{\"query\":\"what does $q do\"}" \
    | grep -m1 -o '"degraded":[^,]*' &
done; wait
```

Expected: one worst case costs about $0.009 at those prices, so the first two requests get `"degraded":null` and the other two get `"degraded":"global_budget"`. Before this change, all four would be `null`. Then check the admin usage page (`/admin/usage`, password from `.env`): today's spend ≤ budget + one worst case. Note the actual numbers in the PR. If the worst case on this data isn't near $0.009, adjust `MTG_API_DAILY_BUDGET_USD` to about 1.5× one answer's `pending` cost from the first run, then reset the day's rows (`DELETE FROM llm_usage` in the dev Postgres) and repeat.

Afterwards: Ctrl+C the run, then `docker compose up -d backend`.

- [ ] **Step 3: Finish the branch**

Use superpowers:finishing-a-development-branch. PR title: `refactor(api): the answer allowance (review doc 01)`. The body links the spec and lists the two behaviour changes (exact budget; 429 after retrieval) and the cancel change.
