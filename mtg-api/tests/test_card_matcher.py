from mtg_api.card_matcher import CardMatcher

CARDS = [
    {"oracle_id": "oid-1", "name": "Bolt", "oracle_text": "Bolt text."},
    {"oracle_id": "oid-2", "name": "Lightning Bolt", "oracle_text": "Deals 3 damage."},
    {"oracle_id": "oid-3", "name": "Counterspell", "oracle_text": "Counter target spell."},
]


def test_finds_exact_single_word_match():
    matcher = CardMatcher(CARDS)
    matches = matcher.find_matches("does Counterspell stop everything?")
    names = {c["name"] for c in matches}
    assert names == {"Counterspell"}


def test_finds_multi_word_match():
    matcher = CardMatcher(CARDS)
    matches = matcher.find_matches("how good is Lightning Bolt")
    names = {c["name"] for c in matches}
    assert "Lightning Bolt" in names


def test_case_insensitive():
    matcher = CardMatcher(CARDS)
    matches = matcher.find_matches("COUNTERSPELL rules?")
    names = {c["name"] for c in matches}
    assert "Counterspell" in names


def test_word_boundary_prevents_substring_false_positive():
    matcher = CardMatcher(CARDS)
    matches = matcher.find_matches("what does Voltaic Boltcaster do")
    names = {c["name"] for c in matches}
    assert "Bolt" not in names


def test_empty_query_returns_no_matches():
    matcher = CardMatcher(CARDS)
    assert matcher.find_matches("") == []


def test_empty_card_list_returns_no_matches_without_raising():
    matcher = CardMatcher([])
    assert matcher.find_matches("does Counterspell work") == []


def test_no_matches_returns_empty_list():
    matcher = CardMatcher(CARDS)
    assert matcher.find_matches("just a generic rules question") == []
