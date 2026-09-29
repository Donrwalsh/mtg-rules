from conftest import memory_engine
from fastapi.testclient import TestClient

from mtg_api import main
from mtg_api.card_matcher import CardMatcher
from mtg_api.embedder import Embedder
from mtg_api.history import list_history
from mtg_api.keyword_matcher import KeywordMatcher
from mtg_api.llm import Generation
from mtg_api.main import (
    app,
    get_answerer,
    get_card_matcher,
    get_db_engine,
    get_dense_embedder,
    get_keyword_matcher,
    get_qdrant_client,
    get_rules_index,
    get_sparse_embedder,
)
from mtg_api.rules_index import RulesIndex
from mtg_api.sparse_embedder import SparseEmbedder


class _FakeDenseModel:
    def encode(self, texts, batch_size, show_progress_bar=False):
        return [[0.1, 0.2, 0.3, 0.4] for _ in texts]

    def get_sentence_embedding_dimension(self):
        return 4


class _FakeSparseEmbedding:
    indices = (0,)
    values = (1.0,)


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

    def query_points(self, collection_name, using, query, limit, with_payload, query_filter=None):
        points = self._dense_points if using == "dense" else self._sparse_points
        if query_filter is not None:
            (condition,) = query_filter.must
            points = [p for p in points if p.payload.get(condition.key) == condition.match.value]
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
        return Generation(
            text=self._answer, input_tokens=1000, output_tokens=100, thinking_tokens=200
        )


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
    rules=None,
):
    app.dependency_overrides[get_card_matcher] = lambda: CardMatcher(cards or [])
    app.dependency_overrides[get_keyword_matcher] = lambda: KeywordMatcher(rules or [])
    app.dependency_overrides[get_rules_index] = lambda: RulesIndex(rules or [])
    app.dependency_overrides[get_dense_embedder] = lambda: Embedder(_FakeDenseModel())
    app.dependency_overrides[get_sparse_embedder] = lambda: SparseEmbedder(_FakeSparseModel())
    app.dependency_overrides[get_qdrant_client] = lambda: _FakeQdrantClient(
        dense_points, sparse_points, scroll_points
    )
    app.dependency_overrides[get_answerer] = lambda: answerer or _FakeAnswerer()
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
    cards = [
        {"oracle_id": "oid-1", "name": "Craterhoof Behemoth", "oracle_text": "Trample. When..."}
    ]
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


HEXPROOF_RULES = [
    {"rule_id": "702.11", "text": "Hexproof", "parent_id": "702"},
    {"rule_id": "702.11a", "text": "Hexproof is a static ability.", "parent_id": "702.11"},
    {"rule_id": "702.11b", "text": "Can't be targeted by opponents.", "parent_id": "702.11"},
]


def test_query_includes_keyword_rules_when_keyword_named():
    _override(rules=HEXPROOF_RULES)
    try:
        resp = TestClient(app).post(
            "/api/v1/query", json={"query": "can I target my own hexproof creature"}
        )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    keyword_hits = [r for r in resp.json()["results"] if r["match_type"] == "keyword_rule_match"]
    assert [r["title"] for r in keyword_hits] == ["702.11", "702.11a", "702.11b"]
    assert all(r["source"] == "rule" and r["score"] == 1.0 for r in keyword_hits)
    assert keyword_hits[1]["text"] == "Hexproof is a static ability."


def test_query_orders_keyword_rules_after_card_rulings_and_before_vector_hits():
    cards = [{"oracle_id": "oid-1", "name": "Lightning Bolt", "oracle_text": "Deals 3 damage."}]
    scroll_points = [
        _FakeHit(
            "r1",
            None,
            {
                "source_type": "ruling",
                "card_name": "Lightning Bolt",
                "oracle_id": "oid-1",
                "text": "Bolt ruling.",
            },
        )
    ]
    dense_points = [
        _FakeHit("p1", 0.9, {"source_type": "rule", "rule_id": "115.1", "text": "Targets."})
    ]
    _override(
        cards=cards, rules=HEXPROOF_RULES, dense_points=dense_points, scroll_points=scroll_points
    )
    try:
        resp = TestClient(app).post(
            "/api/v1/query", json={"query": "can Lightning Bolt target my hexproof creature"}
        )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    match_types = [r["match_type"] for r in resp.json()["results"]]
    assert match_types == [
        "card_name_match",
        "card_ruling_match",
        "keyword_rule_match",
        "keyword_rule_match",
        "keyword_rule_match",
        "vector_hit",
    ]


