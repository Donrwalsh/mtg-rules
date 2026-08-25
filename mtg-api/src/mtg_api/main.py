from __future__ import annotations

from celery import Celery
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from qdrant_client import QdrantClient

from mtg_api.celery_client import get_celery_client
from mtg_api.config import settings
from mtg_api.models import EmbedRequest, QueryRequest, QueryResponse, QueryResult
from mtg_api.qdrant_check import check_qdrant

app = FastAPI(title="mtg-api")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


def get_qdrant_client() -> QdrantClient:
    return QdrantClient(host=settings.qdrant_host, port=settings.qdrant_port)


@app.get("/health")
def health(client: QdrantClient = Depends(get_qdrant_client)) -> dict:
    qdrant_status = "ok" if check_qdrant(client) else "unreachable"
    return {"status": "ok", "qdrant": qdrant_status}


_DUMMY_RESULTS = [
    QueryResult(
        source="rule",
        title="702.19. Trample",
        text="702.19a Trample is a static ability...",
        score=0.91,
    ),
    QueryResult(
        source="ruling",
        title="Craterhoof Behemoth",
        text="If the creature with trample is blocked, you may assign...",
        score=0.87,
    ),
]


@app.post("/api/v1/query", response_model=QueryResponse)
def query(request: QueryRequest) -> QueryResponse:
    return QueryResponse(query=request.query, results=_DUMMY_RESULTS)


@app.post("/api/v1/ingest")
def trigger_ingest(client: Celery = Depends(get_celery_client)) -> dict:
    result = client.send_task("mtg_worker.ingest")
    return {"task_id": result.id}


@app.post("/api/v1/embed")
def trigger_embed(request: EmbedRequest, client: Celery = Depends(get_celery_client)) -> dict:
    if request.limit == "all":
        limit = None
    else:
        try:
            limit = int(request.limit)
        except ValueError:
            raise HTTPException(status_code=400, detail='limit must be "all" or a positive integer')
        if limit <= 0:
            raise HTTPException(status_code=400, detail='limit must be "all" or a positive integer')
    result = client.send_task("mtg_worker.embed", kwargs={"limit": limit})
    return {"task_id": result.id}
