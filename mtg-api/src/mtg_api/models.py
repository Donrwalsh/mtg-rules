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
    # True when Gemini finished the answer, False when it stopped early
    # (token limit, safety, timeout, dropped stream); None without an answer.
    answer_complete: bool | None = None
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
    # Eval mode only: why the answer failed or stopped early (None when it didn't).
    generation_error: str | None = None


class StreamHead(BaseModel):
    """The answer stream's first event, `results`: what is known before any
    answer is written. Field order is the order on the wire."""

    results: list[QueryResult]
    degraded: str | None = None
    answers_remaining: int | None = None
    cached_at: datetime | None = None
    # The numbered sources the answer being written may cite (empty when
    # nothing is being written).
    sources: list[Citation] = Field(default_factory=list)

    @classmethod
    def of(cls, response: QueryResponse, sources: list[Citation]) -> StreamHead:
        return cls(
            results=response.results,
            degraded=response.degraded,
            answers_remaining=response.answers_remaining,
            cached_at=response.cached_at,
            sources=sources,
        )


class StreamDone(BaseModel):
    """The answer stream's last event, `done`: the authoritative answer.
    Together with StreamHead it carries every QueryResponse field but
    `query`; a test pins that partition."""

    results: list[QueryResult]
    answer: str | None = None
    citations: list[Citation] = Field(default_factory=list)
    rule_references: list[str] = Field(default_factory=list)
    citation_stats: CitationStats = Field(default_factory=CitationStats)
    answer_complete: bool | None = None
    context_hash: str | None = None
    prompt_version: int | None = None
    generator: str | None = None
    usage: dict[str, int] | None = None
    generation_error: str | None = None

    @classmethod
    def of(cls, response: QueryResponse) -> StreamDone:
        return cls(**{name: getattr(response, name) for name in cls.model_fields})

    def response(self, query: str, head: StreamHead) -> QueryResponse:
        """The blocking endpoint's body: the head's per-request fields, then
        everything in done (whose results carry the `cited` flags)."""
        fields = {
            name: getattr(head, name)
            for name in StreamHead.model_fields
            if name not in ("results", "sources")
        }
        fields |= {name: getattr(self, name) for name in StreamDone.model_fields}
        return QueryResponse(query=query, **fields)


class ReplayResponse(QueryResponse):
    """A saved history row, shaped like the answer it produced."""

    id: int
    created_at: datetime


class EmbedRequest(BaseModel):
    limit: str = "all"
