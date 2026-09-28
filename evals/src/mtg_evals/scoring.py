from __future__ import annotations

import re

from mtg_evals.cases import Case

_CARD_SOURCES = ("card", "oracle")


def _source_type(item: dict) -> str | None:
    # `source_type` is the eval-facing name; `source` is the original field.
    return item.get("source_type") or item.get("source")


def requirement_satisfied(kind: str, value: str, item: dict) -> bool:
    """Does one result (or citation) satisfy one requirement?

    rules:   rule_id equals or starts with the value (plain string prefix, as
             eval.yaml defines it)
    rulings: a ruling for that exact card name
    cards:   the card's own text, from the card matcher or a vector hit
    """
    source_type = _source_type(item)
    if kind == "rules":
        rule_id = item.get("rule_id") or ""
        return source_type == "rule" and rule_id.startswith(value)
    if kind == "rulings":
        return source_type == "ruling" and item.get("card_name") == value
    if kind == "cards":
        return source_type in _CARD_SOURCES and item.get("card_name") == value
    raise ValueError(f"unknown requirement kind {kind!r}")


def requirement_label(kind: str, value: str) -> str:
    return f"{kind}:{value}"


def score_retrieval(case: Case, results: list[dict]) -> dict:
    ranks: dict[str, int | None] = {}
    for kind, value in case.requirements:
        ranks[requirement_label(kind, value)] = next(
            (
                rank
                for rank, item in enumerate(results, start=1)
                if requirement_satisfied(kind, value, item)
            ),
            None,
        )
    satisfied = [rank for rank in ranks.values() if rank is not None]

    matched_cards = [
        r.get("card_name") for r in results if r.get("match_type") == "card_name_match"
    ]
    expected_missing = [c for c in case.expected_cards if c not in matched_cards]
    forbidden_hits = [c for c in case.forbidden_cards if c in matched_cards]
    cards_ok = not expected_missing and not forbidden_hits

    if case.has_required:
        recall = len(satisfied) / len(ranks)
        sources_pass = len(satisfied) == len(ranks)
        passed = sources_pass and cards_ok
    else:
        recall = None
        sources_pass = None
        # Nothing to retrieve: the case only counts when it constrains the
        # card matcher (e.g. a forbidden rules-word card).
        has_card_checks = bool(case.expected_cards or case.forbidden_cards)
        passed = cards_ok if has_card_checks else None

    return {
        "pass": passed,
        "sources_pass": sources_pass,
        "recall": recall,
        "first_hit_rank": min(satisfied) if satisfied else None,
        "requirement_ranks": ranks,
        "matched_cards": matched_cards,
        "expected_missing": expected_missing,
        "forbidden_hits": forbidden_hits,
        "result_count": len(results),
        "context_chars": sum(len(r.get("text") or "") for r in results),
    }


def score_citations(case: Case, citations: list[dict], stats: dict | None) -> dict:
    if case.has_required:
        cited = any(
            requirement_satisfied(kind, value, citation)
            for kind, value in case.requirements
            for citation in citations
        )
    else:
        cited = None
    return {
        "cited_satisfies_required": cited,
        "cited_count": len(citations),
        "invalid_citations": (stats or {}).get("invalid_count", 0),
    }


_MARKDOWN = re.compile(r"[*_`#>]")
_SENTENCE_END = re.compile(r"[.!?\n]")
_ANSWER_PREFIX = re.compile(r"^\s*(answer|short answer)\s*:\s*", re.IGNORECASE)
_YES_NO = re.compile(r"\b(yes|no)\b")


def parse_verdict(answer: str | None) -> str:
    """ "yes" | "no" | "unclear", from the answer's opening sentence only.

    An answer that opens with yes/no wins outright. Otherwise the first
    sentence must contain exactly one kind of standalone yes/no ("The
    answer is no.")."""
    if not answer:
        return "unclear"
    text = _ANSWER_PREFIX.sub("", _MARKDOWN.sub("", answer)).strip().lower()
    first = _SENTENCE_END.split(text, maxsplit=1)[0][:200]
    opening = re.match(r"(yes|no)\b", first)
    if opening:
        return opening.group(1)
    kinds = set(_YES_NO.findall(first))
    return kinds.pop() if len(kinds) == 1 else "unclear"
