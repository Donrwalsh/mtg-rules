from fastapi.testclient import TestClient

from conftest import memory_engine
from mtg_api.card_matcher import CardMatcher
from mtg_api.embedder import Embedder
from mtg_api.history import list_history
from mtg_api.main import (
    app,
    get_card_matcher,
    get_db_engine,
    get_dense_embedder,
    get_groq_answerer,
    get_qdrant_client,
    get_sparse_embedder,
)
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
    def __init__(self, dense_points=None, sparse_points=None, scroll_points=None):
        self._dense_points = dense_points or []
        self._sparse_points = sparse_points or []
        self._scroll_points = scroll_points or []

    def query_points(self, collection_name, using, query, limit, with_payload):
        points = self._dense_points if using == "dense" else self._sparse_points
        return _FakeQueryResult(points[:limit])

    def scroll(self, collection_name, scroll_filter, limit, with_payload):
        return self._scroll_points[:limit], None


class _FakeAnswerer:
    def __init__(self, answer="A generated answer.", raises=None):
        self._answer = answer
        self._raises = raises

    def generate(self, query, context):
        if self._raises:
            raise self._raises
        return self._answer


class _FailingEngine:
    def begin(self):
        raise RuntimeError("db unreachable")

    def connect(self):
        raise RuntimeError("db unreachable")


def _override(
    cards=None,
    dense_points=None,
    sparse_points=None,
    scroll_points=None,
    answerer=None,
    engine=None,
):
    app.dependency_overrides[get_card_matcher] = lambda: CardMatcher(cards or [])
    app.dependency_overrides[get_dense_embedder] = lambda: Embedder(_FakeDenseModel())
    app.dependency_overrides[get_sparse_embedder] = lambda: SparseEmbedder(_FakeSparseModel())
    app.dependency_overrides[get_qdrant_client] = lambda: _FakeQdrantClient(
        dense_points, sparse_points, scroll_points
    )
    app.dependency_overrides[get_groq_answerer] = lambda: answerer or _FakeAnswerer()
    app.dependency_overrides[get_db_engine] = lambda: engine or memory_engine()


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


def test_query_includes_matched_cards_own_rulings():
    cards = [{"oracle_id": "oid-1", "name": "Craterhoof Behemoth", "oracle_text": "Trample. When..."}]
    # An unrelated card's ruling that a naive semantic search might surface
    # instead of Craterhoof's own -- the bug this test guards against.
    dense_points = [
        _FakeHit(
            "p1",
            0.9,
            {
                "source_type": "ruling",
                "card_name": "Trench Behemoth",
                "oracle_id": "oid-2",
                "text": "An unrelated ruling.",
            },
        )
    ]
    scroll_points = [
        _FakeHit(
            "r1",
            None,
            {
                "source_type": "ruling",
                "card_name": "Craterhoof Behemoth",
                "oracle_id": "oid-1",
                "text": "Craterhoof's own ruling.",
            },
        )
    ]
    _override(cards=cards, dense_points=dense_points, scroll_points=scroll_points)
    try:
        resp = TestClient(app).post(
            "/api/v1/query", json={"query": "how does Craterhoof Behemoth work"}
        )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    card_ruling_hits = [r for r in body["results"] if r["match_type"] == "card_ruling_match"]
    assert len(card_ruling_hits) == 1
    assert card_ruling_hits[0]["oracle_id"] == "oid-1"
    assert card_ruling_hits[0]["text"] == "Craterhoof's own ruling."
    # The unrelated card's ruling still comes back too, just not miscategorized.
    vector_hits = [r for r in body["results"] if r["match_type"] == "vector_hit"]
    assert len(vector_hits) == 1
    assert vector_hits[0]["oracle_id"] == "oid-2"


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
    # FastAPI resolves Depends() sub-dependencies before request-body
    # validation runs, so this still needs the same overrides as every
    # other test here -- without them it silently falls through to the
    # real, un-cached get_dense_embedder()/get_sparse_embedder(), which
    # downloads and loads the actual models over the network.
    _override()
    try:
        resp = TestClient(app).post("/api/v1/query", json={})
    finally:
        app.dependency_overrides.clear()
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


def test_query_returns_generated_answer_on_success():
    _override(answerer=_FakeAnswerer(answer="Trample carries excess damage over."))
    try:
        resp = TestClient(app).post("/api/v1/query", json={"query": "how does trample work"})
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 200
    assert resp.json()["answer"] == "Trample carries excess damage over."


def test_query_returns_null_answer_when_groq_fails():
    _override(answerer=_FakeAnswerer(raises=RuntimeError("rate limited")))
    try:
        resp = TestClient(app).post("/api/v1/query", json={"query": "how does trample work"})
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 200
    assert resp.json()["answer"] is None
    assert resp.json()["results"] == []


def test_query_succeeds_even_when_history_write_fails():
    _override(answerer=_FakeAnswerer(answer="An answer."), engine=_FailingEngine())
    try:
        resp = TestClient(app).post("/api/v1/query", json={"query": "how does trample work"})
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 200
    assert resp.json()["answer"] == "An answer."


def test_query_persists_a_history_row():
    engine = memory_engine()
    _override(answerer=_FakeAnswerer(answer="An answer."), engine=engine)
    try:
        TestClient(app).post("/api/v1/query", json={"query": "how does trample work"})
    finally:
        app.dependency_overrides.clear()
    rows = list_history(engine)
    assert len(rows) == 1
    assert rows[0]["query"] == "how does trample work"
    assert rows[0]["answer"] == "An answer."
    assert rows[0]["error"] is None
