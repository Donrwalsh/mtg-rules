import json
from pathlib import Path

from mtg_embed.ids import rule_point_id
from mtg_embed.sources.rules import load_rule_chunks

ROWS = [
    {"rule_id": "601", "text": "Casting Spells", "parent_id": None, "content_hash": "h601"},
    {"rule_id": "601.2", "text": "Playing a Spell", "parent_id": "601", "content_hash": "h6012"},
    {
        "rule_id": "601.2a",
        "text": "A player may cast an instant spell any time they have priority.",
        "parent_id": "601.2",
        "content_hash": "h6012a",
    },
]


def _write_rows(tmp_path: Path) -> Path:
    dest = tmp_path / "rules.jsonl"
    with dest.open("w", encoding="utf-8") as f:
        for row in ROWS:
            f.write(json.dumps(row) + "\n")
    return dest


def test_leaf_rule_gets_full_section_chain_prefix(tmp_path):
    path = _write_rows(tmp_path)
    chunks = {c.payload["rule_id"]: c for c in load_rule_chunks(path)}

    leaf = chunks["601.2a"]
    assert leaf.text_to_embed == (
        "Section 601: Casting Spells > 601.2: Playing a Spell\n"
        "A player may cast an instant spell any time they have priority."
    )
    assert leaf.payload["section_id"] == "601"
    assert leaf.payload["section_title"] == "Casting Spells"


def test_top_level_rule_has_no_prefix(tmp_path):
    path = _write_rows(tmp_path)
    chunks = {c.payload["rule_id"]: c for c in load_rule_chunks(path)}

    top = chunks["601"]
    assert top.text_to_embed == "Casting Spells"
    assert top.payload["section_id"] == "601"
    assert top.payload["section_title"] == "Casting Spells"


def test_point_id_and_payload_shape(tmp_path):
    path = _write_rows(tmp_path)
    chunks = {c.payload["rule_id"]: c for c in load_rule_chunks(path)}

    leaf = chunks["601.2a"]
    assert leaf.point_id == rule_point_id("601.2a")
    assert leaf.source_type == "rule"
    assert leaf.content_hash == "h6012a"
    assert leaf.payload["source_type"] == "rule"
    assert leaf.payload["text"] == "A player may cast an instant spell any time they have priority."


def test_limit_caps_number_of_chunks(tmp_path):
    path = _write_rows(tmp_path)
    chunks = load_rule_chunks(path, limit=2)
    assert len(chunks) == 2
