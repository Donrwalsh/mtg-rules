from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any


def payload_hash(payload: dict[str, Any]) -> str:
    """Hash of everything stored alongside the vectors. Lets the pipeline
    notice metadata-only changes (a new payload field, a changed link) that
    content_hash -- which covers only the embedded text -- can't see."""
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass
class EmbeddableChunk:
    """One point-to-be: everything needed to embed it and store it in Qdrant."""

    point_id: str
    source_type: str  # "rule" | "ruling" | "oracle"
    text_to_embed: str
    content_hash: str
    payload: dict[str, Any]

    @property
    def stored_payload(self) -> dict[str, Any]:
        """The payload as written to Qdrant, stamped with its own hash."""
        return {**self.payload, "payload_hash": payload_hash(self.payload)}
