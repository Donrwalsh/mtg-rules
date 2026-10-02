import hashlib

import pytest
from conftest import FakeAnswerer, FakeHit, StreamsFromGenerate, memory_engine, override
from fastapi.testclient import TestClient
from pydantic import SecretStr

from mtg_api import main, query_pipeline
from mtg_api.history import list_history
from mtg_api.llm import PROMPT_VERSION, GeminiAnswerer, Generation, OllamaAnswerer, build_context
from mtg_api.main import app
from mtg_api.models import QueryResult


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def eval_mode(monkeypatch):
    monkeypatch.setattr(main.settings, "eval_mode", True)


class _RecordingAnswerer(StreamsFromGenerate):
    def __init__(self):
        self.calls = []

    def generate(self, query, context):
        self.calls.append(query)
        return Generation(text="An answer.", finish_reason="STOP")


def _two_rule_hits():
    return [
        FakeHit("p1", 1.0, {"source_type": "rule", "rule_id": "702.19b", "text": "Trample."}),
        FakeHit("p2", 0.5, {"source_type": "rule", "rule_id": "702.2c", "text": "Deathtouch."}),
    ]


def _post(json):
    return TestClient(app).post("/api/v1/query", json=json)


def test_generate_false_skips_answerer():
    answerer = _RecordingAnswerer()
    override(dense_points=_two_rule_hits(), answerer=answerer)
    resp = _post({"query": "trample", "generate": False})
    assert resp.status_code == 200
    assert answerer.calls == []
    body = resp.json()
    assert body["answer"] is None
    assert body["citations"] == []
    assert len(body["results"]) == 2


def test_overrides_rejected_when_eval_mode_off():
    override()
    resp = _post({"query": "trample", "overrides": {"hybrid_top_k": 1}})
    assert resp.status_code == 403


def test_empty_overrides_are_fine_when_eval_mode_off():
    override()
    assert _post({"query": "trample", "overrides": {}}).status_code == 200


def test_unknown_override_key_is_422(eval_mode):
    override()
    resp = _post({"query": "trample", "overrides": {"gemini_api_key": "x", "hybrid_top_k": 1}})
    assert resp.status_code == 422
    assert "gemini_api_key" in resp.json()["detail"]


def test_bad_override_type_is_422(eval_mode):
    override()
    resp = _post({"query": "trample", "overrides": {"hybrid_top_k": "lots"}})
    assert resp.status_code == 422
    assert "hybrid_top_k" in resp.json()["detail"]


def test_override_applies_to_its_request_only(eval_mode, monkeypatch):
    monkeypatch.setattr(main.settings, "rules_top_k", 0)
    default_top_k = main.settings.hybrid_top_k
    override(dense_points=_two_rule_hits())

    overridden = _post({"query": "trample", "overrides": {"hybrid_top_k": 1}})
    following = _post({"query": "trample"})

    assert len(overridden.json()["results"]) == 1
    assert len(following.json()["results"]) == 2
    assert main.settings.hybrid_top_k == default_top_k


def test_generation_override_builds_a_per_request_answerer(eval_mode, monkeypatch):
    seen = []

    def fake_build_answerer(s):
        seen.append(s)
        return FakeAnswerer("Per-request answer.")

    monkeypatch.setattr(query_pipeline, "build_answerer", fake_build_answerer)
    override(answerer=FakeAnswerer("Shared answer."))

    resp = _post({"query": "q", "overrides": {"generation_temperature": 0.7}})

    assert resp.json()["answer"] == "Per-request answer."
    assert seen[0].generation_temperature == 0.7
    assert main.settings.generation_temperature == 0.0


def test_retrieval_override_keeps_the_shared_answerer(eval_mode, monkeypatch):
    built = []
    monkeypatch.setattr(query_pipeline, "build_answerer", lambda s: built.append(s))
    override(answerer=FakeAnswerer("Shared answer."))
    resp = _post({"query": "q", "overrides": {"hybrid_top_k": 3}})
    assert resp.json()["answer"] == "Shared answer."
    assert built == []


def test_eval_source_is_not_saved_to_history(eval_mode):
    engine = memory_engine()
    override(engine=engine)
    _post({"query": "an eval question", "source": "eval"})
    _post({"query": "a user question"})
    assert [row["query"] for row in list_history(engine)] == ["a user question"]


def test_eval_fields_are_null_outside_eval_mode():
    override()
    body = _post({"query": "q"}).json()
    assert body["context_hash"] is None
    assert body["prompt_version"] is None
    assert body["generator"] is None


