from fastapi.testclient import TestClient

from mtg_api.main import app, get_rules_index
from mtg_api.rules_index import RulesIndex

RULES = [
    {"rule_id": "702", "text": "Keyword Abilities", "parent_id": None},
    {"rule_id": "702.11", "text": "Hexproof", "parent_id": "702"},
    {"rule_id": "702.11a", "text": "Hexproof is a static ability.", "parent_id": "702.11"},
    {"rule_id": "702.11b", "text": "Can't be targeted by opponents.", "parent_id": "702.11"},
]


def _get(path: str):
    app.dependency_overrides[get_rules_index] = lambda: RulesIndex(RULES, "2026-08-25")
    try:
        return TestClient(app).get(path)
    finally:
        app.dependency_overrides.clear()


def test_rule_found_with_ancestors_and_ingest_date():
    resp = _get("/api/v1/rules/702.11b")
    assert resp.status_code == 200
    assert resp.json() == {
        "rule_id": "702.11b",
        "text": "Can't be targeted by opponents.",
        "ancestors": [
            {"rule_id": "702", "text": "Keyword Abilities"},
            {"rule_id": "702.11", "text": "Hexproof"},
        ],
        "subrules": [],
        "rules_ingested_at": "2026-08-25",
    }


def test_rule_includes_direct_subrules_in_order():
    body = _get("/api/v1/rules/702.11").json()
    assert body["subrules"] == [
        {"rule_id": "702.11a", "text": "Hexproof is a static ability."},
        {"rule_id": "702.11b", "text": "Can't be targeted by opponents."},
    ]
    assert [a["rule_id"] for a in body["ancestors"]] == ["702"]


def test_top_level_rule_lists_only_direct_children():
    body = _get("/api/v1/rules/702").json()
    assert [s["rule_id"] for s in body["subrules"]] == ["702.11"]
    assert body["ancestors"] == []


def test_rule_id_is_normalized():
    body = _get("/api/v1/rules/702.11B.").json()
    assert body["rule_id"] == "702.11b"


def test_unknown_rule_is_404():
    resp = _get("/api/v1/rules/702.99z")
    assert resp.status_code == 404
    assert resp.json() == {"detail": "Rule not found"}
