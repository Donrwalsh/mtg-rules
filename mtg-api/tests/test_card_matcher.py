from mtg_api.card_matcher import CardMatcher, ordinary_words

CARDS = [
    {"oracle_id": "oid-1", "name": "Bolt", "oracle_text": "Bolt text."},
    {"oracle_id": "oid-2", "name": "Lightning Bolt", "oracle_text": "Deals 3 damage."},
    {"oracle_id": "oid-3", "name": "Counterspell", "oracle_text": "Counter target spell."},
    {"oracle_id": "oid-4", "name": "Vigilance", "oracle_text": "Target creature gains vigilance."},
    {"oracle_id": "oid-5", "name": "Humility", "oracle_text": "Each creature is 1/1."},
]


def _names(matcher: CardMatcher, query: str) -> set[str]:
    return {c["name"] for c in matcher.find_matches(query)}


def test_ordinary_words_counts_only_lowercase_uses():
    rules = [
        {"text": "Vigilance: attacking doesn't cause it to tap. See vigilance."},
        {"text": "A creature with vigilance can attack. Vigilance is static."},
        {"text": "Example: Humility and vigilance."},
    ]
    assert ordinary_words(rules, min_count=3) == {"vigilance"}


def test_ordinary_words_ignores_words_below_min_count():
    rules = [{"text": "you may sacrifice it"}, {"text": "sacrifice a creature"}]
    assert ordinary_words(rules, min_count=3) == set()


def test_ordinary_word_card_not_matched_in_lowercase():
    matcher = CardMatcher(CARDS, ordinary_words={"vigilance"})
    assert _names(matcher, "does a creature with vigilance tap when it attacks") == set()


def test_ordinary_word_card_matched_when_capitalized_mid_sentence():
    matcher = CardMatcher(CARDS, ordinary_words={"vigilance"})
    assert _names(matcher, "what does the card Vigilance do") == {"Vigilance"}


def test_ordinary_word_card_not_matched_at_sentence_start():
    matcher = CardMatcher(CARDS, ordinary_words={"vigilance"})
    assert _names(matcher, "Vigilance means it doesn't tap.") == set()
    assert _names(matcher, "I attack. Vigilance means it doesn't tap.") == set()


def test_other_single_word_card_still_matched_in_lowercase():
    matcher = CardMatcher(CARDS, ordinary_words={"vigilance"})
    assert _names(matcher, "does humility affect tokens") == {"Humility"}


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


def test_name_inside_a_longer_match_is_dropped():
    matcher = CardMatcher(CARDS)
    matches = matcher.find_matches("how good is Lightning Bolt")
    names = {c["name"] for c in matches}
    assert names == {"Lightning Bolt"}


def test_same_name_elsewhere_in_query_is_kept():
    matcher = CardMatcher(CARDS)
    matches = matcher.find_matches("is Bolt better than Lightning Bolt")
    names = {c["name"] for c in matches}
    assert names == {"Bolt", "Lightning Bolt"}


def test_separate_cards_are_all_matched():
    matcher = CardMatcher(CARDS)
    matches = matcher.find_matches("Lightning Bolt in response to Counterspell")
    names = {c["name"] for c in matches}
    assert names == {"Lightning Bolt", "Counterspell"}


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


def test_by_oracle_id_returns_the_loaded_row():
    bolt = {"oracle_id": "oid-bolt", "name": "Lightning Bolt"}
    matcher = CardMatcher([bolt, {"oracle_id": "oid-x", "name": "Counterspell"}])
    assert matcher.by_oracle_id("oid-bolt") is bolt
    assert matcher.by_oracle_id("missing") is None
    assert matcher.by_oracle_id(None) is None
