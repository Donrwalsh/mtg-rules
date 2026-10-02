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
            logger.warning(
                "Answer closed without settling; its reservation keeps the worst-case cost"
            )

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
