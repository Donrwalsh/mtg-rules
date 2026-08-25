from __future__ import annotations

from mtg_worker.celery_app import celery_app


@celery_app.task(name="mtg_worker.ingest")
def ingest_task() -> None:
    from mtg_ingestion.cli import run_all

    run_all()


@celery_app.task(name="mtg_worker.embed")
def embed_task(limit: int | None = None) -> None:
    from mtg_embed.cli import run as embed_run

    embed_run(source="all", limit=limit)
