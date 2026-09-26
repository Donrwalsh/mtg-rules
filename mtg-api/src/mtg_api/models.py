from __future__ import annotations

from pydantic import BaseModel, Field


class QueryRequest(BaseModel):
    query: str


class QueryResult(BaseModel):
    source: str
    title: str
    text: str
    score: float
    match_type: str
    oracle_id: str | None = None
    rule_id: str | None = None
    card_name: str | None = None
    published_at: str | None = None
    scryfall_uri: str | None = None
    # Set when the generated answer cites this result's context block.
    cited: bool = False


class Citation(BaseModel):
    number: int
    source_type: str  # "rule" | "card" | "ruling"
    title: str
    rule_id: str | None = None
    card_name: str | None = None
    oracle_id: str | None = None
    text: str
    url: str | None = None
    published_at: str | None = None


class CitationStats(BaseModel):
    cited_count: int = 0
    invalid_count: int = 0
    uncited_answer: bool = False


class QueryResponse(BaseModel):
    query: str
    results: list[QueryResult]
    answer: str | None = None
    # Only the sources the answer actually cites, ordered by number.
    citations: list[Citation] = Field(default_factory=list)
    # Rule numbers mentioned in the answer's prose that exist in the rules.
    rule_references: list[str] = Field(default_factory=list)
    citation_stats: CitationStats = Field(default_factory=CitationStats)


class EmbedRequest(BaseModel):
    limit: str = "all"
