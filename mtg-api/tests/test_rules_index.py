import json

from mtg_api.rules_index import RulesIndex, load_rules_index

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
