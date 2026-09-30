from __future__ import annotations

from pydantic import BaseModel


class CardDetails(BaseModel):
    """What the UI shows for a card: its face, not its rules text (that's
    the result's `text`)."""

    name: str
    type_line: str = ""
    mana_cost: str | None = None
    power: str | None = None
    toughness: str | None = None
    loyalty: str | None = None
    image_small: str | None = None
    image_normal: str | None = None


def card_details(row: dict) -> CardDetails:
    """From a parsed cards_*.jsonl row. Only the "normal" image URL is
    stored; Scryfall serves the other sizes at the same path."""
    normal = row.get("image_uri")
    return CardDetails(
        name=row["name"],
        type_line=row.get("type_line") or "",
        mana_cost=row.get("mana_cost"),
        power=row.get("power"),
        toughness=row.get("toughness"),
        loyalty=row.get("loyalty"),
        image_small=normal.replace("/normal/", "/small/", 1) if normal else None,
        image_normal=normal,
    )
