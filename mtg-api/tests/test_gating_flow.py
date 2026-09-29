from datetime import UTC, datetime

import pytest
from conftest import admin_client, memory_engine
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_query import _FailingEngine, _FakeHit, _override

from mtg_api import main
from mtg_api.answer_cache import answer_cache, cache_key
from mtg_api.history import list_history
from mtg_api.llm import Generation
from mtg_api.main import app, get_data_version
from mtg_api.usage import llm_usage, record_usage


class _CountingAnswerer:
    def __init__(self, raises=None, finish_reason="STOP"):
        self.calls = 0
        self._raises = raises
        self._finish_reason = finish_reason

    def generate(self, query, context):
        self.calls += 1
        if self._raises:
            raise self._raises
        return Generation(
            text="Yes [1].",
            input_tokens=1_000_000,
            output_tokens=0,
            thinking_tokens=0,
            finish_reason=self._finish_reason,
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
    engine, _ = _setup()
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


def test_truncated_answer_is_returned_but_not_cached(gated):
    _, answerer = _setup(answerer=_CountingAnswerer(finish_reason="MAX_TOKENS"))
    first = _post({"query": "trample"}).json()
    assert first["answer"] == "Yes [1]."
    second = _post({"query": "trample"}).json()
    assert answerer.calls == 2
    assert second["cached_at"] is None
    assert second["answer"] == "Yes [1]."


def test_failed_answers_are_not_cached():
    engine, answerer = _setup(answerer=_CountingAnswerer(raises=RuntimeError("boom")))
    _post({"query": "trample"})
    _post({"query": "trample"})
    assert answerer.calls == 2
    assert _outcomes(engine) == ["error", "error"]


def test_malformed_cache_row_falls_back_to_fresh_generation(gated):
    engine, answerer = _setup()
    _post({"query": "trample"})
    key = cache_key("trample", main.settings, "v1")
    with engine.begin() as conn:
        conn.execute(
            answer_cache.update()
            .where(answer_cache.c.key == key)
            .values(response={"answer": "old", "results": "not-a-list"})
        )
    body = _post({"query": "trample"}).json()
    assert answerer.calls == 2
    assert body["answer"] == "Yes [1]."
    assert body["cached_at"] is None


def test_cache_hit_is_free_even_when_the_daily_budget_is_exhausted(gated):
    engine, answerer = _setup()
    _post({"query": "trample"})
    record_usage(
        engine,
        now=datetime.now(UTC),
        ip_bucket="elsewhere",
        is_admin=False,
        outcome="generated",
        model="m",
        cost=1.0,  # exhausts the $1.00 daily budget for everyone
    )
    body = _post({"query": "trample"}).json()
    assert answerer.calls == 1
    assert body["cached_at"] is not None
    assert body["degraded"] is None
    # The global budget is exhausted, so the UI must not promise more
    # answers than the next new question will actually get.
    assert body["answers_remaining"] == 0


def test_new_data_version_misses_the_cache():
    _, answerer = _setup()
    _post({"query": "trample"})
    app.dependency_overrides[get_data_version] = lambda: "v2"
    assert _post({"query": "trample"}).json()["cached_at"] is None
    assert answerer.calls == 2


def test_fresh_is_admin_only(monkeypatch):
    _setup()
    assert _post({"query": "trample", "fresh": True}).status_code == 403


def test_admin_fresh_bypasses_and_replaces_the_cache(monkeypatch):
    _, answerer = _setup()
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
    _setup(engine=_FailingEngine())
    resp = _post({"query": "trample"})
    assert resp.status_code == 200
    assert resp.json()["answer"] == "Yes [1]."


def test_cache_hit_rows_from_before_enrichment_are_enriched(gated):
    engine, _ = _setup()
    app.dependency_overrides[main.get_rules_index] = lambda: main.RulesIndex(
        [
            {"rule_id": "702", "text": "Keyword Abilities", "parent_id": None},
            {"rule_id": "702.19", "text": "Trample", "parent_id": "702"},
            {"rule_id": "702.19b", "text": "T.", "parent_id": "702.19"},
        ]
    )
    first = _post({"query": "How does trample work?"}).json()
    assert first["citations"][0]["heading"] == "Trample"
    # Simulate a row cached before this change: strip the enrichment.
    with engine.begin() as conn:
        stored = dict(conn.execute(select(answer_cache.c.response)).scalar_one())
        for r in stored["results"]:
            r.pop("heading", None)
            r.pop("card", None)
        for c in stored["citations"]:
            c.pop("heading", None)
            c.pop("card", None)
        conn.execute(answer_cache.update().values(response=stored))

    second = _post({"query": "how does trample work"}).json()
    assert second["cached_at"] is not None
    assert second["results"][0]["heading"] == "Trample"
    assert second["citations"][0]["heading"] == "Trample"
