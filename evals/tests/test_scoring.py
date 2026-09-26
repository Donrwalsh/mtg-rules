import pytest

from mtg_evals.cases import Case
from mtg_evals.scoring import (
    parse_verdict,
    requirement_satisfied,
    score_citations,
    score_retrieval,
)


def _case(**fields):
    return Case(id="c", question="q", tags=("keyword",), split="dev", **fields)


def rule(rule_id, match_type="vector_hit", text="t"):
    return {"source_type": "rule", "rule_id": rule_id, "match_type": match_type, "text": text}


def ruling(card):
    return {"source_type": "ruling", "card_name": card, "match_type": "card_ruling_match"}


def card(name, match_type="card_name_match", source_type="card"):
    return {"source_type": source_type, "card_name": name, "match_type": match_type}


def test_rule_requirement_is_prefix_matched():
    assert requirement_satisfied("rules", "702.19", rule("702.19b"))
    assert requirement_satisfied("rules", "702.19b", rule("702.19b"))
    assert not requirement_satisfied("rules", "702.19b", rule("702.19"))
    assert not requirement_satisfied("rules", "702.19", rule("702.2c"))
    # Plain string prefix, as eval.yaml specifies: "702.1" also covers 702.19b.
    assert requirement_satisfied("rules", "702.1", rule("702.19b"))


def test_rule_requirement_needs_a_rule_source():
    assert not requirement_satisfied("rules", "702", {"source_type": "ruling", "rule_id": "702"})


def test_rulings_requirement_needs_a_ruling_for_that_card():
    assert requirement_satisfied("rulings", "Blood Moon", ruling("Blood Moon"))
    assert not requirement_satisfied("rulings", "Blood Moon", card("Blood Moon"))
    assert not requirement_satisfied("rulings", "Blood Moon", ruling("Blood Artist"))


def test_cards_requirement_accepts_card_matches_and_oracle_vector_hits():
    assert requirement_satisfied("cards", "Blood Moon", card("Blood Moon"))
    oracle_hit = card("Blood Moon", match_type="vector_hit", source_type="oracle")
    assert requirement_satisfied("cards", "Blood Moon", oracle_hit)
    assert not requirement_satisfied("cards", "Blood Moon", ruling("Blood Moon"))


def test_falls_back_to_the_original_source_field():
    assert requirement_satisfied("rules", "702", {"source": "rule", "rule_id": "702.2"})


def test_recall_and_first_hit_rank():
    case = _case(rules=("702.2c", "702.19b", "510.1c"))
    results = [card("Colossal Dreadmaw"), rule("702.19b"), rule("702.19c"), rule("702.2c")]
    score = score_retrieval(case, results)
    assert score["recall"] == pytest.approx(2 / 3)
    assert score["first_hit_rank"] == 2
    assert score["requirement_ranks"] == {"rules:702.2c": 4, "rules:702.19b": 2, "rules:510.1c": None}
    assert score["sources_pass"] is False
    assert score["pass"] is False


def test_all_requirements_satisfied_passes():
    case = _case(rules=("702.11",), rulings=("Lightning Bolt",))
    score = score_retrieval(case, [ruling("Lightning Bolt"), rule("702.11b")])
    assert score["pass"] is True
    assert score["recall"] == 1.0
    assert score["first_hit_rank"] == 1


def test_no_hits_gives_null_rank():
    score = score_retrieval(_case(rules=("702.11",)), [rule("100.1")])
    assert score["first_hit_rank"] is None
    assert score["recall"] == 0.0


def test_forbidden_card_from_matcher_fails_the_case():
    case = _case(rules=("702.11b",), forbidden_cards=("Lightning",), expected_cards=("Lightning Bolt",))
    results = [card("Lightning Bolt"), card("Lightning"), rule("702.11b")]
    score = score_retrieval(case, results)
    assert score["forbidden_hits"] == ["Lightning"]
    assert score["sources_pass"] is True
    assert score["pass"] is False


def test_forbidden_card_only_counts_matcher_hits():
    case = _case(rules=("702.11b",), forbidden_cards=("Lightning",))
    oracle_hit = card("Lightning", match_type="vector_hit", source_type="oracle")
    score = score_retrieval(case, [rule("702.11b"), oracle_hit])
    assert score["forbidden_hits"] == []
    assert score["pass"] is True


def test_missing_expected_card_fails_the_case():
    case = _case(rules=("702.11b",), expected_cards=("Lightning Bolt",))
    score = score_retrieval(case, [rule("702.11b")])
    assert score["expected_missing"] == ["Lightning Bolt"]
    assert score["pass"] is False


def test_case_without_requirements_is_unscored_unless_it_checks_cards():
    assert score_retrieval(_case(should_decline=True), [rule("100.1")])["pass"] is None
    decline_with_card = _case(should_decline=True, expected_cards=("Black Lotus",))
    assert score_retrieval(decline_with_card, [card("Black Lotus")])["pass"] is True
    assert score_retrieval(decline_with_card, [])["pass"] is False
    assert score_retrieval(decline_with_card, [])["recall"] is None


def test_context_size():
    score = score_retrieval(_case(rules=("1",)), [rule("1", text="abc"), rule("2", text="de")])
    assert score["result_count"] == 2
    assert score["context_chars"] == 5


def test_citations_satisfying_a_requirement():
    case = _case(rules=("702.19",))
    citations = [{"source_type": "rule", "rule_id": "702.19b", "number": 1}]
    score = score_citations(case, citations, {"invalid_count": 2})
    assert score == {"cited_satisfies_required": True, "cited_count": 1, "invalid_citations": 2}
    assert score_citations(case, [], None)["cited_satisfies_required"] is False
    assert score_citations(_case(), [], None)["cited_satisfies_required"] is None


@pytest.mark.parametrize(
    ("answer", "verdict"),
    [
        ("Yes. Hexproof only stops opponents.", "yes"),
        ("yes, you can [1].", "yes"),
        ("**No**, because it is a state-based action.", "no"),
        ("No — it stays blocked.", "no"),
        ("Answer: No. It isn't destroyed.", "no"),
        ("The answer is no. It stays blocked.", "no"),
        ("You can, yes: hexproof only stops opponents.", "yes"),
        ("Yes, and there is no restriction.", "yes"),
        ("It depends on the timing.", "unclear"),
        ("Nope.", "unclear"),
        ("Not quite; the creature survives.", "unclear"),
        ("It may or may not, yes or no depending on timing.", "unclear"),
        ("Trample assigns lethal damage first. Yes, the rest goes through.", "unclear"),
        ("", "unclear"),
        (None, "unclear"),
    ],
)
def test_parse_verdict(answer, verdict):
    assert parse_verdict(answer) == verdict
