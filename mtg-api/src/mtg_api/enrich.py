from __future__ import annotations

from mtg_api.card_details import card_details
from mtg_api.card_matcher import CardMatcher
from mtg_api.models import Citation, QueryResult
from mtg_api.rules_index import RulesIndex

_CARD_SOURCES = {"card", "oracle", "ruling"}


def _fill(item: QueryResult | Citation, kind: str, cards: CardMatcher, rules: RulesIndex) -> None:
    if kind == "rule":
        item.heading = rules.heading(item.rule_id or item.title)
    elif kind in _CARD_SOURCES:
        # A ruling gets the card it rules on.
        row = cards.by_oracle_id(item.oracle_id)
        item.card = card_details(row) if row else None


def enrich_results(results: list[QueryResult], cards: CardMatcher, rules: RulesIndex) -> None:
    """Attach card faces and rule headings for the UI. Runs after
    build_context, so it can never change what the LLM saw."""
    for result in results:
        _fill(result, result.source, cards, rules)


def enrich_citations(citations: list[Citation], cards: CardMatcher, rules: RulesIndex) -> None:
    for citation in citations:
        _fill(citation, citation.source_type, cards, rules)
