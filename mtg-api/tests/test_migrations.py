from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine, inspect

from alembic import command
from mtg_api.config import settings

API_ROOT = Path(__file__).resolve().parents[1]
CITATION_COLUMNS = {"citations", "citation_stats", "rule_references"}


def _config() -> Config:
    # No ini file on purpose: env.py only calls logging's fileConfig() when
    # one is given, and that would disable loggers other tests assert on.
    cfg = Config()
    cfg.set_main_option("script_location", str(API_ROOT / "alembic"))
    return cfg


def _columns(dsn: str) -> set[str]:
    engine = create_engine(dsn)
    try:
        return {c["name"] for c in inspect(engine).get_columns("query_history")}
    finally:
        engine.dispose()


def test_0002_adds_and_removes_citation_columns(tmp_path, monkeypatch):
    dsn = f"sqlite:///{(tmp_path / 'history.db').as_posix()}"
    monkeypatch.setattr(settings, "postgres_dsn", dsn)
    cfg = _config()

    command.upgrade(cfg, "0001")
    assert not CITATION_COLUMNS & _columns(dsn)

    command.upgrade(cfg, "head")
    assert CITATION_COLUMNS <= _columns(dsn)

    command.downgrade(cfg, "0001")
    assert not CITATION_COLUMNS & _columns(dsn)
    assert {"id", "query", "answer", "results"} <= _columns(dsn)
