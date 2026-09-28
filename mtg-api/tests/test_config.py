from mtg_api.config import Settings, generator_label


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
