from datetime import UTC, datetime, timedelta

import pytest
from conftest import memory_engine
from sqlalchemy import select

from mtg_api.config import Settings
from mtg_api.llm import Generation, StreamAccumulator, StreamChunk
from mtg_api.usage import (
    Gate,
    answers_since,
    check_gate,
    check_gating_config,
    cost_usd,
    day_start,
    estimate_generation,
    estimate_tokens,
    finalize_usage,
    ip_bucket,
    llm_usage,
    record_usage,
    reserve_usage,
    spend_since,
    usage_summary,
    worst_case_cost,
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


def _reserve(engine, *, cost=0.5, bucket="203.0.113.7", at=NOW):
    return reserve_usage(
        engine, now=at, ip_bucket=bucket, is_admin=False, model="gemini-3.5-flash", cost=cost
    )


def test_estimate_tokens_rounds_up():
    assert estimate_tokens(0) == 0
    assert estimate_tokens(1) == 1
    assert estimate_tokens(8) == 2
    assert estimate_tokens(9) == 3


def test_worst_case_cost_is_prompt_plus_max_tokens():
    s = _settings(generation_max_tokens=1000)
    # 4M chars = 1M input tokens at $1; 1000 output tokens at $2/M.
    assert worst_case_cost(4_000_000, s) == pytest.approx(1.0 + 0.002)


def test_worst_case_cost_assumes_2048_without_max_tokens():
    assert worst_case_cost(0, _settings()) == pytest.approx(2048 * 2.0 / 1_000_000)


def test_pending_reservation_counts_against_quota_and_budget():
    engine = memory_engine()
    _reserve(engine, cost=0.6)
    assert answers_since(engine, "203.0.113.7", day_start(NOW)) == 1
    assert spend_since(engine, day_start(NOW)) == pytest.approx(0.6)


def test_finalize_replaces_the_reservation_in_place():
    engine = memory_engine()
    row_id = _reserve(engine, cost=0.6)
    g = Generation("x", input_tokens=10, output_tokens=2, thinking_tokens=3)
    finalize_usage(engine, row_id, outcome="generated", generation=g, cost=0.01)
    with engine.connect() as conn:
        row = conn.execute(select(llm_usage)).mappings().one()
    assert (row["outcome"], row["cost_usd"]) == ("generated", pytest.approx(0.01))
    assert (row["input_tokens"], row["output_tokens"], row["thinking_tokens"]) == (10, 2, 3)


def test_estimate_generation_uses_real_counts_once_the_stream_finished():
    acc = StreamAccumulator()
    acc.add(StreamChunk(text="abcd", input_tokens=100, output_tokens=5, finish_reason="STOP"))
    # No thoughtsTokenCount after a finished stream means no thinking.
    assert estimate_generation(acc, 4000, _settings()) == Generation(
        "abcd", input_tokens=100, output_tokens=5, thinking_tokens=0, finish_reason="STOP"
    )


def test_estimate_generation_fills_gaps_when_the_stream_broke_off():
    acc = StreamAccumulator()
    acc.add(StreamChunk(text="abcdefghi"))  # 9 chars, no usage, no finish reason
    g = estimate_generation(acc, 4001, _settings(generation_max_tokens=512))
    assert (g.input_tokens, g.output_tokens, g.thinking_tokens) == (1001, 3, 512)


def test_estimate_generation_prefers_counts_seen_before_the_break():
    acc = StreamAccumulator()
    acc.add(StreamChunk(text="ab", input_tokens=700, thinking_tokens=40))
    g = estimate_generation(acc, 4000, _settings())
    assert (g.input_tokens, g.output_tokens, g.thinking_tokens) == (700, 1, 40)


def test_estimate_generation_is_free_when_nothing_arrived():
    g = estimate_generation(StreamAccumulator(), 4000, _settings())
    assert (g.input_tokens, g.output_tokens, g.thinking_tokens) == (0, 0, 0)
