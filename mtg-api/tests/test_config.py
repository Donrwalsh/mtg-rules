from mtg_api.config import Settings


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


def test_groq_defaults():
    s = Settings(_env_file=None)
    assert s.groq_api_key == ""
    assert s.groq_model == "openai/gpt-oss-120b"


def test_groq_env_override(monkeypatch):
    monkeypatch.setenv("MTG_API_GROQ_API_KEY", "test-key")
    monkeypatch.setenv("MTG_API_GROQ_MODEL", "llama-3.1-8b-instant")
    s = Settings(_env_file=None)
    assert s.groq_api_key == "test-key"
    assert s.groq_model == "llama-3.1-8b-instant"


def test_postgres_dsn_default():
    s = Settings(_env_file=None)
    assert s.postgres_dsn == "postgresql+psycopg://mtg:mtg@postgres:5432/mtg"


def test_postgres_dsn_env_override(monkeypatch):
    monkeypatch.setenv("MTG_API_POSTGRES_DSN", "postgresql://x:y@localhost:5432/z")
    s = Settings(_env_file=None)
    assert s.postgres_dsn == "postgresql://x:y@localhost:5432/z"
