import json

import httpx
import pytest

from mtg_api.llm import (
    _SYSTEM_PROMPT,
    PROMPT_VERSION,
    GeminiAnswerer,
    Generation,
    StreamAccumulator,
    StreamChunk,
    build_context,
    prompt_chars,
    source_label,
)
from mtg_api.models import QueryResult


def _result(source, title, text="t", **fields):
    return QueryResult(
        source=source, title=title, text=text, score=1.0, match_type="vector_hit", **fields
    )


def test_source_label_for_each_source_type():
    assert source_label(_result("rule", "702.11b", rule_id="702.11b")) == "Rule 702.11b"
    assert source_label(_result("rule", "702.19")) == "Rule 702.19"
    assert (
        source_label(_result("card", "Lightning Bolt", card_name="Lightning Bolt"))
        == "Card — Lightning Bolt"
    )
    # Vector hits on card text come back as source "oracle".
    assert source_label(_result("oracle", "Shock")) == "Card — Shock"
    assert (
        source_label(_result("ruling", "Homing Lightning", published_at="2018-01-19"))
        == "Ruling — Homing Lightning (2018-01-19)"
    )
    assert source_label(_result("ruling", "Homing Lightning")) == "Ruling — Homing Lightning"


def test_build_context_numbers_blocks_in_order_and_returns_mapping():
    results = [
        _result("rule", "702.11b", "Hexproof text.", rule_id="702.11b"),
        _result("card", "Lightning Bolt", "Deals 3 damage.", card_name="Lightning Bolt"),
        _result("ruling", "Homing Lightning", "A ruling.", published_at="2018-01-19"),
    ]
    context, sources = build_context(results)
    assert context == (
        "[1] Rule 702.11b: Hexproof text.\n\n"
        "[2] Card — Lightning Bolt: Deals 3 damage.\n\n"
        "[3] Ruling — Homing Lightning (2018-01-19): A ruling."
    )
    assert sources == {1: results[0], 2: results[1], 3: results[2]}
    # Same objects, not copies: citation processing flags them as cited.
    assert sources[1] is results[0]


def test_build_context_empty_list():
    assert build_context([]) == ("", {})


def test_system_prompt_requires_numbered_citations():
    assert "only numbers that appear in the context" in _SYSTEM_PROMPT
    assert "[1][3]" in _SYSTEM_PROMPT
    assert "[1, 3]" in _SYSTEM_PROMPT
    assert "with no citations" in _SYSTEM_PROMPT


def _sse(*chunks: dict) -> bytes:
    return b"".join(b"data: " + json.dumps(c).encode() + b"\r\n\r\n" for c in chunks)


_OK = {"candidates": [{"content": {"parts": [{"text": "ok"}]}, "finishReason": "STOP"}]}


def _gemini(chunks=None, *, status=200, sent=None, body=None, **kwargs) -> GeminiAnswerer:
    """A GeminiAnswerer whose HTTP calls go to an in-memory Gemini that
    streams `chunks` as SSE (or `body`, an iterator of raw bytes)."""
    chunks = [_OK] if chunks is None else chunks

    def handler(request: httpx.Request) -> httpx.Response:
        if sent is not None:
            sent.update(
                url=str(request.url), headers=request.headers, json=json.loads(request.content)
            )
        content = body if body is not None else _sse(*chunks)
        return httpx.Response(
            status, content=content, headers={"content-type": "text/event-stream"}
        )

    api_key = kwargs.pop("api_key", "k")
    model = kwargs.pop("model", "m")
    return GeminiAnswerer(api_key, model, transport=httpx.MockTransport(handler), **kwargs)


def test_gemini_answerer_posts_stream_generate_content():
    sent = {}
    answer = _gemini(
        [{"candidates": [{"content": {"parts": [{"text": "Trample carries over [1]."}]}}]}],
        sent=sent,
        api_key="secret",
        model="gemini-3.5-flash",
        base_url="https://generativelanguage.googleapis.com/",
    ).generate("q", "ctx")
    assert answer.text == "Trample carries over [1]."
    assert sent["url"] == (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "gemini-3.5-flash:streamGenerateContent?alt=sse"
    )
    assert sent["headers"]["x-goog-api-key"] == "secret"
    assert sent["json"]["systemInstruction"] == {"parts": [{"text": _SYSTEM_PROMPT}]}
    assert sent["json"]["contents"][0]["role"] == "user"
    assert "ctx" in sent["json"]["contents"][0]["parts"][0]["text"]
    assert "generationConfig" not in sent["json"]


def test_gemini_answerer_sends_temperature_and_max_tokens():
    sent = {}
    _gemini(sent=sent, temperature=0.0, max_tokens=256).generate("q", "ctx")
    assert sent["json"]["generationConfig"] == {"temperature": 0.0, "maxOutputTokens": 256}


