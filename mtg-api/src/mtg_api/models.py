from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator

from mtg_api.card_details import CardDetails


class QueryRequest(BaseModel):
    query: str
    # False: retrieval only, the LLM is never called.
    generate: bool = True
    # Per-request setting overrides; only honoured in eval mode.
    overrides: dict[str, Any] = Field(default_factory=dict)
    # Free-form caller label. "eval" requests are not saved to history.
    source: str | None = None
    # Admin only: skip the answer cache and replace its entry.
    fresh: bool = False


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
    # Display data filled in just before responding (see mtg_api.enrich);
    # never part of the LLM context.
    card: CardDetails | None = None
    heading: str | None = None
    # Same value as `source`, under the name the embed payloads use.
    source_type: str | None = None

    @model_validator(mode="after")
    def _mirror_source(self) -> QueryResult:
        self.source_type = self.source
        return self


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
    # Display data filled in just before responding (see mtg_api.enrich).
    card: CardDetails | None = None
    heading: str | None = None


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
    # Set when this answer came from the answer cache: when it was generated.
    cached_at: datetime | None = None
    # Why no answer was generated: "ip_quota" or "global_budget".
    degraded: str | None = None
    # LLM answers this visitor has left today (null when not gated, or admin).
    answers_remaining: int | None = None
    # Eval mode only (None otherwise).
    context_hash: str | None = None
    prompt_version: int | None = None
    generator: str | None = None
    # Eval mode only: this request's Gemini token counts.
    usage: dict[str, int] | None = None


class ReplayResponse(QueryResponse):
    """A saved history row, shaped like the answer it produced."""

    id: int
    created_at: datetime


class EmbedRequest(BaseModel):
    limit: str = "all"
