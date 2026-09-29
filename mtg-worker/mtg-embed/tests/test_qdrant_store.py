from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

from mtg_embed.models import EmbeddableChunk, payload_hash
from mtg_embed.qdrant_store import QdrantStore, StoredHashes
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
    store.upsert([chunk], [[0.1, 0.2, 0.3, 0.4]], [SparseVector(indices=[0, 2], values=[0.5, 0.5])])

    assert store.existing_hashes([chunk.point_id]) == {
        chunk.point_id: StoredHashes("hash-1", payload_hash(chunk.payload))
    }


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

    assert result == {
        chunk_with_hash.point_id: StoredHashes("hash-1", payload_hash(chunk_with_hash.payload))
    }
    assert "8a8a8a8a-8a8a-8a8a-8a8a-8a8a8a8a8a8a" not in result


def _raw_point(point_id: str, payload: dict) -> qmodels.PointStruct:
    return qmodels.PointStruct(
        id=point_id,
        vector={
            "dense": [1.0, 0.0, 0.0, 0.0],
            "sparse": qmodels.SparseVector(indices=[0], values=[1.0]),
        },
        payload=payload,
    )


def test_existing_hashes_reports_missing_payload_hash_as_none():
    store = _store()
    store.ensure_collection(dense_size=4)
    point_id = "6f6e0e2a-6f0a-4c1a-9f1a-6b0c9f6f6e2a"
    store._client.upsert(
        collection_name="test_collection",
        points=[_raw_point(point_id, {"content_hash": "hash-1", "text": "t"})],
    )
    assert store.existing_hashes([point_id]) == {point_id: StoredHashes("hash-1", None)}


def test_overwrite_payloads_replaces_payload_and_keeps_vectors():
    store = _store()
    store.ensure_collection(dense_size=4)
    point_id = "6f6e0e2a-6f0a-4c1a-9f1a-6b0c9f6f6e2a"
    store._client.upsert(
        collection_name="test_collection",
        points=[_raw_point(point_id, {"content_hash": "hash-1", "stale": True})],
    )
    chunk = EmbeddableChunk(
        point_id=point_id,
        source_type="oracle",
        text_to_embed="text",
        content_hash="hash-1",
        payload={"content_hash": "hash-1", "scryfall_uri": "https://x"},
    )

    store.overwrite_payloads([chunk])

    point = store._client.retrieve(
        collection_name="test_collection", ids=[point_id], with_vectors=True
    )[0]
    assert point.payload == chunk.stored_payload
    assert "stale" not in point.payload
    assert point.vector["dense"] == [1.0, 0.0, 0.0, 0.0]


def test_overwrite_payloads_of_empty_list_is_a_no_op():
    store = _store()
    store.ensure_collection(dense_size=4)
    store.overwrite_payloads([])


def _point(point_id: str, source_type: str) -> EmbeddableChunk:
    return EmbeddableChunk(
        point_id=point_id,
        source_type=source_type,
        text_to_embed="text",
        content_hash="h",
        payload={"source_type": source_type, "content_hash": "h", "text": "text"},
    )


def _store_with(points: list[EmbeddableChunk]) -> QdrantStore:
    store = _store()
    store.ensure_collection(dense_size=4)
    store.upsert(
        points,
        [[0.1, 0.2, 0.3, 0.4]] * len(points),
        [SparseVector(indices=[0], values=[1.0])] * len(points),
    )
    return store


RULE_A = "00000000-0000-0000-0000-00000000000a"
RULE_B = "00000000-0000-0000-0000-00000000000b"
CARD_C = "00000000-0000-0000-0000-00000000000c"


def test_point_ids_lists_only_the_given_source_type():
    store = _store_with([_point(RULE_A, "rule"), _point(RULE_B, "rule"), _point(CARD_C, "oracle")])
    assert store.point_ids("rule") == {RULE_A, RULE_B}
    assert store.point_ids("oracle") == {CARD_C}


def test_point_ids_pages_through_the_whole_collection():
    ids = [f"00000000-0000-0000-0000-{i:012d}" for i in range(7)]
    store = _store_with([_point(i, "rule") for i in ids])
    assert store.point_ids("rule", page_size=2) == set(ids)


def test_delete_removes_exactly_the_given_points():
    store = _store_with([_point(RULE_A, "rule"), _point(RULE_B, "rule"), _point(CARD_C, "oracle")])
    store.delete([RULE_A])
    assert store.point_ids("rule") == {RULE_B}
    assert store.point_ids("oracle") == {CARD_C}


def test_delete_with_no_ids_is_a_no_op():
    store = _store_with([_point(RULE_A, "rule")])
    store.delete([])
    assert store.point_ids("rule") == {RULE_A}
