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
