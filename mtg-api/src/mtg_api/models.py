from __future__ import annotations

from pydantic import BaseModel


class QueryRequest(BaseModel):
    query: str


class QueryResult(BaseModel):
    source: str
    title: str
    text: str
    score: float
    match_type: str
    oracle_id: str | None = None


class QueryResponse(BaseModel):
    query: str
    results: list[QueryResult]


class EmbedRequest(BaseModel):
    limit: str = "all"
