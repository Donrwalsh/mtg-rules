from __future__ import annotations

from qdrant_client import QdrantClient


def check_qdrant(client: QdrantClient) -> bool:
    try:
        client.get_collections()
    except Exception:  # noqa: BLE001 -- any failure means "unreachable"
        return False
    return True
