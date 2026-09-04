from conftest import memory_engine

from mtg_api.history import list_history, save_history


def test_save_and_list_round_trips_a_row():
    engine = memory_engine()
    save_history(
        engine,
        query="how does trample work",
        answer="Trample lets excess damage carry over.",
        results=[
            {
                "source": "rule",
                "title": "702.19",
                "text": "...",
                "score": 0.9,
                "match_type": "vector_hit",
                "oracle_id": None,
            }
        ],
        model="openai/gpt-oss-120b",
        error=None,
    )
    rows = list_history(engine)
    assert len(rows) == 1
    assert rows[0]["query"] == "how does trample work"
    assert rows[0]["answer"] == "Trample lets excess damage carry over."
    assert rows[0]["results"][0]["title"] == "702.19"
    assert rows[0]["model"] == "openai/gpt-oss-120b"
    assert rows[0]["error"] is None


def test_save_history_persists_null_answer_and_error():
    engine = memory_engine()
    save_history(
        engine,
        query="what does bolt do",
        answer=None,
        results=[],
        model="openai/gpt-oss-120b",
        error="rate limited",
    )
    rows = list_history(engine)
    assert rows[0]["answer"] is None
    assert rows[0]["error"] == "rate limited"


def test_list_history_orders_newest_first():
    engine = memory_engine()
    save_history(engine, query="first", answer="a1", results=[], model="m", error=None)
    save_history(engine, query="second", answer="a2", results=[], model="m", error=None)
    rows = list_history(engine)
    assert [r["query"] for r in rows] == ["second", "first"]


def test_list_history_respects_limit_and_offset():
    engine = memory_engine()
    for i in range(3):
        save_history(engine, query=f"q{i}", answer=None, results=[], model="m", error=None)
    rows = list_history(engine, limit=1, offset=1)
    assert len(rows) == 1
    assert rows[0]["query"] == "q1"


def test_list_history_empty_table_returns_empty_list():
    assert list_history(memory_engine()) == []
