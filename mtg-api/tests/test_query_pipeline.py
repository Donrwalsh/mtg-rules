"""start() without HTTP: no TestClient, no dependency overrides, no ASGI scope."""

import pytest
from conftest import ChunksAnswerer, CountingAnswerer, FakeAnswerer, make_deps
from sqlalchemy import select

from mtg_api import query_pipeline
from mtg_api.config import settings
from mtg_api.history import list_history
from mtg_api.llm import StreamChunk
from mtg_api.models import QueryRequest
from mtg_api.query_pipeline import (
    AnswerStream,
    Caller,
    Delta,
    Done,
    Failed,
    Refused,
    Thinking,
    collect,
    sse_frames,
    start,
)
from mtg_api.streaming import join_all
from mtg_api.usage import llm_usage

CALLER = Caller(ip_bucket="1.2.3.4", is_admin=False, may_refresh=False)


def _ask(query="trample", deps=None, caller=CALLER, **fields):
    return start(QueryRequest(query=query, **fields), caller, deps or make_deps())


def test_generating_is_thinking_deltas_then_done():
    answerer = ChunksAnswerer(
        [StreamChunk(text="Yes "), StreamChunk(text="[1].", finish_reason="STOP")]
    )
    stream = _ask(deps=make_deps(answerer=answerer))
    events = list(stream.events)
    assert [type(e) for e in events] == [Thinking, Delta, Delta, Done]
    assert [s.number for s in stream.head.sources] == [1]
    assert events[-1].body.answer == "Yes [1]."
    assert events[-1].body.answer_complete is True


def test_failure_with_no_text_is_failed_then_done():
    stream = _ask(deps=make_deps(answerer=ChunksAnswerer([], RuntimeError("boom"))))
    events = list(stream.events)
    assert [type(e) for e in events] == [Thinking, Failed, Done]
    assert events[1] == Failed("boom")
    assert events[2].body.answer is None


def test_retrieval_only_is_just_done():
    answerer = CountingAnswerer()
    stream = _ask(deps=make_deps(answerer=answerer), generate=False)
    assert [type(e) for e in stream.events] == [Done]
    assert stream.head.sources == []
    assert answerer.calls == 0


def test_cache_hit_is_head_then_done():
    deps = make_deps()
    list(_ask(deps=deps).events)
    second = _ask(deps=deps)
    assert second.head.cached_at is not None
    events = list(second.events)
    assert [type(e) for e in events] == [Done]
    assert events[0].body.answer == "Yes [1]."


@pytest.mark.parametrize(
    ("fields", "status"),
    [({"query": "x" * 501}, 422), ({"query": "trample", "fresh": True}, 403)],
)
def test_refusals_before_any_work(fields, status):
    answerer = CountingAnswerer()
    with pytest.raises(Refused) as exc:
        start(QueryRequest(**fields), CALLER, make_deps(answerer=answerer))
    assert exc.value.status == status
    assert answerer.calls == 0


def test_busy_is_429_and_starts_nothing():
    answerer = CountingAnswerer()
    release = query_pipeline.generation_slots.acquire(CALLER.ip_bucket, total_limit=4, ip_limit=1)
    try:
        with pytest.raises(Refused) as exc:
            _ask(deps=make_deps(answerer=answerer))
    finally:
        release()
    assert exc.value.status == 429
    assert answerer.calls == 0


def test_admin_with_the_marker_may_ask_for_a_fresh_answer():
    admin = Caller(ip_bucket="1.2.3.4", is_admin=True, may_refresh=True)
    assert [type(e) for e in _ask(caller=admin, fresh=True).events][-1] is Done


def test_closing_the_relay_still_finishes_the_answer():
    deps = make_deps()
    frames = sse_frames(_ask(deps=deps))
    assert next(frames).startswith("event: results")
    frames.close()  # the visitor left
    join_all()
    with deps.engine.connect() as conn:
        outcomes = [r.outcome for r in conn.execute(select(llm_usage))]
    assert outcomes == ["generated"]
    assert list_history(deps.engine)[0]["answer"] == "Yes [1]."


def test_eval_mode_adds_eval_fields_and_skips_history_for_eval_runs(monkeypatch):
    monkeypatch.setattr(settings, "eval_mode", True)
    deps = make_deps()
    response = collect("trample", _ask(deps=deps, source="eval"))
    assert response.context_hash is not None
    assert response.generator == f"gemini:{settings.gemini_model}"
    assert response.usage is not None
    assert response.generation_error is None
    assert list_history(deps.engine) == []


def test_generation_overrides_build_a_per_request_answerer(monkeypatch):
    monkeypatch.setattr(settings, "eval_mode", True)
    built = []

    def fake_build(s):
        built.append(s)
        return FakeAnswerer("Per-request answer.")

    monkeypatch.setattr(query_pipeline, "build_answerer", fake_build)
    deps = make_deps(answerer=FakeAnswerer("Shared answer."))
    response = collect("trample", _ask(deps=deps, overrides={"generation_temperature": 0.7}))
    assert response.answer == "Per-request answer."
    assert built[0].generation_temperature == 0.7


def test_collect_is_the_done_body_as_a_response():
    stream = _ask()
    events = list(stream.events)
    expected = events[-1].body.response("trample", stream.head)
    assert collect("trample", AnswerStream(stream.head, iter(events))) == expected


def test_collect_without_done_raises():
    head = _ask(generate=False).head
    with pytest.raises(AssertionError, match="without done"):
        collect("trample", AnswerStream(head, iter([Thinking()])))


def test_sse_frames_frame_each_event_type():
    stream = _ask(generate=False)
    (done,) = list(stream.events)
    events = iter([Thinking(), Delta("a\nb"), Failed("boom"), done])
    frames = list(sse_frames(AnswerStream(stream.head, events)))
    assert [f.split("\n", 1)[0] for f in frames] == [
        "event: results",
        "event: thinking",
        "event: delta",
        "event: error",
        "event: done",
    ]
    assert frames[1] == "event: thinking\ndata: {}\n\n"
    assert frames[2] == 'event: delta\ndata: {"text": "a\\nb"}\n\n'
    assert frames[3] == 'event: error\ndata: {"message": "boom"}\n\n'


def test_collect_reads_the_stream_to_the_end():
    # The worker frees its generation slot after sending Done; the blocking
    # endpoint must not answer before that, or the caller's next question
    # can hit a 429.
    stream = _ask(generate=False)
    (done,) = list(stream.events)
    finished = []

    def events():
        yield done
        finished.append(True)

    collect("trample", AnswerStream(stream.head, events()))
    assert finished == [True]
