from fastapi.testclient import TestClient

from mtg_api.card_matcher import CardMatcher
from mtg_api.embedder import Embedder
from mtg_api.main import app, get_card_matcher, get_dense_embedder, get_qdrant_client, get_sparse_embedder
from mtg_api.sparse_embedder import SparseEmbedder


class _FakeDenseModel:
    def encode(self, texts, batch_size, show_progress_bar=False):
        return [[0.1, 0.2, 0.3, 0.4] for _ in texts]

    def get_sentence_embedding_dimension(self):
        return 4


class _FakeSparseEmbedding:
    indices = [0]
    values = [1.0]


class _FakeSparseModel:
    def embed(self, texts):
        return [_FakeSparseEmbedding() for _ in texts]


class _FakeHit:
    def __init__(self, id, score, payload):
        self.id = id
        self.score = score
        self.payload = payload


class _FakeQueryResult:
    def __init__(self, points):
        self.points = points


class _FakeQdrantClient:
    def __init__(self, dense_points=None, sparse_points=None):
        self._dense_points = dense_points or []
        self._sparse_points = sparse_points or []

    def query_points(self, collection_name, using, query, limit, with_payload):
        points = self._dense_points if using == "dense" else self._sparse_points
        return _FakeQueryResult(points[:limit])


def _override(cards=None, dense_points=None, sparse_points=None):
    app.dependency_overrides[get_card_matcher] = lambda: CardMatcher(cards or [])
    app.dependency_overrides[get_dense_embedder] = lambda: Embedder(_FakeDenseModel())
    app.dependency_overrides[get_sparse_embedder] = lambda: SparseEmbedder(_FakeSparseModel())
    app.dependency_overrides[get_qdrant_client] = lambda: _FakeQdrantClient(dense_points, sparse_points)


def test_query_returns_card_match_when_name_detected():
    cards = [{"oracle_id": "oid-1", "name": "Counterspell", "oracle_text": "Counter target spell."}]
    _override(cards=cards)
    try:
        resp = TestClient(app).post("/api/v1/query", json={"query": "how does Counterspell work"})
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    card_hits = [r for r in body["results"] if r["match_type"] == "card_name_match"]
    assert len(card_hits) == 1
    assert card_hits[0]["title"] == "Counterspell"
    assert card_hits[0]["oracle_id"] == "oid-1"
    assert card_hits[0]["score"] == 1.0


def test_query_returns_vector_hit_when_no_card_named():
    dense_points = [
        _FakeHit("p1", 1.0, {"source_type": "rule", "rule_id": "702.19", "text": "Trample text"})
    ]
    _override(dense_points=dense_points)
    try:
        resp = TestClient(app).post("/api/v1/query", json={"query": "how does trample work"})
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    vector_hits = [r for r in body["results"] if r["match_type"] == "vector_hit"]
    assert len(vector_hits) == 1
    assert vector_hits[0]["title"] == "702.19"
    assert vector_hits[0]["source"] == "rule"


def test_query_dedupes_vector_hit_matching_a_card_match():
    cards = [{"oracle_id": "oid-1", "name": "Counterspell", "oracle_text": "Counter target spell."}]
    dense_points = [
        _FakeHit(
            "p1",
            1.0,
            {
                "source_type": "oracle",
                "card_name": "Counterspell",
                "oracle_id": "oid-1",
                "text": "Counter target spell.",
            },
        )
    ]
    _override(cards=cards, dense_points=dense_points)
    try:
        resp = TestClient(app).post("/api/v1/query", json={"query": "Counterspell rulings"})
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["results"]) == 1
    assert body["results"][0]["match_type"] == "card_name_match"


def test_query_rejects_missing_query_field():
    resp = TestClient(app).post("/api/v1/query", json={})
    assert resp.status_code == 422


def test_cors_header_present_for_configured_origin():
    _override()
    try:
        resp = TestClient(app).post(
            "/api/v1/query",
            json={"query": "trample"},
            headers={"Origin": "http://localhost:3000"},
        )
    finally:
        app.dependency_overrides.clear()
    assert resp.headers["access-control-allow-origin"] == "http://localhost:3000"