def test_query_dedupes_vector_hit_matching_a_keyword_rule():
    dense_points = [
        _FakeHit(
            "p1",
            0.9,
            {
                "source_type": "rule",
                "rule_id": "702.11b",
                "text": "Can't be targeted by opponents.",
            },
        ),
        _FakeHit("p2", 0.8, {"source_type": "rule", "rule_id": "115.1", "text": "Targets."}),
    ]
    _override(rules=HEXPROOF_RULES, dense_points=dense_points)
    try:
        resp = TestClient(app).post("/api/v1/query", json={"query": "how does hexproof work"})
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    titles = [r["title"] for r in resp.json()["results"]]
    assert titles == ["702.11", "702.11a", "702.11b", "115.1"]


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


def test_query_returns_null_answer_when_generation_fails():
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


BOLT_URI = "https://scryfall.com/card/lea/161/lightning-bolt"


def test_query_results_carry_source_metadata():
    cards = [
        {
            "oracle_id": "oid-1",
            "name": "Lightning Bolt",
            "oracle_text": "Deals 3 damage.",
            "scryfall_uri": BOLT_URI,
        }
    ]
    scroll_points = [
        _FakeHit(
            "r1",
            None,
            {
                "source_type": "ruling",
                "card_name": "Lightning Bolt",
                "oracle_id": "oid-1",
                "text": "Bolt ruling.",
                "published_at": "2020-01-01",
                "scryfall_uri": BOLT_URI,
            },
        )
    ]
    dense_points = [
        _FakeHit("p1", 0.9, {"source_type": "rule", "rule_id": "115.1", "text": "Targets."})
    ]
    _override(
        cards=cards, rules=HEXPROOF_RULES, dense_points=dense_points, scroll_points=scroll_points
    )
    try:
        resp = TestClient(app).post(
            "/api/v1/query", json={"query": "can Lightning Bolt target my hexproof creature"}
        )
    finally:
        app.dependency_overrides.clear()

    card, ruling, keyword, _, _, vector = resp.json()["results"]
    assert (card["card_name"], card["scryfall_uri"]) == ("Lightning Bolt", BOLT_URI)
    assert (ruling["card_name"], ruling["published_at"], ruling["scryfall_uri"]) == (
        "Lightning Bolt",
        "2020-01-01",
        BOLT_URI,
    )
    assert keyword["rule_id"] == "702.11"
    assert vector["rule_id"] == "115.1"


def test_query_returns_validated_citations_and_persists_them():
    cards = [
        {
            "oracle_id": "oid-1",
            "name": "Lightning Bolt",
            "oracle_text": "Deals 3 damage.",
            "scryfall_uri": BOLT_URI,
        }
    ]
    dense_points = [
        _FakeHit("p1", 0.9, {"source_type": "rule", "rule_id": "115.1", "text": "Targets."})
    ]
    engine = memory_engine()
    # Context numbering: 1 Bolt, 2 702.11, 3 702.11a, 4 702.11b, 5 115.1.
    answer = "Bolt deals 3 [1]. Hexproof only stops opponents [4][99], see 702.11b and 999.9z."
    _override(
        cards=cards,
        rules=HEXPROOF_RULES,
        dense_points=dense_points,
        answerer=_FakeAnswerer(answer=answer),
        engine=engine,
    )
    try:
        resp = TestClient(app).post(
            "/api/v1/query", json={"query": "can Lightning Bolt target my hexproof creature"}
        )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    assert body["answer"] == (
        "Bolt deals 3 [1]. Hexproof only stops opponents [4], see 702.11b and 999.9z."
    )
    assert [c["number"] for c in body["citations"]] == [1, 4]
    bolt, rule = body["citations"]
    assert (bolt["source_type"], bolt["title"], bolt["url"]) == (
        "card",
        "Card — Lightning Bolt",
        BOLT_URI,
    )
    assert (rule["source_type"], rule["rule_id"], rule["url"], rule["text"]) == (
        "rule",
        "702.11b",
        "/rules/702.11b",
        "Can't be targeted by opponents.",
    )
    assert [r["cited"] for r in body["results"]] == [True, False, False, True, False]
    assert body["citation_stats"] == {"cited_count": 2, "invalid_count": 1, "uncited_answer": False}
    assert body["rule_references"] == ["702.11b"]

    row = list_history(engine)[0]
    assert row["answer"] == body["answer"]
    assert [c["number"] for c in row["citations"]] == [1, 4]
    assert row["citation_stats"] == body["citation_stats"]
    assert row["rule_references"] == ["702.11b"]
    assert [r["cited"] for r in row["results"]] == [True, False, False, True, False]


