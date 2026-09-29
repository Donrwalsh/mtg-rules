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
        "scryfall_uri": "https://scryfall.com/card/m10/155/llanowar-elves?utm_source=api",
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


def test_parse_cards_keeps_scryfall_uri_without_query_string(tmp_path: Path) -> None:
    raw = tmp_path / "oracle_cards.jsonl.gz"
    with gzip.open(raw, "wt", encoding="utf-8") as f:
        for row in RAW_CARDS:
            f.write(json.dumps(row) + "\n")
    cards = {c.oracle_id: c for c in parse_cards_file(raw)}
    assert cards["abc-123"].scryfall_uri == "https://scryfall.com/card/m10/155/llanowar-elves"
    assert cards["def-456"].scryfall_uri is None


def test_scryfall_uri_is_not_part_of_content_hash() -> None:
    base = {"oracle_id": "o", "name": "N", "oracle_text": "t", "type_line": "x", "mana_cost": None}
    assert Card(**base).content_hash == Card(**base, scryfall_uri="https://a").content_hash


NORMAL = "https://cards.scryfall.io/normal/front/a/b/{}.jpg?1"


def test_parse_cards_keeps_display_fields(tmp_path: Path) -> None:
    raw = [
        {
            "oracle_id": "bears",
            "name": "Grizzly Bears",
            "oracle_text": "",
            "type_line": "Creature — Bear",
            "mana_cost": "{1}{G}",
            "power": "2",
            "toughness": "2",
            "image_uris": {"small": "ignored", "normal": NORMAL.format("bears")},
        },
        {
            # Double-faced: P/T and images live on the faces; use the front.
            "oracle_id": "delver",
            "name": "Delver of Secrets // Insectile Aberration",
            "type_line": "Creature — Human Wizard // Creature — Human Insect",
            "mana_cost": "{U}",
            "card_faces": [
                {
                    "oracle_text": "Look at the top card.",
                    "power": "1",
                    "toughness": "1",
                    "image_uris": {"normal": NORMAL.format("delver")},
                },
                {"oracle_text": "Flying", "power": "3", "toughness": "2"},
            ],
        },
        {
            "oracle_id": "jace",
            "name": "Jace Beleren",
            "oracle_text": "+2: Each player draws a card.",
            "type_line": "Legendary Planeswalker — Jace",
            "mana_cost": "{1}{U}{U}",
            "loyalty": "3",
        },
    ]
    raw_path = tmp_path / "oracle_cards.json"
    raw_path.write_text(json.dumps(raw))

    cards = {c.oracle_id: c for c in parse_cards_file(raw_path)}

    bears = cards["bears"]
    assert (bears.power, bears.toughness, bears.loyalty) == ("2", "2", None)
    assert bears.image_uri == NORMAL.format("bears")
    delver = cards["delver"]
    assert (delver.power, delver.toughness) == ("1", "1")
    assert delver.image_uri == NORMAL.format("delver")
    jace = cards["jace"]
    assert (jace.power, jace.loyalty, jace.image_uri) == (None, "3", None)


def test_display_fields_do_not_change_content_hash() -> None:
    plain = Card(oracle_id="a", name="N", oracle_text="t", type_line="T", mana_cost="{1}")
    dressed = Card(
        oracle_id="a",
        name="N",
        oracle_text="t",
        type_line="T",
        mana_cost="{1}",
        power="2",
        toughness="2",
        loyalty="4",
        image_uri=NORMAL.format("a"),
    )
    assert plain.content_hash == dressed.content_hash