def test_eval_fields_present_in_eval_mode(eval_mode):
    override()
    body = _post({"query": "q", "generate": False}).json()
    assert body["prompt_version"] == PROMPT_VERSION
    assert body["generator"] == f"gemini:{main.settings.gemini_model}"


def test_generator_reflects_model_override(eval_mode, monkeypatch):
    monkeypatch.setattr(query_pipeline, "build_answerer", lambda s: FakeAnswerer())
    override()
    body = _post({"query": "q", "overrides": {"gemini_model": "gemini-x"}}).json()
    assert body["generator"] == "gemini:gemini-x"


def test_build_answerer_requires_a_key():
    s = main.settings.model_copy(update={"gemini_api_key": SecretStr("")})
    with pytest.raises(RuntimeError, match="MTG_API_GEMINI_API_KEY"):
        main.build_answerer(s)


def test_build_answerer_builds_gemini():
    s = main.settings.model_copy(update={"gemini_api_key": SecretStr("k")})
    assert isinstance(main.build_answerer(s), GeminiAnswerer)


def test_history_records_the_answering_model(monkeypatch):
    monkeypatch.setattr(main.settings, "gemini_model", "gemini-x")
    engine = memory_engine()
    override(engine=engine)
    _post({"query": "q"})
    assert list_history(engine)[0]["model"] == "gemini-x"


def test_context_hash_is_stable_and_hashes_the_llm_context(eval_mode):
    override(dense_points=_two_rule_hits())
    first = _post({"query": "trample", "generate": False}).json()
    second = _post({"query": "trample"}).json()

    assert first["context_hash"] == second["context_hash"]
    results = [QueryResult(**r) for r in first["results"]]
    context, _ = build_context(results)
    assert first["context_hash"] == hashlib.sha256(context.encode("utf-8")).hexdigest()


def test_results_carry_source_type():
    override(dense_points=_two_rule_hits())
    results = _post({"query": "trample"}).json()["results"]
    assert {r["source_type"] for r in results} == {"rule"}


class _CountResult:
    def __init__(self, count):
        self.count = count


class _CountingClient:
    def __init__(self, count=1234, raises=None):
        self._count = count
        self._raises = raises
        self.counted = []

    def count(self, collection_name, exact):
        if self._raises:
            raise self._raises
        self.counted.append(collection_name)
        return _CountResult(self._count)


def _parsed_dir(tmp_path):
    for name in [
        "rules_2026-01-01.jsonl",
        "rules_2026-08-25.jsonl",
        "cards_2026-09-26.jsonl",
        "rulings_2026-08-25.jsonl",
    ]:
        (tmp_path / name).write_text("")
    return tmp_path


def test_config_is_404_outside_eval_mode():
    app.dependency_overrides[main.get_qdrant_client] = lambda: _CountingClient()
    assert TestClient(app).get("/api/v1/config").status_code == 404


def test_config_reports_settings_collection_and_data_files(eval_mode, monkeypatch, tmp_path):
    monkeypatch.setattr(main.settings, "parsed_dir", _parsed_dir(tmp_path))
    client = _CountingClient(count=4321)
    app.dependency_overrides[main.get_qdrant_client] = lambda: client

    body = TestClient(app).get("/api/v1/config").json()

    assert body["settings"]["hybrid_top_k"] == main.settings.hybrid_top_k
    assert body["settings"]["gemini_model"] == main.settings.gemini_model
    assert body["generator"] == f"gemini:{main.settings.gemini_model}"
    assert body["prompt_version"] == PROMPT_VERSION
    assert body["collection"] == {"name": main.settings.collection_name, "points_count": 4321}
    assert body["data_files"] == {
        "rules": "rules_2026-08-25.jsonl",
        "cards": "cards_2026-09-26.jsonl",
        "rulings": "rulings_2026-08-25.jsonl",
    }


def test_config_reports_qdrant_failure_instead_of_crashing(eval_mode, monkeypatch, tmp_path):
    monkeypatch.setattr(main.settings, "parsed_dir", _parsed_dir(tmp_path))
    client = _CountingClient(raises=RuntimeError("collection missing"))
    app.dependency_overrides[main.get_qdrant_client] = lambda: client

    body = TestClient(app).get("/api/v1/config").json()

    assert body["collection"]["points_count"] is None
    assert "collection missing" in body["collection"]["error"]


