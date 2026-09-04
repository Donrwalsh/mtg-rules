from mtg_api.models import QueryRequest, QueryResponse, QueryResult


def test_query_request_requires_query_field():
    req = QueryRequest(query="how does trample work")
    assert req.query == "how does trample work"


def test_query_response_holds_results_list():
    result = QueryResult(
        source="rule", title="702.19", text="Trample text", score=0.9, match_type="vector_hit"
    )
    resp = QueryResponse(query="trample", results=[result])
    assert resp.results[0].source == "rule"
    assert resp.results[0].score == 0.9


def test_query_response_answer_defaults_to_none():
    resp = QueryResponse(query="trample", results=[])
    assert resp.answer is None


def test_query_response_holds_answer():
    resp = QueryResponse(query="trample", results=[], answer="Trample lets excess damage through.")
    assert resp.answer == "Trample lets excess damage through."
