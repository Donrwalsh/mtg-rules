import json

import pytest
from conftest import ChunksAnswerer, admin_client, setup_trample
from fastapi.testclient import TestClient

from mtg_api import main, query_pipeline
from mtg_api.llm import StreamChunk
from mtg_api.main import app


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
    answerer = ChunksAnswerer(
        [StreamChunk(text="Yes "), StreamChunk(text="[1].", finish_reason="STOP")]
    )
    setup_trample(answerer=answerer)
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
    setup_trample()
    _stream({"query": "trample"})
    events = _events(_stream({"query": "trample"}))
    assert [name for name, _ in events] == ["results", "done"]
    assert events[0][1]["cached_at"] is not None
    assert events[1][1]["answer"] == "Yes [1]."


def test_degraded_request_streams_results_then_done(monkeypatch):
    monkeypatch.setattr(main.settings, "gating_enabled", True)
    monkeypatch.setattr(main.settings, "ip_daily_llm_limit", 0)
    setup_trample()
    events = _events(_stream({"query": "trample"}))
    assert [name for name, _ in events] == ["results", "done"]
    assert events[0][1]["degraded"] == "ip_quota"
    assert events[1][1]["answer"] is None


def test_failure_with_no_text_sends_error_then_done():
    setup_trample(answerer=ChunksAnswerer([], RuntimeError("Gemini 500")))
    events = _events(_stream({"query": "trample"}))
    assert [name for name, _ in events] == ["results", "thinking", "error", "done"]
    assert events[2][1] == {"message": "Gemini 500"}
    assert events[3][1]["answer"] is None


def test_validation_errors_are_plain_http():
    setup_trample()
    assert _stream({"query": "x" * 501}).status_code == 422
    assert _stream({"query": "trample", "fresh": True}).status_code == 403


def test_second_answer_from_one_ip_is_429_while_one_is_in_progress():
    setup_trample()
    release = query_pipeline.generation_slots.acquire("testclient", total_limit=4, ip_limit=1)
    try:
        assert _stream({"query": "trample"}).status_code == 429
    finally:
        release()
    assert _stream({"query": "trample"}).status_code == 200


def test_global_cap_is_429(monkeypatch):
    monkeypatch.setattr(main.settings, "max_concurrent_generations", 0)
    setup_trample()
    assert _stream({"query": "trample"}).status_code == 429


def test_cache_hits_and_retrieval_only_need_no_slot(monkeypatch):
    setup_trample()
    _stream({"query": "trample"})
    monkeypatch.setattr(main.settings, "max_concurrent_generations", 0)
    assert _stream({"query": "trample"}).status_code == 200  # cache hit
    assert _stream({"query": "other", "generate": False}).status_code == 200


def test_eval_mode_skips_the_caps(monkeypatch):
    monkeypatch.setattr(main.settings, "eval_mode", True)
    monkeypatch.setattr(main.settings, "max_concurrent_generations", 0)
    setup_trample()
    assert _stream({"query": "trample"}).status_code == 200


def test_admin_skips_the_per_ip_cap_but_not_the_global_one(monkeypatch):
    setup_trample()
    client = admin_client(monkeypatch)
    release = query_pipeline.generation_slots.acquire("testclient", total_limit=4, ip_limit=1)
    try:
        assert _stream({"query": "trample"}, client).status_code == 200
    finally:
        release()
    monkeypatch.setattr(main.settings, "max_concurrent_generations", 0)
    # A new question: "trample" is now a cache hit, which needs no slot.
    assert _stream({"query": "deathtouch"}, client).status_code == 429


def test_blocking_and_streamed_answers_agree():
    setup_trample()
    streamed = _events(_stream({"query": "deathtouch"}))
    blocking = TestClient(app).post("/api/v1/query", json={"query": "trample"}).json()
    head, done = streamed[0][1], streamed[-1][1]
    assert set(blocking) == (set(head) | set(done) | {"query"}) - {"sources"}
