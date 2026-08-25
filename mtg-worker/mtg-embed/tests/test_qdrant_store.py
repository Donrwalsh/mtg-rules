from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

from mtg_embed.models import EmbeddableChunk
from mtg_embed.qdrant_store import QdrantStore
from mtg_embed.sparse_embedder import SparseVector


def _store() -> QdrantStore:
    # In-memory Qdrant: real client, real semantics, no network or docker.
    client = QdrantClient(location=":memory:")
    return QdrantStore(client, "test_collection")


def test_ensure_collection_is_idempotent():
    store = _store()
    store.ensure_collection(dense_size=4)
    store.ensure_collection(dense_size=4)  # must not raise on second call
    assert store.existing_hashes(["00000000-0000-0000-0000-000000000000"]) == {}


def test_upsert_then_existing_hashes_round_trips_content_hash():
    store = _store()
    store.ensure_collection(dense_size=4)

    chunk = EmbeddableChunk(
        point_id="6f6e0e2a-6f0a-4c1a-9f1a-6b0c9f6f6e2a",
        source_type="rule",
        text_to_embed="text",
        content_hash="hash-1",
        payload={"source_type": "rule", "content_hash": "hash-1", "text": "text"},
    )
    store.upsert(
        [chunk], [[0.1, 0.2, 0.3, 0.4]], [SparseVector(indices=[0, 2], values=[0.5, 0.5])]
    )

    assert store.existing_hashes([chunk.point_id]) == {chunk.point_id: "hash-1"}


def test_upsert_stores_both_dense_and_sparse_vectors():
    store = _store()
    store.ensure_collection(dense_size=4)

    chunk = EmbeddableChunk(
        point_id="6f6e0e2a-6f0a-4c1a-9f1a-6b0c9f6f6e2a",
        source_type="rule",
        text_to_embed="text",
        content_hash="hash-1",
        payload={"source_type": "rule", "content_hash": "hash-1", "text": "text"},
    )
    # Unit vector: COSINE-distance collections store vectors normalized, so
    # only an already-unit-length vector round-trips exactly.
    store.upsert(
        [chunk], [[1.0, 0.0, 0.0, 0.0]], [SparseVector(indices=[0, 2], values=[0.5, 0.75])]
    )

    points = store._client.retrieve(
        collection_name="test_collection", ids=[chunk.point_id], with_vectors=True
    )
    vectors = points[0].vector
    assert vectors["dense"] == [1.0, 0.0, 0.0, 0.0]
    assert list(vectors["sparse"].indices) == [0, 2]
    assert list(vectors["sparse"].values) == [0.5, 0.75]


def test_existing_hashes_empty_for_unknown_ids():
    store = _store()
    store.ensure_collection(dense_size=4)
    assert store.existing_hashes(["00000000-0000-0000-0000-000000000000"]) == {}


def test_existing_hashes_of_empty_list_makes_no_call():
    store = _store()
    store.ensure_collection(dense_size=4)
    assert store.existing_hashes([]) == {}


def test_existing_hashes_tolerates_missing_content_hash_key():
    """Test that points without content_hash key are safely skipped."""
    store = _store()
    store.ensure_collection(dense_size=4)

    chunk_with_hash = EmbeddableChunk(
        point_id="6f6e0e2a-6f0a-4c1a-9f1a-6b0c9f6f6e2a",
        source_type="rule",
        text_to_embed="text",
        content_hash="hash-1",
        payload={"source_type": "rule", "content_hash": "hash-1", "text": "text"},
    )
    store.upsert(
        [chunk_with_hash], [[0.1, 0.2, 0.3, 0.4]], [SparseVector(indices=[0], values=[1.0])]
    )

    # Directly insert a point via client with payload missing content_hash,
    # using the same named-vector shape ensure_collection now requires.
    point_without_hash = qmodels.PointStruct(
        id="8a8a8a8a-8a8a-8a8a-8a8a-8a8a8a8a8a8a",
        vector={
            "dense": [0.5, 0.5, 0.5, 0.5],
            "sparse": qmodels.SparseVector(indices=[0], values=[1.0]),
        },
        payload={"source_type": "rule", "text": "text"},  # no content_hash
    )
    store._client.upsert(collection_name="test_collection", points=[point_without_hash])

    result = store.existing_hashes(
        [chunk_with_hash.point_id, "8a8a8a8a-8a8a-8a8a-8a8a-8a8a8a8a8a8a"]
    )

    assert result == {chunk_with_hash.point_id: "hash-1"}
    assert "8a8a8a8a-8a8a-8a8a-8a8a-8a8a8a8a8a8a" not in result