def test_config_never_includes_secrets(eval_mode, monkeypatch, tmp_path):
    monkeypatch.setattr(main.settings, "parsed_dir", _parsed_dir(tmp_path))
    monkeypatch.setattr(main.settings, "gemini_api_key", SecretStr("AIza-very-secret"))
    monkeypatch.setattr(
        main.settings, "postgres_dsn", "postgresql+psycopg://mtg:hunter2@postgres:5432/mtg"
    )
    app.dependency_overrides[main.get_qdrant_client] = lambda: _CountingClient()

    text = TestClient(app).get("/api/v1/config").text

    for secret in ["AIza-very-secret", "hunter2", "gemini_api_key", "postgres_dsn", "broker_url"]:
        assert secret not in text


def test_eval_fields_include_token_usage(eval_mode):
    override()
    body = _post({"query": "q"}).json()
    assert body["usage"] == {"input_tokens": 1000, "output_tokens": 100, "thinking_tokens": 200}


def test_usage_is_null_outside_eval_mode():
    override()
    assert _post({"query": "q"}).json()["usage"] is None


def test_override_accepts_a_valid_thinking_level(eval_mode, monkeypatch):
    monkeypatch.setattr(query_pipeline, "build_answerer", lambda s: FakeAnswerer("An answer."))
    override()
    resp = _post({"query": "trample", "overrides": {"generation_thinking_level": "low"}})
    assert resp.status_code == 200


def test_override_rejects_an_invalid_thinking_level(eval_mode):
    override()
    resp = _post({"query": "trample", "overrides": {"generation_thinking_level": "loww"}})
    assert resp.status_code == 422
    assert "generation_thinking_level" in resp.json()["detail"]


def test_build_answerer_passes_the_thinking_level():
    s = main.settings.model_copy(
        update={"gemini_api_key": SecretStr("k"), "generation_thinking_level": "low"}
    )
    assert main.build_answerer(s)._thinking_level == "low"


def test_build_answerer_builds_ollama_without_a_gemini_key():
    s = main.settings.model_copy(
        update={
            "answer_provider": "ollama",
            "gemini_api_key": SecretStr(""),
            "generation_temperature": 0.0,
            "generation_max_tokens": None,
        }
    )
    answerer = main.build_answerer(s)
    assert isinstance(answerer, OllamaAnswerer)
    assert answerer.model == "phi4:latest"
    assert answerer._body("q", "c")["options"] == {
        "num_ctx": 6144,
        "seed": 0,
        "num_predict": 1024,
        "temperature": 0.0,
    }


def test_build_answerer_passes_max_tokens_to_ollama():
    s = main.settings.model_copy(update={"answer_provider": "ollama", "generation_max_tokens": 300})
    assert main.build_answerer(s)._body("q", "c")["options"]["num_predict"] == 300


def test_history_records_the_ollama_model(monkeypatch):
    monkeypatch.setattr(main.settings, "answer_provider", "ollama")
    engine = memory_engine()
    override(engine=engine)
    _post({"query": "q"})
    assert list_history(engine)[0]["model"] == "phi4:latest"


def test_generation_error_is_reported_in_eval_mode(eval_mode):
    message = "context overflow: prompt of 9 tokens exceeds num_ctx 8"
    override(answerer=FakeAnswerer(raises=RuntimeError(message)))
    body = _post({"query": "q"}).json()
    assert body["answer"] is None
    assert body["generation_error"] == message


def test_generation_error_is_null_for_a_good_answer(eval_mode):
    override()
    assert _post({"query": "q"}).json()["generation_error"] is None


def test_generation_error_is_null_outside_eval_mode():
    override(answerer=FakeAnswerer(raises=RuntimeError("boom")))
    assert _post({"query": "q"}).json()["generation_error"] is None


def test_config_exposes_the_ollama_settings_but_not_the_url(eval_mode, monkeypatch, tmp_path):
    monkeypatch.setattr(main.settings, "parsed_dir", _parsed_dir(tmp_path))
    app.dependency_overrides[main.get_qdrant_client] = lambda: _CountingClient()
    settings_shown = TestClient(app).get("/api/v1/config").json()["settings"]
    assert settings_shown["answer_provider"] == "gemini"
    assert settings_shown["ollama_model"] == "phi4:latest"
    assert settings_shown["ollama_num_ctx"] == 6144
    assert settings_shown["ollama_seed"] == 0
    assert settings_shown["ollama_num_predict_default"] == 1024
    assert settings_shown["ollama_timeout_seconds"] == 180.0
    assert settings_shown["ollama_stream_chunk_timeout_seconds"] == 120.0
    assert "ollama_url" not in settings_shown
