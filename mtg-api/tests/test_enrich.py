from mtg_api.card_matcher import CardMatcher
from mtg_api.enrich import enrich_citations, enrich_results
from mtg_api.models import Citation, QueryResult
from mtg_api.rules_index import RulesIndex

CARDS = CardMatcher(
    [
        {
            "oracle_id": "oid-collar",
            "name": "Basilisk Collar",
            "type_line": "Artifact — Equipment",
            "mana_cost": "{1}",
            "image_uri": "https://cards.scryfall.io/normal/front/c/c/collar.jpg?1",
        }
    ]
)
RULES = RulesIndex(
    [
        {"rule_id": "702", "text": "Keyword Abilities", "parent_id": None},
        {"rule_id": "702.2", "text": "Deathtouch", "parent_id": "702"},
        {"rule_id": "702.2c", "text": "Any nonzero damage is lethal.", "parent_id": "702.2"},
    ]
)


def _result(source: str, **kw) -> QueryResult:
    return QueryResult(
        source=source, title=kw.pop("title", "t"), text="x", score=1.0, match_type="m", **kw
    )


def test_card_oracle_and_ruling_results_get_their_card():
    results = [
        _result("card", oracle_id="oid-collar"),
        _result("oracle", oracle_id="oid-collar"),
        _result("ruling", oracle_id="oid-collar"),
        _result("oracle", oracle_id="unknown"),
    ]
    enrich_results(results, CARDS, RULES)
    assert [r.card.name if r.card else None for r in results] == [
        "Basilisk Collar",
        "Basilisk Collar",
        "Basilisk Collar",
        None,
    ]
    assert results[0].card.image_small == "https://cards.scryfall.io/small/front/c/c/collar.jpg?1"


def test_rule_results_get_a_heading_and_no_card():
    results = [_result("rule", rule_id="702.2c", title="702.2c")]
    enrich_results(results, CARDS, RULES)
    assert results[0].heading == "Deathtouch"
    assert results[0].card is None


def test_citations_are_enriched_the_same_way():
    citations = [
        Citation(number=1, source_type="rule", title="Rule 702.2c", rule_id="702.2c", text="x"),
        Citation(
            number=2,
            source_type="card",
            title="Card — Basilisk Collar",
            oracle_id="oid-collar",
            text="x",
        ),
        Citation(
            number=3,
            source_type="ruling",
            title="Ruling — Basilisk Collar",
            oracle_id="oid-collar",
            text="x",
        ),
    ]
    enrich_citations(citations, CARDS, RULES)
    assert citations[0].heading == "Deathtouch"
    assert citations[1].card.mana_cost == "{1}"
    assert citations[2].card.name == "Basilisk Collar"
