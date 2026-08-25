from __future__ import annotations

from fastapi import Depends, FastAPI
from qdrant_client import QdrantClient

from mtg_api.config import settings
from mtg_api.qdrant_check import check_qdrant

app = FastAPI(title="mtg-api")


def get_qdrant_client() -> QdrantClient:
    return QdrantClient(host=settings.qdrant_host, port=settings.qdrant_port)


@app.get("/health")
def health(client: QdrantClient = Depends(get_qdrant_client)) -> dict:
    qdrant_status = "ok" if check_qdrant(client) else "unreachable"
    return {"status": "ok", "qdrant": qdrant_status}
