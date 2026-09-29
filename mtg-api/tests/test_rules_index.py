import json

from mtg_api.rules_index import CR_SECTIONS, RulesIndex, load_rules_index

RULES = [
    {"rule_id": "702", "text": "Keyword Abilities", "parent_id": None},
    {"rule_id": "702.11", "text": "Hexproof", "parent_id": "702"},
    {"rule_id": "702.11a", "text": "Hexproof is a static ability.", "parent_id": "702.11"},
    {"rule_id": "702.11b", "text": "Can't be targeted by opponents.", "parent_id": "702.11"},
    # Parent row absent from the file: ancestors must stop, not crash.
    {"rule_id": "704.5b", "text": "Empty library loses.", "parent_id": "704.5"},
]


def test_get_and_contains():
    index = RulesIndex(RULES)
    assert index.get("702.11b")["text"] == "Can't be targeted by opponents."
    assert "702.11" in index
    assert "999.9" not in index
    assert index.get("999.9") is None


def test_children_are_direct_only_in_file_order():
    index = RulesIndex(RULES)
    assert [r["rule_id"] for r in index.children("702.11")] == ["702.11a", "702.11b"]
    assert [r["rule_id"] for r in index.children("702")] == ["702.11"]
    assert index.children("702.11b") == []
    assert index.children("999.9") == []


def test_ancestors_are_top_level_first_and_stop_at_a_missing_parent():
    index = RulesIndex(RULES)
    assert [r["rule_id"] for r in index.ancestors("702.11b")] == ["702", "702.11"]
    assert index.ancestors("702") == []
    assert index.ancestors("704.5b") == []
    assert index.ancestors("999.9") == []


def test_load_reads_jsonl_and_ingest_date_from_file_name(tmp_path):
    path = tmp_path / "rules_2026-08-25.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in RULES) + "\n", encoding="utf-8")
    index = load_rules_index(path)
    assert index.ingested_at == "2026-08-25"
    assert "702.11a" in index
    assert len(index.rules) == len(RULES)


def test_ingest_date_is_none_when_file_name_has_no_date(tmp_path):
    path = tmp_path / "rules.jsonl"
    path.write_text(json.dumps(RULES[0]) + "\n", encoding="utf-8")
    assert load_rules_index(path).ingested_at is None


HEADING_RULES = [
    {"rule_id": "100", "text": "General", "parent_id": None},
    {"rule_id": "100.1", "text": "These Magic rules apply to any Magic game.", "parent_id": "100"},
    {"rule_id": "510", "text": "Combat Damage Step", "parent_id": None},
    {"rule_id": "510.1", "text": "First, the active player announces:", "parent_id": "510"},
    {"rule_id": "510.1c", "text": "A blocked creature assigns damage.", "parent_id": "510.1"},
    {"rule_id": "702", "text": "Keyword Abilities", "parent_id": None},
    {"rule_id": "702.2", "text": "Deathtouch", "parent_id": "702"},
    {"rule_id": "702.2c", "text": "Any nonzero damage is lethal.", "parent_id": "702.2"},
]


def test_heading_is_nearest_title_like_rule():
    index = RulesIndex(HEADING_RULES)
    assert index.heading("702.2c") == "Deathtouch"
    assert index.heading("702.2") == "Deathtouch"
    assert index.heading("510.1c") == "Combat Damage Step"
    assert index.heading("100.1") == "General"
    assert index.heading("100") == "General"


def test_heading_of_unknown_rule_is_none():
    assert RulesIndex(HEADING_RULES).heading("999.9z") is None


def test_long_or_punctuated_text_is_not_a_heading():
    rules = [{"rule_id": "800.1", "text": "x" * 61, "parent_id": None}]
    assert RulesIndex(rules).heading("800.1") is None


def test_table_of_contents_groups_top_level_rules_by_section():
    toc = RulesIndex(HEADING_RULES).table_of_contents()
    assert toc == [
        {"number": 1, "title": "Game Concepts", "rules": [{"rule_id": "100", "text": "General"}]},
        {
            "number": 5,
            "title": "Turn Structure",
            "rules": [{"rule_id": "510", "text": "Combat Damage Step"}],
        },
        {
            "number": 7,
            "title": "Additional Rules",
            "rules": [{"rule_id": "702", "text": "Keyword Abilities"}],
        },
    ]


def test_cr_sections_are_the_nine_sections():
    assert CR_SECTIONS[6] == "Spells, Abilities, and Effects"
    assert sorted(CR_SECTIONS) == list(range(1, 10))
