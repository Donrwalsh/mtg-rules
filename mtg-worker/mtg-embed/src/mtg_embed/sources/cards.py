from __future__ import annotations

import json
from pathlib import Path

from mtg_embed.ids import oracle_point_id
from mtg_embed.models import EmbeddableChunk


def _read_rows(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def load_card_chunks(path: Path, limit: int | None = None) -> list[EmbeddableChunk]:
    rows = _read_rows(path)
    if limit is not None:
        rows = rows[:limit]

    chunks: list[EmbeddableChunk] = []
    for row in rows:
        oracle_text = row.get("oracle_text", "")
        text_to_embed = "\n".join(
            [row["name"], row.get("type_line", ""), row.get("mana_cost") or "", oracle_text]
        )
        chunks.append(
            EmbeddableChunk(
                point_id=oracle_point_id(row["oracle_id"]),
                source_type="oracle",
                text_to_embed=text_to_embed,
                content_hash=row["content_hash"],
                payload={
                    "source_type": "oracle",
                    "content_hash": row["content_hash"],
                    "text": oracle_text,
                    "card_name": row["name"],
                },
            )
        )
    return chunks
