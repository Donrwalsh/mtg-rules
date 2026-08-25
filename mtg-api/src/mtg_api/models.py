from __future__ import annotations

from pydantic import BaseModel


class QueryRequest(BaseModel):
    query: str


class QueryResult(BaseModel):
    source: str
    title: str
    text: str
    score: float


class QueryResponse(BaseModel):
    query: str
    results: list[QueryResult]
