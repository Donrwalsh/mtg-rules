from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from mtg_embed.ids import ruling_point_id
from mtg_embed.models import EmbeddableChunk

_SNIPPET_LEN = 200


def _read_rows(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def load_ruling_chunks(
    rulings_path: Path, cards_path: Path, limit: int | None = None
) -> tuple[list[EmbeddableChunk], int]:
    cards_by_oracle_id = {row["oracle_id"]: row for row in _read_rows(cards_path)}
    rows = _read_rows(rulings_path)

    chunks: list[EmbeddableChunk] = []
    skipped_no_card = 0
    # Index tracks each oracle_id's position in file order (not the kept-chunk
    # count), so it stays stable across runs regardless of which cards match.
    next_index: dict[str, int] = defaultdict(int)

    for row in rows:
        if limit is not None and len(chunks) >= limit:
            break

        oracle_id = row["oracle_id"]
        index = next_index[oracle_id]
        next_index[oracle_id] += 1

        card = cards_by_oracle_id.get(oracle_id)
        if card is None:
            skipped_no_card += 1
            continue

        snippet = card.get("oracle_text", "")[:_SNIPPET_LEN]
        prefix = f"{card['name']} — {snippet}\nRuling: "

        chunks.append(
            EmbeddableChunk(
                point_id=ruling_point_id(oracle_id, index),
                source_type="ruling",
                text_to_embed=f"{prefix}{row['comment']}",
                content_hash=row["content_hash"],
                payload={
                    "source_type": "ruling",
                    "content_hash": row["content_hash"],
                    "text": row["comment"],
                    "card_name": card["name"],
                },
            )
        )

    return chunks, skipped_no_card
