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
    return a.admit(s or _settings(), ip_bucket=bucket, is_admin=admin, generate=generate, model="m")


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