def test_gemini_answerer_sends_thinking_level():
    sent = {}
    _gemini(sent=sent, max_tokens=2048, thinking_level="low").generate("q", "ctx")
    assert sent["json"]["generationConfig"] == {
        "maxOutputTokens": 2048,
        "thinkingConfig": {"thinkingLevel": "low"},
    }


def test_stream_yields_text_per_chunk_and_skips_thought_parts():
    chunks = [
        {"candidates": [{"content": {"parts": [{"text": "Let me think", "thought": True}]}}]},
        {"candidates": [{"content": {"parts": [{"text": "Yes "}]}}]},
        {"candidates": [{"content": {"parts": [{"text": "[1]."}]}, "finishReason": "STOP"}]},
    ]
    got = list(_gemini(chunks).stream("q", "ctx"))
    assert [c.text for c in got] == ["", "Yes ", "[1]."]
    assert got[-1].finish_reason == "STOP"


def test_generate_joins_the_stream_and_takes_the_last_usage():
    chunks = [
        {
            "candidates": [{"content": {"parts": [{"text": "o"}]}}],
            "usageMetadata": {"promptTokenCount": 1200},
        },
        {
            "candidates": [{"content": {"parts": [{"text": "k"}]}, "finishReason": "STOP"}],
            "usageMetadata": {
                "promptTokenCount": 1200,
                "candidatesTokenCount": 150,
                "thoughtsTokenCount": 300,
            },
        },
    ]
    result = _gemini(chunks).generate("q", "ctx")
    assert result == Generation(
        text="ok", input_tokens=1200, output_tokens=150, thinking_tokens=300, finish_reason="STOP"
    )
    assert result.usage() == {"input_tokens": 1200, "output_tokens": 150, "thinking_tokens": 300}


def test_gemini_answerer_usage_defaults_to_zero():
    result = _gemini().generate("q", "ctx")  # no usageMetadata
    assert (result.input_tokens, result.output_tokens, result.thinking_tokens) == (0, 0, 0)


def test_gemini_answerer_parses_finish_reason():
    chunks = [
        {"candidates": [{"content": {"parts": [{"text": "cut"}]}, "finishReason": "MAX_TOKENS"}]}
    ]
    assert _gemini(chunks).generate("q", "ctx").finish_reason == "MAX_TOKENS"


def test_gemini_answerer_finish_reason_defaults_to_none_when_absent():
    chunks = [{"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}]
    assert _gemini(chunks).generate("q", "ctx").finish_reason is None


def test_gemini_answerer_raises_when_blocked():
    with pytest.raises(RuntimeError, match="SAFETY"):
        _gemini([{"promptFeedback": {"blockReason": "SAFETY"}}]).generate("q", "ctx")


def test_gemini_answerer_raises_on_http_error():
    with pytest.raises(httpx.HTTPStatusError):
        _gemini(status=429).generate("q", "ctx")


def test_stream_enforces_the_total_deadline():
    import time

    def slow_body():
        yield _sse({"candidates": [{"content": {"parts": [{"text": "a"}]}}]})
        time.sleep(0.2)
        yield _sse({"candidates": [{"content": {"parts": [{"text": "b"}]}}]})

    stream = _gemini(body=slow_body(), timeout=0.1).stream("q", "ctx")
    assert next(stream).text == "a"
    with pytest.raises(TimeoutError, match="0.1s"):
        next(stream)


def test_chunk_timeout_is_the_read_timeout():
    answerer = GeminiAnswerer("k", "m", chunk_timeout=30.0)
    with answerer._client() as client:
        assert client.timeout.read == 30.0
        assert client.timeout.connect == 10.0


def test_accumulator_keeps_the_last_counts_and_the_finish_reason():
    acc = StreamAccumulator()
    assert not acc.received
    acc.add(StreamChunk(text="a", input_tokens=10))
    acc.add(StreamChunk(text="b", output_tokens=2))
    acc.add(StreamChunk(finish_reason="STOP"))
    assert acc.received
    assert acc.text == "ab"
    assert (acc.input_tokens, acc.output_tokens, acc.thinking_tokens) == (10, 2, None)
    assert acc.generation() == Generation(
        "ab", input_tokens=10, output_tokens=2, thinking_tokens=0, finish_reason="STOP"
    )


def test_prompt_chars_counts_system_prompt_and_user_message():
    assert prompt_chars("q", "ctx") == len(_SYSTEM_PROMPT) + len("Context:\nctx\n\nQuestion: q")


def test_gemini_answerer_exposes_its_model():
    assert GeminiAnswerer("k", "gemini-3.5-flash").model == "gemini-3.5-flash"


def test_prompt_version_is_an_int():
    assert isinstance(PROMPT_VERSION, int)
