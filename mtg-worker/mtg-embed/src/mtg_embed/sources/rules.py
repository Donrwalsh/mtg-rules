from __future__ import annotations

import json
from pathlib import Path

from mtg_embed.ids import rule_point_id
from mtg_embed.models import EmbeddableChunk


def _read_rows(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _section_chain(rule_id: str, by_id: dict[str, dict]) -> tuple[str, str, str]:
    """Walk parent_id up to the top-level ancestor.

    Returns (section_id, section_title, prefix) where prefix looks like
    "Section 601: Casting Spells > 601.2: Playing a Spell\n" -- empty for a
    top-level rule, which has no ancestors to chain.
    """
    chain: list[dict] = []
    current = by_id.get(rule_id)
    while current is not None:
        chain.append(current)
        parent_id = current.get("parent_id")
        current = by_id.get(parent_id) if parent_id else None
    chain.reverse()  # top-level ancestor first

    top = chain[0]
    section_id = top["rule_id"]
    section_title = top["text"]

    ancestors = chain[:-1]  # exclude the rule itself
    parts = [
        f"Section {row['rule_id']}: {row['text']}" if i == 0 else f"{row['rule_id']}: {row['text']}"
        for i, row in enumerate(ancestors)
    ]
    prefix = " > ".join(parts) + "\n" if parts else ""
    return section_id, section_title, prefix


def load_rule_chunks(path: Path, limit: int | None = None) -> list[EmbeddableChunk]:
    rows = _read_rows(path)
    by_id = {row["rule_id"]: row for row in rows}

    chunks: list[EmbeddableChunk] = []
    for row in rows:
        if limit is not None and len(chunks) >= limit:
            break

        section_id, section_title, prefix = _section_chain(row["rule_id"], by_id)
        chunks.append(
            EmbeddableChunk(
                point_id=rule_point_id(row["rule_id"]),
                source_type="rule",
                text_to_embed=f"{prefix}{row['text']}",
                content_hash=row["content_hash"],
                payload={
                    "source_type": "rule",
                    "content_hash": row["content_hash"],
                    "text": row["text"],
                    "rule_id": row["rule_id"],
                    "section_id": section_id,
                    "section_title": section_title,
                },
            )
        )
    return chunks
