import json

import pytest
from conftest import admin_client, memory_engine
from fastapi.testclient import TestClient
from sqlalchemy import select
from starlette.requests import Request
from test_gating_flow import _ChunksAnswerer, _CountingAnswerer, _setup
from test_query import _FakeDenseModel, _FakeHit, _FakeQdrantClient, _FakeSparseModel

from mtg_api import main
from mtg_api.card_matcher import CardMatcher
from mtg_api.embedder import Embedder
from mtg_api.history import list_history
from mtg_api.keyword_matcher import KeywordMatcher
from mtg_api.llm import StreamChunk
from mtg_api.main import app
from mtg_api.models import QueryRequest
from mtg_api.rules_index import RulesIndex
from mtg_api.sparse_embedder import SparseEmbedder
from mtg_api.streaming import join_all
from mtg_api.usage import llm_usage


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def _events(resp):
    out = []
    for frame in resp.text.strip().split("\n\n"):
        name_line, data_line = frame.split("\n")
        out.append((name_line.removeprefix("event: "), json.loads(data_line[len("data: ") :])))
    return out


def _stream(json_body, client=None):
    return (client or TestClient(app)).post("/api/v1/query/stream", json=json_body)


def test_stream_sends_results_thinking_deltas_then_done():
    answerer = _ChunksAnswerer(
        [StreamChunk(text="Yes "), StreamChunk(text="[1].", finish_reason="STOP")]
    )
    _setup(answerer=answerer)
    resp = _stream({"query": "trample"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert resp.headers["x-accel-buffering"] == "no"
    events = _events(resp)
    assert [name for name, _ in events] == ["results", "thinking", "delta", "delta", "done"]
    head, done = events[0][1], events[-1][1]
    assert [s["number"] for s in head["sources"]] == [1]
    assert head["sources"][0]["url"] == "/rules/702.19b"
    assert head["results"][0]["cited"] is False
    assert [e[1]["text"] for e in events if e[0] == "delta"] == ["Yes ", "[1]."]
    assert done["answer"] == "Yes [1]."
    assert done["answer_complete"] is True
    assert done["results"][0]["cited"] is True
    assert "degraded" not in done


def test_cache_hit_streams_results_then_done():
    _setup()
    _stream({"query": "trample"})
    events = _events(_stream({"query": "trample"}))
    assert [name for name, _ in events] == ["results", "done"]
    assert events[0][1]["cached_at"] is not None
    assert events[1][1]["answer"] == "Yes [1]."


def test_degraded_request_streams_results_then_done(monkeypatch):
    monkeypatch.setattr(main.settings, "gating_enabled", True)
    monkeypatch.setattr(main.settings, "ip_daily_llm_limit", 0)
    _setup()
    events = _events(_stream({"query": "trample"}))
    assert [name for name, _ in events] == ["results", "done"]
    assert events[0][1]["degraded"] == "ip_quota"
    assert events[1][1]["answer"] is None


def test_failure_with_no_text_sends_error_then_done():
    _setup(answerer=_ChunksAnswerer([], RuntimeError("Gemini 500")))
    events = _events(_stream({"query": "trample"}))
    assert [name for name, _ in events] == ["results", "thinking", "error", "done"]
    assert events[2][1] == {"message": "Gemini 500"}
    assert events[3][1]["answer"] is None


def test_validation_errors_are_plain_http():
    _setup()
    assert _stream({"query": "x" * 501}).status_code == 422
    assert _stream({"query": "trample", "fresh": True}).status_code == 403


def test_second_answer_from_one_ip_is_429_while_one_is_in_progress():
    _setup()
    release = main.generation_slots.acquire("testclient", total_limit=4, ip_limit=1)
    try:
        assert _stream({"query": "trample"}).status_code == 429
    finally:
        release()
    assert _stream({"query": "trample"}).status_code == 200


def test_global_cap_is_429(monkeypatch):
    monkeypatch.setattr(main.settings, "max_concurrent_generations", 0)
    _setup()
    assert _stream({"query": "trample"}).status_code == 429


def test_cache_hits_and_retrieval_only_need_no_slot(monkeypatch):
    _setup()
    _stream({"query": "trample"})
    monkeypatch.setattr(main.settings, "max_concurrent_generations", 0)
    assert _stream({"query": "trample"}).status_code == 200  # cache hit
    assert _stream({"query": "other", "generate": False}).status_code == 200


def test_eval_mode_skips_the_caps(monkeypatch):
    monkeypatch.setattr(main.settings, "eval_mode", True)
    monkeypatch.setattr(main.settings, "max_concurrent_generations", 0)
    _setup()
    assert _stream({"query": "trample"}).status_code == 200


def test_admin_skips_the_per_ip_cap_but_not_the_global_one(monkeypatch):
    _setup()
    client = admin_client(monkeypatch)
    release = main.generation_slots.acquire("testclient", total_limit=4, ip_limit=1)
    try:
        assert _stream({"query": "trample"}, client).status_code == 200
    finally:
        release()
    monkeypatch.setattr(main.settings, "max_concurrent_generations", 0)
    # A new question: "trample" is now a cache hit, which needs no slot.
    assert _stream({"query": "deathtouch"}, client).status_code == 429


def test_closing_the_stream_early_still_finishes_the_answer():
    engine = memory_engine()
    hits = [_FakeHit("p1", 1.0, {"source_type": "rule", "rule_id": "702.19b", "text": "T."})]
    deps = main.QueryDeps(
        matcher=CardMatcher([]),
        keyword_matcher=KeywordMatcher([]),
        dense_embedder=Embedder(_FakeDenseModel()),
        sparse_embedder=SparseEmbedder(_FakeSparseModel()),
        client=_FakeQdrantClient(hits),
        answerer=_CountingAnswerer(),
        engine=engine,
        rules_index=RulesIndex([]),
        data_version="v1",
    )
    http_request = Request(
        {"type": "http", "method": "POST", "path": "/", "headers": [], "client": ("1.2.3.4", 1)}
    )
    frames = main._sse(main._start_query(QueryRequest(query="trample"), http_request, deps))
    assert next(frames).startswith("event: results")
    frames.close()  # the visitor left
    join_all()
    with engine.connect() as conn:
        outcomes = [r.outcome for r in conn.execute(select(llm_usage))]
    assert outcomes == ["generated"]
    assert list_history(engine)[0]["answer"] == "Yes [1]."
