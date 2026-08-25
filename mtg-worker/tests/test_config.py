from mtg_worker.config import Settings


def test_defaults():
    s = Settings(_env_file=None)
    assert s.broker_url == "redis://redis:6379/0"
    assert s.result_backend == "redis://redis:6379/0"


def test_env_override(monkeypatch):
    monkeypatch.setenv("MTG_WORKER_BROKER_URL", "redis://localhost:6379/0")
    s = Settings(_env_file=None)
    assert s.broker_url == "redis://localhost:6379/0"
