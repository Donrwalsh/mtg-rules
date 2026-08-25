from __future__ import annotations

from qdrant_client import QdrantClient


def check_qdrant(client: QdrantClient) -> bool:
    try:
        client.get_collections()
    except Exception:
        return False
    return True
