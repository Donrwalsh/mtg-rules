from conftest import FailingEngine, FakeAnswerer, FakeHit, memory_engine, override
from fastapi.testclient import TestClient

from mtg_api import main
from mtg_api.history import list_history
from mtg_api.main import (
    app,
)


def test_query_returns_card_match_when_name_detected():
    cards = [{"oracle_id": "oid-1", "name": "Counterspell", "oracle_text": "Counter target spell."}]
    override(cards=cards)
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
        FakeHit("p1", 1.0, {"source_type": "rule", "rule_id": "702.19", "text": "Trample text"})
    ]
    override(dense_points=dense_points)
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
        FakeHit(
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
        FakeHit(
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
    override(cards=cards, dense_points=dense_points, scroll_points=scroll_points)
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
        FakeHit(
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
    override(cards=cards, dense_points=dense_points)
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
    override(rules=HEXPROOF_RULES)
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
        FakeHit(
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
        FakeHit("p1", 0.9, {"source_type": "rule", "rule_id": "115.1", "text": "Targets."})
    ]
    override(
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
        FakeHit(
            "p1",
            0.9,
            {
                "source_type": "rule",
                "rule_id": "702.11b",
                "text": "Can't be targeted by opponents.",
            },
        ),
        FakeHit("p2", 0.8, {"source_type": "rule", "rule_id": "115.1", "text": "Targets."}),
    ]
    override(rules=HEXPROOF_RULES, dense_points=dense_points)
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
    override()
    try:
        resp = TestClient(app).post("/api/v1/query", json={})
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 422


def test_cors_header_present_for_configured_origin():
    override()
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
    override(answerer=FakeAnswerer(answer="Trample carries excess damage over."))
    try:
        resp = TestClient(app).post("/api/v1/query", json={"query": "how does trample work"})
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 200
    assert resp.json()["answer"] == "Trample carries excess damage over."


def test_query_returns_null_answer_when_generation_fails():
    override(answerer=FakeAnswerer(raises=RuntimeError("rate limited")))
    try:
        resp = TestClient(app).post("/api/v1/query", json={"query": "how does trample work"})
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 200
    assert resp.json()["answer"] is None
    assert resp.json()["results"] == []


def test_query_succeeds_even_when_history_write_fails():
    override(answerer=FakeAnswerer(answer="An answer."), engine=FailingEngine())
    try:
        resp = TestClient(app).post("/api/v1/query", json={"query": "how does trample work"})
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 200
    assert resp.json()["answer"] == "An answer."


def test_query_persists_a_history_row():
    engine = memory_engine()
    override(answerer=FakeAnswerer(answer="An answer."), engine=engine)
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
        FakeHit(
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
        FakeHit("p1", 0.9, {"source_type": "rule", "rule_id": "115.1", "text": "Targets."})
    ]
    override(
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
        FakeHit("p1", 0.9, {"source_type": "rule", "rule_id": "115.1", "text": "Targets."})
    ]
    engine = memory_engine()
    # Context numbering: 1 Bolt, 2 702.11, 3 702.11a, 4 702.11b, 5 115.1.
    answer = "Bolt deals 3 [1]. Hexproof only stops opponents [4][99], see 702.11b and 999.9z."
    override(
        cards=cards,
        rules=HEXPROOF_RULES,
        dense_points=dense_points,
        answerer=FakeAnswerer(answer=answer),
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
    override(answerer=FakeAnswerer(answer="The context doesn't cover that."))
    try:
        body = TestClient(app).post("/api/v1/query", json={"query": "best standard deck"}).json()
    finally:
        app.dependency_overrides.clear()
    assert body["citations"] == []
    assert body["citation_stats"] == {"cited_count": 0, "invalid_count": 0, "uncited_answer": True}


def test_query_failed_generation_has_empty_citations():
    engine = memory_engine()
    override(answerer=FakeAnswerer(raises=RuntimeError("down")), engine=engine)
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
        FakeHit("r1", 0.9, {"source_type": "ruling", "card_name": "A", "text": "Ruling A."}),
        FakeHit("r2", 0.8, {"source_type": "ruling", "card_name": "B", "text": "Ruling B."}),
        FakeHit(
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
    override(dense_points=_ruling_hits_and_one_rule())
    body = _post_query("draw from an empty library")
    assert [r["match_type"] for r in body["results"]] == ["vector_hit", "vector_hit"]


def test_rules_search_adds_rules_the_main_search_missed(monkeypatch):
    monkeypatch.setattr(main.settings, "hybrid_top_k", 2)
    monkeypatch.setattr(main.settings, "rules_top_k", 1)
    override(dense_points=_ruling_hits_and_one_rule())
    body = _post_query("draw from an empty library")
    rule_hits = [r for r in body["results"] if r["match_type"] == "rule_vector_hit"]
    assert [r["rule_id"] for r in rule_hits] == ["704.5b"]
    assert rule_hits[0]["source_type"] == "rule"


def test_rules_search_skips_rules_already_in_the_results(monkeypatch):
    monkeypatch.setattr(main.settings, "rules_top_k", 1)
    override(dense_points=_ruling_hits_and_one_rule())
    body = _post_query("draw from an empty library")
    rule_ids = [r["rule_id"] for r in body["results"] if r["rule_id"]]
    assert rule_ids == ["704.5b"]
    assert body["results"][-1]["match_type"] == "vector_hit"


def test_query_results_and_citations_carry_card_details_and_headings():
    cards = [
        {
            "oracle_id": "oid-1",
            "name": "Lightning Bolt",
            "oracle_text": "Deals 3 damage.",
            "type_line": "Instant",
            "mana_cost": "{R}",
            "image_uri": "https://cards.scryfall.io/normal/front/b/o/bolt.jpg?1",
        }
    ]
    rules = [{"rule_id": "702", "text": "Keyword Abilities", "parent_id": None}, *HEXPROOF_RULES]
    override(cards=cards, rules=rules, answerer=FakeAnswerer("Bolt [1] vs hexproof [2]."))
    try:
        body = (
            TestClient(app)
            .post("/api/v1/query", json={"query": "can Lightning Bolt target my hexproof creature"})
            .json()
        )
    finally:
        app.dependency_overrides.clear()

    card, keyword = body["results"][0], body["results"][1]
    assert card["card"]["type_line"] == "Instant"
    assert card["card"]["image_small"] == "https://cards.scryfall.io/small/front/b/o/bolt.jpg?1"
    assert keyword["rule_id"] == "702.11"
    assert keyword["heading"] == "Hexproof"
    by_number = {c["number"]: c for c in body["citations"]}
    assert by_number[1]["card"]["name"] == "Lightning Bolt"
    assert by_number[2]["heading"] == "Hexproof"


def test_enrichment_does_not_change_the_llm_context():
    seen = []

    class _Recording(FakeAnswerer):
        def generate(self, query, context):
            seen.append(context)
            return super().generate(query, context)

    cards = [
        {
            "oracle_id": "oid-1",
            "name": "Lightning Bolt",
            "oracle_text": "Deals 3 damage.",
            "image_uri": "https://cards.scryfall.io/normal/front/b/o/bolt.jpg?1",
        }
    ]
    override(cards=cards, rules=HEXPROOF_RULES, answerer=_Recording())
    try:
        TestClient(app).post("/api/v1/query", json={"query": "Lightning Bolt vs hexproof"})
    finally:
        app.dependency_overrides.clear()
    assert "scryfall.io" not in seen[0]
    assert "Instant" not in seen[0]
    assert "[2] Rule 702.11: Hexproof" in seen[0]
