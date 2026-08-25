from __future__ import annotations

from pathlib import Path

from mtg_ingestion.models import Ruling
from mtg_ingestion.parse._scryfall_io import iter_scryfall_records


def parse_rulings_file(raw_path: Path) -> list[Ruling]:
    return [
        Ruling(
            oracle_id=raw["oracle_id"],
            published_at=raw["published_at"],
            comment=raw["comment"],
        )
        for raw in iter_scryfall_records(raw_path)
    ]
