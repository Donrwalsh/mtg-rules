import json

from mtg_api.keyword_matcher import KeywordMatcher, load_keyword_matcher

RULES = [
    {"rule_id": "601.2", "text": "Casting a spell.", "parent_id": "601"},
    {"rule_id": "702", "text": "Keyword Abilities", "parent_id": None},
    {
        "rule_id": "702.1",
        "text": "Most abilities describe exactly what they do.",
        "parent_id": "702",
    },
    {"rule_id": "702.1a", "text": "Intro subrule.", "parent_id": "702.1"},
    {"rule_id": "702.4", "text": "Double Strike", "parent_id": "702"},
    {"rule_id": "702.4a", "text": "Double strike is a static ability.", "parent_id": "702.4"},
    {"rule_id": "702.7", "text": "First Strike", "parent_id": "702"},
    {"rule_id": "702.7a", "text": "First strike is a static ability.", "parent_id": "702.7"},
    {"rule_id": "702.8", "text": "Flash", "parent_id": "702"},
    {"rule_id": "702.8a", "text": "Flash is a static ability.", "parent_id": "702.8"},
    {"rule_id": "702.11", "text": "Hexproof", "parent_id": "702"},
    {"rule_id": "702.11a", "text": "Hexproof is a static ability.", "parent_id": "702.11"},
    {
        "rule_id": "702.11b",
        "text": "Can't be the target of opponents' spells.",
        "parent_id": "702.11",
    },
    {"rule_id": "702.19", "text": "Trample", "parent_id": "702"},
    {"rule_id": "702.19a", "text": "Trample is a static ability.", "parent_id": "702.19"},
    {"rule_id": "702.34", "text": "Flashback", "parent_id": "702"},
    {"rule_id": "702.34a", "text": "Flashback appears on some instants.", "parent_id": "702.34"},
    {"rule_id": "702.145", "text": "Daybound and Nightbound", "parent_id": "702"},
    {"rule_id": "702.145a", "text": "Day and night.", "parent_id": "702.145"},
    {"rule_id": "702.163", "text": "For Mirrodin!", "parent_id": "702"},
    {"rule_id": "702.163a", "text": "Rebel token.", "parent_id": "702.163"},
    {"rule_id": "702.186", "text": "∞ (Infinity)", "parent_id": "702"},
    {"rule_id": "702.186a", "text": "Infinity text.", "parent_id": "702.186"},
    # Fake keyword that overlaps "double strike"/"first strike", to exercise
    # longest-match resolution.
    {"rule_id": "702.999", "text": "Strike", "parent_id": "702"},
]


def _ids(matches):
    return [m["rule_id"] for m in matches]


def test_finds_single_word_keyword_with_heading_and_subrules_in_order():
    matcher = KeywordMatcher(RULES)
    matches = matcher.find_matches("Can I target my own creature with hexproof?")
    assert _ids(matches) == ["702.11"]
    assert matches[0]["keyword"] == "Hexproof"
    assert [r["rule_id"] for r in matches[0]["rules"]] == ["702.11", "702.11a", "702.11b"]


def test_finds_multi_word_keyword():
    matcher = KeywordMatcher(RULES)
    assert _ids(matcher.find_matches("how does first strike work")) == ["702.7"]


def test_case_insensitive():
    matcher = KeywordMatcher(RULES)
    assert _ids(matcher.find_matches("DOUBLE STRIKE and TRAMPLE")) == ["702.4", "702.19"]


def test_longest_match_wins_on_overlap():
    matcher = KeywordMatcher(RULES)
    assert _ids(matcher.find_matches("does double strike stack?")) == ["702.4"]


def test_non_overlapping_shorter_keyword_still_matches():
    matcher = KeywordMatcher(RULES)
    assert _ids(matcher.find_matches("first strike vs a plain strike")) == ["702.7", "702.999"]


def test_word_boundary_prevents_substring_false_positive():
    matcher = KeywordMatcher(RULES)
    assert _ids(matcher.find_matches("can I cast it with flashback")) == ["702.34"]
    assert matcher.find_matches("the flashy creature") == []


def test_matches_simple_inflections():
    matcher = KeywordMatcher(RULES)
    assert _ids(matcher.find_matches("it tramples over")) == ["702.19"]
    assert _ids(matcher.find_matches("trampling damage")) == ["702.19"]
    assert _ids(matcher.find_matches("damage was trampled")) == ["702.19"]
    assert _ids(matcher.find_matches("I flashed it in")) == ["702.8"]


def test_returns_each_keyword_once():
    matcher = KeywordMatcher(RULES)
    assert _ids(matcher.find_matches("trample and more trample, trampling")) == ["702.19"]


def test_skips_section_intro_rule():
    matcher = KeywordMatcher(RULES)
    assert matcher.find_matches("most abilities describe exactly what they do") == []


def test_strips_trailing_exclamation_mark():
    matcher = KeywordMatcher(RULES)
    assert _ids(matcher.find_matches("how does for mirrodin work")) == ["702.163"]


def test_and_heading_matches_each_half():
    matcher = KeywordMatcher(RULES)
    assert _ids(matcher.find_matches("when does a daybound card transform")) == ["702.145"]
    assert _ids(matcher.find_matches("nightbound")) == ["702.145"]


def test_parenthetical_heading_matches_either_part():
    matcher = KeywordMatcher(RULES)
    assert _ids(matcher.find_matches("what is infinity")) == ["702.186"]


def test_empty_query_returns_no_matches():
    assert KeywordMatcher(RULES).find_matches("") == []


def test_empty_rules_returns_no_matches_without_raising():
    assert KeywordMatcher([]).find_matches("does trample work") == []


def test_no_matches_returns_empty_list():
    assert KeywordMatcher(RULES).find_matches("just a generic rules question") == []


def test_load_keyword_matcher_reads_jsonl(tmp_path):
    path = tmp_path / "rules_2026-01-01.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in RULES) + "\n", encoding="utf-8")
    matcher = load_keyword_matcher(path)
    assert _ids(matcher.find_matches("hexproof")) == ["702.11"]
