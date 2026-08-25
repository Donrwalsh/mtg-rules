import json
from pathlib import Path

from mtg_embed.ids import oracle_point_id
from mtg_embed.sources.cards import load_card_chunks

ROWS = [
    {
        "oracle_id": "oid-1",
        "name": "Lightning Bolt",
        "oracle_text": "Lightning Bolt deals 3 damage to any target.",
        "type_line": "Instant",
        "mana_cost": "{R}",
        "content_hash": "hcard1",
    },
    {
        "oracle_id": "oid-2",
        "name": "Static Orb",
        "oracle_text": "As long as this artifact is untapped, players can't untap more than two permanents during their untap steps.",
        "type_line": "Artifact",
        "mana_cost": "{3}",
        "content_hash": "hcard2",
    },
]


def _write_rows(tmp_path: Path) -> Path:
    dest = tmp_path / "cards.jsonl"
    with dest.open("w", encoding="utf-8") as f:
        for row in ROWS:
            f.write(json.dumps(row) + "\n")
    return dest


def test_card_chunk_has_no_prefix_and_joins_fields(tmp_path):
    path = _write_rows(tmp_path)
    chunks = {c.payload["card_name"]: c for c in load_card_chunks(path)}

    bolt = chunks["Lightning Bolt"]
    assert bolt.text_to_embed == (
        "Lightning Bolt\nInstant\n{R}\nLightning Bolt deals 3 damage to any target."
    )
    assert bolt.point_id == oracle_point_id("oid-1")
    assert bolt.source_type == "oracle"
    assert bolt.content_hash == "hcard1"
    assert bolt.payload["source_type"] == "oracle"
    assert bolt.payload["text"] == "Lightning Bolt deals 3 damage to any target."


def test_limit_caps_number_of_chunks(tmp_path):
    path = _write_rows(tmp_path)
    chunks = load_card_chunks(path, limit=1)
    assert len(chunks) == 1
