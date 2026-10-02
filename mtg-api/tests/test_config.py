import pytest
from pydantic import ValidationError

from mtg_api.config import OVERRIDABLE_SETTINGS, Settings, answer_model, generator_label


def test_defaults():
    s = Settings(_env_file=None)
    assert s.qdrant_host == "qdrant"
    assert s.qdrant_port == 6333
    assert s.cors_origins == ["http://localhost:3000"]


def test_env_override(monkeypatch):
    monkeypatch.setenv("MTG_API_QDRANT_HOST", "localhost")
    s = Settings(_env_file=None)
    assert s.qdrant_host == "localhost"


def test_broker_defaults():
    s = Settings(_env_file=None)
    assert s.broker_url == "redis://redis:6379/0"
    assert s.result_backend == "redis://redis:6379/0"


def test_broker_env_override(monkeypatch):
    monkeypatch.setenv("MTG_API_BROKER_URL", "redis://localhost:6379/0")
    s = Settings(_env_file=None)
    assert s.broker_url == "redis://localhost:6379/0"


def test_hybrid_defaults():
    s = Settings(_env_file=None)
    assert s.collection_name == "mtg_rules"
    assert s.dense_model_name == "BAAI/bge-base-en-v1.5"
    assert s.sparse_model_name == "Qdrant/bm25"
    assert s.hybrid_dense_weight == 0.5
    assert s.hybrid_sparse_weight == 0.5
    assert s.hybrid_top_k == 10
    assert s.hybrid_per_branch_limit == 50
    assert s.hybrid_score_threshold == 0.0


def test_hybrid_env_override(monkeypatch):
    monkeypatch.setenv("MTG_API_HYBRID_DENSE_WEIGHT", "0.7")
    s = Settings(_env_file=None)
    assert s.hybrid_dense_weight == 0.7


def test_card_ruling_limit_default():
    s = Settings(_env_file=None)
    assert s.card_ruling_limit == 20


def test_card_ruling_limit_env_override(monkeypatch):
    monkeypatch.setenv("MTG_API_CARD_RULING_LIMIT", "5")
    s = Settings(_env_file=None)
    assert s.card_ruling_limit == 5


def test_gemini_defaults():
    s = Settings(_env_file=None)
    assert s.gemini_api_key.get_secret_value() == ""
    assert s.gemini_model == "gemini-3.5-flash"
    assert s.gemini_url == "https://generativelanguage.googleapis.com"
    assert generator_label(s) == "gemini:gemini-3.5-flash"


def test_gemini_env_override(monkeypatch):
    monkeypatch.setenv("MTG_API_GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("MTG_API_GEMINI_MODEL", "gemini-x")
    s = Settings(_env_file=None)
    assert s.gemini_api_key.get_secret_value() == "test-key"
    # The key never shows up in a repr (logs, tracebacks).
    assert "test-key" not in repr(s)
    assert generator_label(s) == "gemini:gemini-x"


def test_embed_threads_defaults_to_onnx_runtime_choice():
    assert Settings(_env_file=None).embed_threads is None


def test_embed_threads_env_override(monkeypatch):
    monkeypatch.setenv("MTG_API_EMBED_THREADS", "1")
    assert Settings(_env_file=None).embed_threads == 1


def test_task_endpoints_on_by_default():
    assert Settings(_env_file=None).task_endpoints is True


def test_task_endpoints_env_override(monkeypatch):
    monkeypatch.setenv("MTG_API_TASK_ENDPOINTS", "false")
    assert Settings(_env_file=None).task_endpoints is False


def test_gating_defaults():
    s = Settings(_env_file=None)
    assert s.gating_enabled is False
    assert s.daily_budget_usd == 1.0
    assert s.gemini_input_price_per_mtok == 0.0
    assert s.gemini_output_price_per_mtok == 0.0
    assert s.ip_daily_llm_limit == 20
    assert s.ip_window_llm_limit == 5
    assert s.ip_window_minutes == 10
    assert s.max_query_chars == 500
    assert s.answer_cache_enabled is True
    assert s.admin_password.get_secret_value() == ""
    assert s.generation_thinking_level is None


def test_thinking_level_is_an_overridable_generation_setting():
    from mtg_api.config import GENERATION_SETTINGS, OVERRIDABLE_SETTINGS

    assert "generation_thinking_level" in OVERRIDABLE_SETTINGS
    assert "generation_thinking_level" in GENERATION_SETTINGS


def test_generator_label_without_thinking_level_is_unchanged():
    s = Settings(_env_file=None, gemini_model="gemini-3.5-flash")
    assert generator_label(s) == "gemini:gemini-3.5-flash"


def test_generator_label_includes_thinking_level():
    s = Settings(_env_file=None, gemini_model="gemini-3.5-flash", generation_thinking_level="low")
    assert generator_label(s) == "gemini:gemini-3.5-flash:think=low"


def test_generation_thinking_level_rejects_an_invalid_value():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, generation_thinking_level="loww")


def test_generation_thinking_level_accepts_each_valid_value():
    for level in ("minimal", "low", "medium", "high"):
        assert (
            Settings(_env_file=None, generation_thinking_level=level).generation_thinking_level
            == level
        )


def test_ollama_defaults():
    s = Settings(_env_file=None)
    assert s.answer_provider == "gemini"
    assert s.ollama_url == "http://host.docker.internal:11434"
    assert s.ollama_model == "phi4:latest"
    assert (s.ollama_num_ctx, s.ollama_seed, s.ollama_num_predict_default) == (6144, 0, 1024)
    assert (s.ollama_timeout_seconds, s.ollama_stream_chunk_timeout_seconds) == (180.0, 120.0)


def test_generator_label_and_answer_model_for_ollama():
    s = Settings(
        _env_file=None,
        answer_provider="ollama",
        ollama_model="phi4:latest",
        generation_thinking_level="low",
    )
    assert generator_label(s) == "ollama:phi4:latest"
    assert answer_model(s) == "phi4:latest"


def test_gemini_label_and_answer_model_are_unchanged():
    s = Settings(_env_file=None, gemini_model="gemini-x", generation_thinking_level="low")
    assert generator_label(s) == "gemini:gemini-x:think=low"
    assert answer_model(s) == "gemini-x"


def test_answer_provider_is_never_overridable():
    assert "answer_provider" not in OVERRIDABLE_SETTINGS
