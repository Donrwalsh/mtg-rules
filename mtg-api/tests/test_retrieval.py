from mtg_api.retrieval import _normalize, hybrid_search
from mtg_api.sparse_embedder import SparseVector


class _FakeHit:
    def __init__(self, id, score, payload):
        self.id = id
        self.score = score
        self.payload = payload


class _FakeQueryResult:
    def __init__(self, points):
        self.points = points


class _FakeClient:
    def __init__(self, dense_points, sparse_points):
        self._dense_points = dense_points
        self._sparse_points = sparse_points
        self.calls: list[tuple[str, int]] = []

    def query_points(self, collection_name, using, query, limit, with_payload):
        self.calls.append((using, limit))
        points = self._dense_points if using == "dense" else self._sparse_points
        return _FakeQueryResult(points[:limit])


def test_normalize_empty_returns_empty():
    assert _normalize({}) == {}


def test_normalize_single_distinct_value_maps_to_one():
    assert _normalize({"a": 5.0}) == {"a": 1.0}
    assert _normalize({"a": 3.0, "b": 3.0}) == {"a": 1.0, "b": 1.0}


def test_normalize_spread_maps_to_zero_one_range():
    result = _normalize({"a": 0.0, "b": 5.0, "c": 10.0})
    assert result == {"a": 0.0, "b": 0.5, "c": 1.0}


def test_hybrid_search_combines_dense_and_sparse_with_weights():
    dense_points = [_FakeHit("p1", 1.0, {"text": "dense hit"}), _FakeHit("p2", 0.0, {"text": "other"})]
    sparse_points = [_FakeHit("p2", 1.0, {"text": "other"}), _FakeHit("p1", 0.0, {"text": "dense hit"})]
    client = _FakeClient(dense_points, sparse_points)

    results = hybrid_search(
        client,
        "test_collection",
        [0.1] * 4,
        SparseVector(indices=[0], values=[1.0]),
        per_branch_limit=10,
        dense_weight=0.5,
        sparse_weight=0.5,
        score_threshold=0.0,
        top_k=10,
    )

    result_ids = [r[0] for r in results]
    assert set(result_ids) == {"p1", "p2"}
    for _point_id, score, _payload in results:
        assert score == 0.5


def test_hybrid_search_respects_score_threshold():
    dense_points = [_FakeHit("p1", 1.0, {"text": "a"}), _FakeHit("p2", 0.0, {"text": "b"})]
    client = _FakeClient(dense_points, [])

    results = hybrid_search(
        client,
        "test_collection",
        [0.1] * 4,
        SparseVector(indices=[0], values=[1.0]),
        per_branch_limit=10,
        dense_weight=1.0,
        sparse_weight=0.0,
        score_threshold=0.5,
        top_k=10,
    )

    result_ids = [r[0] for r in results]
    assert result_ids == ["p1"]


def test_hybrid_search_truncates_to_top_k():
    dense_points = [_FakeHit(f"p{i}", float(i), {"text": str(i)}) for i in range(5)]
    client = _FakeClient(dense_points, [])

    results = hybrid_search(
        client,
        "test_collection",
        [0.1] * 4,
        SparseVector(indices=[0], values=[1.0]),
        per_branch_limit=10,
        dense_weight=1.0,
        sparse_weight=0.0,
        score_threshold=0.0,
        top_k=2,
    )

    assert len(results) == 2
    assert results[0][1] >= results[1][1]


def test_hybrid_search_uses_per_branch_limit():
    dense_points = [_FakeHit(f"p{i}", float(i), {"text": str(i)}) for i in range(5)]
    client = _FakeClient(dense_points, [])

    hybrid_search(
        client,
        "test_collection",
        [0.1] * 4,
        SparseVector(indices=[0], values=[1.0]),
        per_branch_limit=2,
        dense_weight=1.0,
        sparse_weight=0.0,
        score_threshold=0.0,
        top_k=10,
    )

    assert client.calls == [("dense", 2), ("sparse", 2)]
