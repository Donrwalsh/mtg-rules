import gzip
import json
from pathlib import Path

from mtg_ingestion.models import Card, RuleChunk
from mtg_ingestion.parse.cards import parse_cards_file
from mtg_ingestion.parse.rulings import parse_rulings_file
from mtg_ingestion.storage import read_jsonl, write_jsonl

RAW_CARDS = [
    {
        "oracle_id": "abc-123",
        "name": "Llanowar Elves",
        "oracle_text": "{T}: Add {G}.",
        "type_line": "Creature — Elf Druid",
        "mana_cost": "{G}",
    },
    {
        # double-faced card: text lives on card_faces, not the top level
        "oracle_id": "def-456",
        "name": "Delver of Secrets // Insectile Aberration",
        "type_line": "Creature — Human Wizard // Creature — Human Insect",
        "mana_cost": "{U}",
        "card_faces": [
            {"oracle_text": "At the beginning of your upkeep, look at the top card."},
            {"oracle_text": ""},
        ],
    },
    {
        # missing oracle_id entirely -- should be skipped, not crash
        "name": "Some Art Series Card",
    },
]

RAW_RULINGS = [
    {"oracle_id": "abc-123", "published_at": "2020-01-01", "comment": "This is a ruling."},
]


def test_parse_cards_handles_double_faced_and_missing_oracle_id(tmp_path: Path) -> None:
    raw_path = tmp_path / "oracle_cards.json"
    raw_path.write_text(json.dumps(RAW_CARDS))

    cards = parse_cards_file(raw_path)

    assert len(cards) == 2  # the oracle_id-less entry was skipped
    delver = next(c for c in cards if c.oracle_id == "def-456")
    assert "look at the top card" in delver.oracle_text


def test_parse_rulings(tmp_path: Path) -> None:
    raw_path = tmp_path / "rulings.json"
    raw_path.write_text(json.dumps(RAW_RULINGS))

    rulings = parse_rulings_file(raw_path)

    assert len(rulings) == 1
    assert rulings[0].oracle_id == "abc-123"
    assert rulings[0].comment == "This is a ruling."


def test_jsonl_round_trip(tmp_path: Path) -> None:
    chunks = [
        RuleChunk(rule_id="100", text="General", parent_id=None),
        RuleChunk(rule_id="100.1", text="Something else", parent_id="100"),
    ]
    dest = tmp_path / "rules.jsonl"

    count = write_jsonl(chunks, dest)
    assert count == 2

    loaded = read_jsonl(dest, RuleChunk)
    assert loaded == chunks


def test_parse_cards_handles_current_jsonl_gz_format(tmp_path: Path) -> None:
    """Scryfall's current bulk-data format (post July 20, 2026): a real
    gzip archive containing newline-delimited JSON, not a JSON array."""
    raw_path = tmp_path / "oracle_cards.jsonl.gz"
    with gzip.open(raw_path, "wt", encoding="utf-8") as f:
        for record in RAW_CARDS:
            f.write(json.dumps(record) + "\n")

    cards = parse_cards_file(raw_path)

    assert len(cards) == 2
    assert {c.oracle_id for c in cards} == {"abc-123", "def-456"}


def test_parse_rulings_handles_current_jsonl_gz_format(tmp_path: Path) -> None:
    raw_path = tmp_path / "rulings.jsonl.gz"
    with gzip.open(raw_path, "wt", encoding="utf-8") as f:
        for record in RAW_RULINGS:
            f.write(json.dumps(record) + "\n")

    rulings = parse_rulings_file(raw_path)

    assert len(rulings) == 1
    assert rulings[0].oracle_id == "abc-123"


def test_card_content_hash_ignores_field_order_but_not_content() -> None:
    a = Card(oracle_id="x", name="Foo", oracle_text="bar", type_line="Creature")
    b = Card(oracle_id="x", name="Foo", oracle_text="bar", type_line="Creature")
    c = Card(oracle_id="x", name="Foo", oracle_text="different", type_line="Creature")

    assert a.content_hash == b.content_hash
    assert a.content_hash != c.content_hash
