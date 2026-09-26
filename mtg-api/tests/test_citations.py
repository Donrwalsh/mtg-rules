import logging

from mtg_api.citations import build_citations, cite_answer, find_rule_references, parse_citations
from mtg_api.models import QueryResult
from mtg_api.rules_index import RulesIndex

VALID = {1, 2, 3}


def test_single_marker():
    parsed = parse_citations("Bolt deals 3 damage [1].", VALID)
    assert parsed.answer == "Bolt deals 3 damage [1]."
    assert parsed.cited_numbers == [1]
    assert parsed.invalid_count == 0


def test_adjacent_markers():
    parsed = parse_citations("It can't be targeted [1][3].", VALID)
    assert parsed.cited_numbers == [1, 3]
    assert parsed.answer == "It can't be targeted [1][3]."


def test_comma_separated_marker_is_kept_and_normalized():
    parsed = parse_citations("It can't be targeted [3,1].", VALID)
    assert parsed.cited_numbers == [3, 1]
    assert parsed.answer == "It can't be targeted [3, 1]."


def test_repeated_citation_counted_once():
    parsed = parse_citations("First [2]. Second [2]. Third [2, 2].", VALID)
    assert parsed.cited_numbers == [2]
    assert parsed.answer == "First [2]. Second [2]. Third [2]."


def test_out_of_range_numbers_removed_logged_and_counted(caplog):
    with caplog.at_level(logging.WARNING, logger="mtg_api.citations"):
        parsed = parse_citations("a [1][9]. b [0]. c [2, 7].", VALID)
    assert parsed.answer == "a [1]. b. c [2]."
    assert parsed.cited_numbers == [1, 2]
    assert parsed.invalid_count == 3
    assert "[9]" in caplog.text and "[0]" in caplog.text and "[7]" in caplog.text


def test_malformed_markers_removed_and_counted(caplog):
    with caplog.at_level(logging.WARNING, logger="mtg_api.citations"):
        parsed = parse_citations("a [1-3]. b [^2]. c [1;2]. d [1 2].", VALID)
    assert parsed.answer == "a. b. c. d."
    assert parsed.cited_numbers == []
    assert parsed.invalid_count == 4
    assert "[1-3]" in caplog.text


def test_no_citations():
    parsed = parse_citations("The context doesn't cover that.", VALID)
    assert parsed.answer == "The context doesn't cover that."
    assert parsed.cited_numbers == []
    assert parsed.invalid_count == 0


def test_non_citation_brackets_are_left_alone():
    text = "See [Rule 702.11b], pay {2}{R}, tick [ ] and [a]."
    parsed = parse_citations(text, VALID)
    assert parsed.answer == text
    assert parsed.invalid_count == 0


def test_removing_a_marker_keeps_line_breaks():
    assert parse_citations("First line.\n[9] Second line.", VALID).answer == (
        "First line.\n Second line."
    )


INDEX = RulesIndex(
    [
        {"rule_id": "702.11b", "text": "t", "parent_id": "702.11"},
        {"rule_id": "704.5b", "text": "t", "parent_id": "704.5"},
        {"rule_id": "100.5", "text": "t", "parent_id": "100"},
    ]
)


def test_rule_references_valid_distinct_in_order():
    text = "Per rule 704.5b and 702.11b, and again 704.5b."
    assert find_rule_references(text, INDEX) == ["704.5b", "702.11b"]


def test_rule_references_invalid_are_logged_not_returned(caplog):
    with caplog.at_level(logging.WARNING, logger="mtg_api.citations"):
        assert find_rule_references("See rule 999.9z.", INDEX) == []
    assert "999.9z" in caplog.text


def test_rule_references_ignore_prices_dates_and_versions():
    text = "It costs $100.50, printed 2018.01.19, v100.5.2, 12100.5, and 1.5 turns."
    assert find_rule_references(text, INDEX) == []


def test_rule_reference_at_sentence_end_is_found():
    assert find_rule_references("That's rule 100.5.", INDEX) == ["100.5"]


def _result(source, title="T", **fields):
    return QueryResult(
        source=source, title=title, text="body", score=1.0, match_type="vector_hit", **fields
    )


SHOCK_URI = "https://scryfall.com/card/m19/156/shock"


def test_build_citations_fields_per_source_type():
    sources = {
        1: _result("rule", "702.11b", rule_id="702.11b"),
        2: _result("oracle", "Shock", card_name="Shock", oracle_id="o1", scryfall_uri=SHOCK_URI),
        3: _result(
            "ruling",
            "Shock",
            card_name="Shock",
            oracle_id="o1",
            published_at="2020-01-01",
            scryfall_uri=SHOCK_URI,
        ),
        4: _result("card", "Bolt", card_name="Bolt"),
    }
    citations = build_citations([3, 1, 2, 4], sources)
    assert [c.number for c in citations] == [1, 2, 3, 4]
    rule, card, ruling, no_uri = citations

    assert (rule.source_type, rule.title, rule.rule_id, rule.url) == (
        "rule",
        "Rule 702.11b",
        "702.11b",
        "/rules/702.11b",
    )
    assert rule.card_name is None and rule.published_at is None

    assert (card.source_type, card.title, card.card_name, card.url) == (
        "card",
        "Card — Shock",
        "Shock",
        SHOCK_URI,
    )
    assert card.rule_id is None and card.published_at is None and card.oracle_id == "o1"

    assert (ruling.source_type, ruling.title, ruling.published_at, ruling.url) == (
        "ruling",
        "Ruling — Shock (2020-01-01)",
        "2020-01-01",
        SHOCK_URI,
    )
    assert ruling.text == "body"

    assert no_uri.url is None


def test_cite_answer_cleans_flags_and_counts():
    sources = {
        1: _result("rule", "702.11b", rule_id="702.11b"),
        2: _result("card", "Bolt", card_name="Bolt"),
    }
    cited = cite_answer("Yes [1][5], see 702.11b and 999.9z.", sources, INDEX)
    assert cited.answer == "Yes [1], see 702.11b and 999.9z."
    assert [c.number for c in cited.citations] == [1]
    assert sources[1].cited is True
    assert sources[2].cited is False
    assert cited.rule_references == ["702.11b"]
    assert cited.stats.model_dump() == {
        "cited_count": 1,
        "invalid_count": 1,
        "uncited_answer": False,
    }


def test_cite_answer_with_no_citations_is_flagged_uncited():
    cited = cite_answer("Not covered by the context.", {1: _result("rule", "1.1")}, INDEX)
    assert cited.citations == []
    assert cited.stats.uncited_answer is True
    assert cited.stats.cited_count == 0
