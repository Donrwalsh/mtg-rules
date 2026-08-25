import json
from pathlib import Path

from mtg_embed.ids import ruling_point_id
from mtg_embed.sources.rulings import load_ruling_chunks

CARD_ROWS = [
    {
        "oracle_id": "oid-1",
        "name": "Lightning Bolt",
        "oracle_text": "Lightning Bolt deals 3 damage to any target.",
        "type_line": "Instant",
        "mana_cost": "{R}",
        "content_hash": "hcard1",
    }
]

RULING_ROWS = [
    {"oracle_id": "oid-1", "published_at": "2020-01-01", "comment": "First ruling.", "content_hash": "hr1"},
    {"oracle_id": "oid-1", "published_at": "2020-01-02", "comment": "Second ruling.", "content_hash": "hr2"},
    {"oracle_id": "oid-missing", "published_at": "2020-01-01", "comment": "Orphan ruling.", "content_hash": "hr3"},
]


def _write(tmp_path: Path, name: str, rows: list[dict]) -> Path:
    dest = tmp_path / name
    with dest.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
    return dest


def test_ruling_prefix_includes_card_name_and_oracle_text_snippet(tmp_path):
    rulings_path = _write(tmp_path, "rulings.jsonl", RULING_ROWS)
    cards_path = _write(tmp_path, "cards.jsonl", CARD_ROWS)

    chunks, _ = load_ruling_chunks(rulings_path, cards_path)
    first = chunks[0]

    assert first.text_to_embed == (
        "Lightning Bolt — Lightning Bolt deals 3 damage to any target.\nRuling: First ruling."
    )
    assert first.payload["card_name"] == "Lightning Bolt"
    assert first.payload["text"] == "First ruling."
    assert first.content_hash == "hr1"


def test_ruling_index_increments_per_oracle_id(tmp_path):
    rulings_path = _write(tmp_path, "rulings.jsonl", RULING_ROWS)
    cards_path = _write(tmp_path, "cards.jsonl", CARD_ROWS)

    chunks, _ = load_ruling_chunks(rulings_path, cards_path)

    assert chunks[0].point_id == ruling_point_id("oid-1", 0)
    assert chunks[1].point_id == ruling_point_id("oid-1", 1)


def test_ruling_with_no_matching_card_is_skipped_and_counted(tmp_path):
    rulings_path = _write(tmp_path, "rulings.jsonl", RULING_ROWS)
    cards_path = _write(tmp_path, "cards.jsonl", CARD_ROWS)

    chunks, skipped_no_card = load_ruling_chunks(rulings_path, cards_path)

    assert skipped_no_card == 1
    assert len(chunks) == 2


def test_limit_caps_number_of_chunks(tmp_path):
    rulings_path = _write(tmp_path, "rulings.jsonl", RULING_ROWS)
    cards_path = _write(tmp_path, "cards.jsonl", CARD_ROWS)

    chunks, _ = load_ruling_chunks(rulings_path, cards_path, limit=1)
    assert len(chunks) == 1


def test_ruling_index_is_per_oracle_id_not_global(tmp_path):
    """Verify ruling indices are tracked per-oracle_id, not globally.

    This test would fail if the implementation incorrectly used a single global
    counter instead of defaultdict(int) keyed by oracle_id. With interleaved
    oracle_ids, a global counter bug would produce (0, 1, 2) instead of the
    correct (0, 0, 1).
    """
    # Two cards with different oracle_ids
    card_rows = [
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
            "name": "Counterspell",
            "oracle_text": "Counter target spell.",
            "type_line": "Instant",
            "mana_cost": "{UU}",
            "content_hash": "hcard2",
        },
    ]

    # Interleaved rulings: oid-1, oid-2, oid-1
    ruling_rows = [
        {"oracle_id": "oid-1", "published_at": "2020-01-01", "comment": "First oid-1 ruling.", "content_hash": "hr1"},
        {"oracle_id": "oid-2", "published_at": "2020-01-01", "comment": "First oid-2 ruling.", "content_hash": "hr2"},
        {"oracle_id": "oid-1", "published_at": "2020-01-02", "comment": "Second oid-1 ruling.", "content_hash": "hr3"},
    ]

    rulings_path = _write(tmp_path, "rulings.jsonl", ruling_rows)
    cards_path = _write(tmp_path, "cards.jsonl", card_rows)

    chunks, _ = load_ruling_chunks(rulings_path, cards_path)

    # With a global counter bug, indices would be (0, 1, 2).
    # With correct per-oracle_id tracking, they should be (0, 0, 1).
    assert chunks[0].point_id == ruling_point_id("oid-1", 0)
    assert chunks[1].point_id == ruling_point_id("oid-2", 0)
    assert chunks[2].point_id == ruling_point_id("oid-1", 1)
