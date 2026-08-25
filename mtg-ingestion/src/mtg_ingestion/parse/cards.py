from __future__ import annotations

from pathlib import Path

from mtg_ingestion.models import Card
from mtg_ingestion.parse._scryfall_io import iter_scryfall_records


def parse_cards_file(raw_path: Path) -> list[Card]:
    """Parse a downloaded oracle-cards bulk file (.jsonl.gz or legacy .json)
    into Card records.

    Scryfall's "oracle_cards" bulk type already contains exactly one entry
    per unique Oracle ID, so no dedup is needed here.
    """
    cards: list[Card] = []
    for raw in iter_scryfall_records(raw_path):
        oracle_id = raw.get("oracle_id")
        if not oracle_id:
            # A handful of card objects (e.g. some reversible/art-series
            # cards) don't carry an oracle_id -- skip rather than guess.
            continue

        oracle_text = raw.get("oracle_text", "")
        if not oracle_text and "card_faces" in raw:
            # Double-faced and split cards keep their text on each face
            # instead of the top-level field; join both so rules text for
            # the whole card is captured in one place.
            oracle_text = "\n//\n".join(
                face.get("oracle_text", "") for face in raw["card_faces"]
            )

        cards.append(
            Card(
                oracle_id=oracle_id,
                name=raw["name"],
                oracle_text=oracle_text,
                type_line=raw.get("type_line", ""),
                mana_cost=raw.get("mana_cost") or None,
            )
        )
    return cards
