from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from mtg_api.llm import source_label
from mtg_api.models import Citation, CitationStats, QueryResult
from mtg_api.rules_index import RulesIndex

logger = logging.getLogger(__name__)

# Any bracket group made only of digits and citation-ish punctuation (with at
# least one digit), plus the horizontal whitespace before it, so a dropped
# marker doesn't leave a stray space. Newlines are never consumed.
_MARKER_RE = re.compile(r"([ \t]*)(\[[\d\s,;^\-–]*\d[\d\s,;^\-–]*\])")
# The subset of markers we accept: [1], [1, 3], [1,3].
_WELL_FORMED_RE = re.compile(r"\[\s*\d+(?:\s*,\s*\d+)*\s*\]")
# \b\d{3}\.\d+[a-z]?\b, hardened against prices ($100.50), dotted dates
# (2018.01.19), versions (100.5.2), and longer numbers (12100.5).
_RULE_REF_RE = re.compile(r"(?<![\d.$€£])\d{3}\.\d+[a-z]?(?![a-z\d]|\.\d)")


@dataclass
class CitationParse:
    answer: str
    cited_numbers: list[int]  # distinct, in first-appearance order
    invalid_count: int


@dataclass
class CitedAnswer:
    answer: str
    citations: list[Citation]
    rule_references: list[str]
    stats: CitationStats


def parse_citations(answer: str, valid_numbers: set[int]) -> CitationParse:
    """Find [n] markers in the answer. Numbers outside valid_numbers and
    malformed markers are removed from the text, logged, and counted."""
    cited: list[int] = []
    invalid = 0

    def replace(match: re.Match) -> str:
        nonlocal invalid
        leading, marker = match.group(1), match.group(2)
        if not _WELL_FORMED_RE.fullmatch(marker):
            logger.warning("Dropping malformed citation marker %s", marker)
            invalid += 1
            return ""
        kept: list[int] = []
        for number in (int(n) for n in re.findall(r"\d+", marker)):
            if number not in valid_numbers:
                logger.warning("Dropping out-of-range citation [%d]", number)
                invalid += 1
                continue
            if number not in kept:
                kept.append(number)
            if number not in cited:
                cited.append(number)
        if not kept:
            return ""
        return f"{leading}[{', '.join(str(n) for n in kept)}]"

    cleaned = _MARKER_RE.sub(replace, answer)
    return CitationParse(answer=cleaned, cited_numbers=cited, invalid_count=invalid)


def find_rule_references(text: str, rules_index: RulesIndex) -> list[str]:
    """Raw rule numbers the answer mentions in prose. Only ones that exist
    in the rules index are returned (distinct, in order); the rest stay as
    plain text and are logged."""
    found: list[str] = []
    for match in _RULE_REF_RE.finditer(text):
        rule_id = match.group(0)
        if rule_id not in rules_index:
            logger.warning("Answer mentions unknown rule %s", rule_id)
        elif rule_id not in found:
            found.append(rule_id)
    return found


def citation_for(number: int, result: QueryResult) -> Citation:
    title = source_label(result)
    if result.source == "rule":
        rule_id = result.rule_id or result.title
        return Citation(
            number=number,
            source_type="rule",
            title=title,
            rule_id=rule_id,
            text=result.text,
            url=f"/rules/{rule_id}",
        )
    # Vector hits on card text arrive as "oracle"; exact matches as "card".
    source_type = "card" if result.source == "oracle" else result.source
    return Citation(
        number=number,
        source_type=source_type,
        title=title,
        card_name=result.card_name or result.title,
        oracle_id=result.oracle_id,
        text=result.text,
        url=result.scryfall_uri,
        published_at=result.published_at if source_type == "ruling" else None,
    )


def build_citations(cited_numbers: list[int], sources: dict[int, QueryResult]) -> list[Citation]:
    return [citation_for(number, sources[number]) for number in sorted(cited_numbers)]


def cite_answer(
    answer: str, sources: dict[int, QueryResult], rules_index: RulesIndex
) -> CitedAnswer:
    """Validate an answer's citations against the numbered sources it was
    generated from. Marks each cited source's QueryResult as cited."""
    parsed = parse_citations(answer, set(sources))
    for number in parsed.cited_numbers:
        sources[number].cited = True
    return CitedAnswer(
        answer=parsed.answer,
        citations=build_citations(parsed.cited_numbers, sources),
        rule_references=find_rule_references(parsed.answer, rules_index),
        stats=CitationStats(
            cited_count=len(parsed.cited_numbers),
            invalid_count=parsed.invalid_count,
            uncited_answer=not parsed.cited_numbers,
        ),
    )
