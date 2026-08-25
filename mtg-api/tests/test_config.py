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
