from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class EmbeddableChunk:
    """One point-to-be: everything needed to embed it and store it in Qdrant."""

    point_id: str
    source_type: str  # "rule" | "ruling" | "oracle"
    text_to_embed: str
    content_hash: str
    payload: dict[str, Any]