def test_query_flags_an_answer_with_no_citations():
    _override(answerer=_FakeAnswerer(answer="The context doesn't cover that."))
    try:
        body = TestClient(app).post("/api/v1/query", json={"query": "best standard deck"}).json()
    finally:
        app.dependency_overrides.clear()
    assert body["citations"] == []
    assert body["citation_stats"] == {"cited_count": 0, "invalid_count": 0, "uncited_answer": True}


def test_query_failed_generation_has_empty_citations():
    engine = memory_engine()
    _override(answerer=_FakeAnswerer(raises=RuntimeError("down")), engine=engine)
    try:
        body = TestClient(app).post("/api/v1/query", json={"query": "trample"}).json()
    finally:
        app.dependency_overrides.clear()
    assert body["answer"] is None
    assert body["citations"] == []
    assert body["rule_references"] == []
    assert body["citation_stats"] == {"cited_count": 0, "invalid_count": 0, "uncited_answer": False}
    assert list_history(engine)[0]["citations"] == []


def _ruling_hits_and_one_rule():
    # The rule scores lowest overall, as rules do among thousands of rulings.
    return [
        _FakeHit("r1", 0.9, {"source_type": "ruling", "card_name": "A", "text": "Ruling A."}),
        _FakeHit("r2", 0.8, {"source_type": "ruling", "card_name": "B", "text": "Ruling B."}),
        _FakeHit(
            "p1", 0.1, {"source_type": "rule", "rule_id": "704.5b", "text": "Draw from empty."}
        ),
    ]


def _post_query(query):
    try:
        return TestClient(app).post("/api/v1/query", json={"query": query}).json()
    finally:
        app.dependency_overrides.clear()


def test_rules_search_is_off_at_zero(monkeypatch):
    monkeypatch.setattr(main.settings, "hybrid_top_k", 2)
    monkeypatch.setattr(main.settings, "rules_top_k", 0)
    _override(dense_points=_ruling_hits_and_one_rule())
    body = _post_query("draw from an empty library")
    assert [r["match_type"] for r in body["results"]] == ["vector_hit", "vector_hit"]


def test_rules_search_adds_rules_the_main_search_missed(monkeypatch):
    monkeypatch.setattr(main.settings, "hybrid_top_k", 2)
    monkeypatch.setattr(main.settings, "rules_top_k", 1)
    _override(dense_points=_ruling_hits_and_one_rule())
    body = _post_query("draw from an empty library")
    rule_hits = [r for r in body["results"] if r["match_type"] == "rule_vector_hit"]
    assert [r["rule_id"] for r in rule_hits] == ["704.5b"]
    assert rule_hits[0]["source_type"] == "rule"


def test_rules_search_skips_rules_already_in_the_results(monkeypatch):
    monkeypatch.setattr(main.settings, "rules_top_k", 1)
    _override(dense_points=_ruling_hits_and_one_rule())
    body = _post_query("draw from an empty library")
    rule_ids = [r["rule_id"] for r in body["results"] if r["rule_id"]]
    assert rule_ids == ["704.5b"]
    assert body["results"][-1]["match_type"] == "vector_hit"
