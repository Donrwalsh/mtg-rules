from __future__ import annotations

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from qdrant_client import QdrantClient

from mtg_api.config import settings
from mtg_api.models import QueryRequest, QueryResponse, QueryResult
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
