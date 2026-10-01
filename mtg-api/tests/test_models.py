from mtg_api.models import (
    Citation,
    QueryRequest,
    QueryResponse,
    QueryResult,
    StreamDone,
    StreamHead,
)


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


def test_query_result_source_type_mirrors_source():
    result = QueryResult(
        source="oracle", title="Shock", text="", score=1.0, match_type="vector_hit"
    )
    assert result.source_type == "oracle"
    assert result.model_dump()["source_type"] == "oracle"


def test_query_request_eval_fields_default_to_normal_behaviour():
    req = QueryRequest(query="q")
    assert req.generate is True
    assert req.overrides == {}
    assert req.source is None


def test_stream_events_partition_the_response_fields():
    head, done = set(StreamHead.model_fields), set(StreamDone.model_fields)
    assert head & done == {"results"}
    assert (head | done) - {"sources"} == set(QueryResponse.model_fields) - {"query"}


def test_head_and_done_rebuild_the_response():
    result = QueryResult(
        source="rule", title="702.19b", text="T.", score=1.0, match_type="vector_hit", cited=True
    )
    citation = Citation(number=1, source_type="rule", title="Rule 702.19b", text="T.")
    response = QueryResponse(
        query="trample",
        results=[result],
        answer="Yes [1].",
        citations=[citation],
        rule_references=["702.19b"],
        answer_complete=True,
        answers_remaining=3,
        generator="ollama:phi4:latest",
        usage={"input_tokens": 5},
    )
    head = StreamHead.of(response, [citation])
    assert head.sources == [citation]
    assert StreamDone.of(response).response("trample", head) == response


def test_wire_order_of_head_and_done():
    head = StreamHead.of(QueryResponse(query="q", results=[]), [])
    assert list(head.model_dump(mode="json")) == [
        "results",
        "degraded",
        "answers_remaining",
        "cached_at",
        "sources",
    ]
    done = StreamDone.of(QueryResponse(query="q", results=[]))
    assert list(done.model_dump(mode="json")) == [
        "results",
        "answer",
        "citations",
        "rule_references",
        "citation_stats",
        "answer_complete",
        "context_hash",
        "prompt_version",
        "generator",
        "usage",
        "generation_error",
    ]
