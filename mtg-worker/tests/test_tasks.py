from mtg_worker.tasks import embed_task, ingest_task


def test_ingest_task_calls_run_all(monkeypatch):
    calls = []
    monkeypatch.setattr("mtg_ingestion.cli.run_all", lambda: calls.append(True))
    ingest_task()
    assert calls == [True]


def test_embed_task_calls_run_with_source_all_and_given_limit(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "mtg_embed.cli.run", lambda source, limit: calls.append((source, limit))
    )
    embed_task(limit=25)
    assert calls == [("all", 25)]


def test_embed_task_defaults_limit_to_none(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "mtg_embed.cli.run", lambda source, limit: calls.append((source, limit))
    )
    embed_task()
    assert calls == [("all", None)]
