from __future__ import annotations

from celery import Celery

from mtg_worker.config import settings

celery_app = Celery("mtg_worker", broker=settings.broker_url, backend=settings.result_backend)
