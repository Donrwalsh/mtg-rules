from qdrant_client import QdrantClient

from mtg_embed.models import EmbeddableChunk
from mtg_embed.qdrant_store import QdrantStore


def _store() -> QdrantStore:
    # In-memory Qdrant: real client, real semantics, no network or docker.
    client = QdrantClient(location=":memory:")
    return QdrantStore(client, "test_collection")


def test_ensure_collection_is_idempotent():
    store = _store()
    store.ensure_collection(vector_size=4)
    store.ensure_collection(vector_size=4)  # must not raise on second call
    assert store.existing_hashes(["00000000-0000-0000-0000-000000000000"]) == {}


def test_upsert_then_existing_hashes_round_trips_content_hash():
    store = _store()
    store.ensure_collection(vector_size=4)

    chunk = EmbeddableChunk(
        point_id="6f6e0e2a-6f0a-4c1a-9f1a-6b0c9f6f6e2a",
        source_type="rule",
        text_to_embed="text",
        content_hash="hash-1",
        payload={"source_type": "rule", "content_hash": "hash-1", "text": "text"},
    )
    store.upsert([chunk], [[0.1, 0.2, 0.3, 0.4]])

    assert store.existing_hashes([chunk.point_id]) == {chunk.point_id: "hash-1"}


def test_existing_hashes_empty_for_unknown_ids():
    store = _store()
    store.ensure_collection(vector_size=4)
    assert store.existing_hashes(["00000000-0000-0000-0000-000000000000"]) == {}


def test_existing_hashes_of_empty_list_makes_no_call():
    store = _store()
    store.ensure_collection(vector_size=4)
    assert store.existing_hashes([]) == {}


def test_existing_hashes_tolerates_missing_content_hash_key():
    """Test that points without content_hash key are safely skipped."""
    store = _store()
    store.ensure_collection(vector_size=4)

    # Upsert a point with content_hash via store
    chunk_with_hash = EmbeddableChunk(
        point_id="6f6e0e2a-6f0a-4c1a-9f1a-6b0c9f6f6e2a",
        source_type="rule",
        text_to_embed="text",
        content_hash="hash-1",
        payload={"source_type": "rule", "content_hash": "hash-1", "text": "text"},
    )
    store.upsert([chunk_with_hash], [[0.1, 0.2, 0.3, 0.4]])

    # Directly insert a point via client with payload missing content_hash
    # This simulates a point from future/other code or partial migration
    from qdrant_client.http import models as qmodels
    point_without_hash = qmodels.PointStruct(
        id="8a8a8a8a-8a8a-8a8a-8a8a-8a8a8a8a8a8a",
        vector=[0.5, 0.5, 0.5, 0.5],
        payload={"source_type": "rule", "text": "text"},  # no content_hash
    )
    store._client.upsert(
        collection_name="test_collection",
        points=[point_without_hash]
    )

    # Call existing_hashes with both point IDs
    # Should not raise KeyError, and should only return the point with content_hash
    result = store.existing_hashes([
        chunk_with_hash.point_id,
        "8a8a8a8a-8a8a-8a8a-8a8a-8a8a8a8a8a8a"
    ])

    assert result == {chunk_with_hash.point_id: "hash-1"}
    assert "8a8a8a8a-8a8a-8a8a-8a8a-8a8a8a8a8a8a" not in result
