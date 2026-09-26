import hashlib

import pytest
from conftest import memory_engine
from fastapi.testclient import TestClient
from test_query import _FakeAnswerer, _FakeHit, _override

import mtg_api.main as main
from mtg_api.history import list_history
from mtg_api.llm import PROMPT_VERSION, build_context
from mtg_api.main import app
from mtg_api.models import QueryResult


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def eval_mode(monkeypatch):
    monkeypatch.setattr(main.settings, "eval_mode", True)


class _RecordingAnswerer:
    def __init__(self):
        self.calls = []

    def generate(self, query, context):
        self.calls.append(query)
        return "An answer."


def _two_rule_hits():
    return [
        _FakeHit("p1", 1.0, {"source_type": "rule", "rule_id": "702.19b", "text": "Trample."}),
        _FakeHit("p2", 0.5, {"source_type": "rule", "rule_id": "702.2c", "text": "Deathtouch."}),
    ]


def _post(json):
    return TestClient(app).post("/api/v1/query", json=json)


def test_generate_false_skips_answerer():
    answerer = _RecordingAnswerer()
    _override(dense_points=_two_rule_hits(), answerer=answerer)
    resp = _post({"query": "trample", "generate": False})
    assert resp.status_code == 200
    assert answerer.calls == []
    body = resp.json()
    assert body["answer"] is None
    assert body["citations"] == []
    assert len(body["results"]) == 2


def test_overrides_rejected_when_eval_mode_off():
    _override()
    resp = _post({"query": "trample", "overrides": {"hybrid_top_k": 1}})
    assert resp.status_code == 403


def test_empty_overrides_are_fine_when_eval_mode_off():
    _override()
    assert _post({"query": "trample", "overrides": {}}).status_code == 200


def test_unknown_override_key_is_422(eval_mode):
    _override()
    resp = _post({"query": "trample", "overrides": {"groq_api_key": "x", "hybrid_top_k": 1}})
    assert resp.status_code == 422
    assert "groq_api_key" in resp.json()["detail"]


def test_bad_override_type_is_422(eval_mode):
    _override()
    resp = _post({"query": "trample", "overrides": {"hybrid_top_k": "lots"}})
    assert resp.status_code == 422
    assert "hybrid_top_k" in resp.json()["detail"]


def test_override_applies_to_its_request_only(eval_mode):
    default_top_k = main.settings.hybrid_top_k
    _override(dense_points=_two_rule_hits())

    overridden = _post({"query": "trample", "overrides": {"hybrid_top_k": 1}})
    following = _post({"query": "trample"})

    assert len(overridden.json()["results"]) == 1
    assert len(following.json()["results"]) == 2
    assert main.settings.hybrid_top_k == default_top_k


def test_generation_override_builds_a_per_request_answerer(eval_mode, monkeypatch):
    seen = []

    def fake_build_answerer(s):
        seen.append(s)
        return _FakeAnswerer("Per-request answer.")

    monkeypatch.setattr(main, "build_answerer", fake_build_answerer)
    _override(answerer=_FakeAnswerer("Shared answer."))

    resp = _post({"query": "q", "overrides": {"generation_temperature": 0}})

    assert resp.json()["answer"] == "Per-request answer."
    assert seen[0].generation_temperature == 0.0
    assert main.settings.generation_temperature is None


def test_retrieval_override_keeps_the_shared_answerer(eval_mode, monkeypatch):
    built = []
    monkeypatch.setattr(main, "build_answerer", lambda s: built.append(s))
    _override(answerer=_FakeAnswerer("Shared answer."))
    resp = _post({"query": "q", "overrides": {"hybrid_top_k": 3}})
    assert resp.json()["answer"] == "Shared answer."
    assert built == []


def test_eval_source_is_not_saved_to_history():
    engine = memory_engine()
    _override(engine=engine)
    _post({"query": "an eval question", "source": "eval"})
    _post({"query": "a user question"})
    assert [row["query"] for row in list_history(engine)] == ["a user question"]


def test_eval_fields_are_null_outside_eval_mode():
    _override()
    body = _post({"query": "q"}).json()
    assert body["context_hash"] is None
    assert body["prompt_version"] is None
    assert body["generator"] is None


def test_eval_fields_present_in_eval_mode(eval_mode):
    _override()
    body = _post({"query": "q", "generate": False}).json()
    assert body["prompt_version"] == PROMPT_VERSION
    assert body["generator"] == f"ollama:{main.settings.ollama_model}"


def test_generator_reflects_model_override(eval_mode, monkeypatch):
    monkeypatch.setattr(main, "build_answerer", lambda s: _FakeAnswerer())
    _override()
    body = _post({"query": "q", "overrides": {"ollama_model": "llama3"}}).json()
    assert body["generator"] == "ollama:llama3"


def test_context_hash_is_stable_and_hashes_the_llm_context(eval_mode):
    _override(dense_points=_two_rule_hits())
    first = _post({"query": "trample", "generate": False}).json()
    second = _post({"query": "trample"}).json()

    assert first["context_hash"] == second["context_hash"]
    results = [QueryResult(**r) for r in first["results"]]
    context, _ = build_context(results)
    assert first["context_hash"] == hashlib.sha256(context.encode("utf-8")).hexdigest()


def test_results_carry_source_type():
    _override(dense_points=_two_rule_hits())
    results = _post({"query": "trample"}).json()["results"]
    assert {r["source_type"] for r in results} == {"rule"}
