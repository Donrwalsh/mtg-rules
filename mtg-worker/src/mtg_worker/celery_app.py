from __future__ import annotations

from celery import Celery

from mtg_worker.config import settings

celery_app = Celery("mtg_worker", broker=settings.broker_url, backend=settings.result_backend)

# Importing tasks here is what registers them against celery_app -- without
# it, a worker started as `celery -A mtg_worker.celery_app worker` never
# imports tasks.py at all, so ingest_task/embed_task never get registered.
from mtg_worker import tasks  # noqa: E402,F401
