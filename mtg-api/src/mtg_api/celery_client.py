from __future__ import annotations

from celery import Celery

from mtg_api.config import settings


def get_celery_client() -> Celery:
    return Celery("mtg_worker", broker=settings.broker_url, backend=settings.result_backend)
