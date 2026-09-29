import pytest

from mtg_api.llm import (
    _SYSTEM_PROMPT,
    PROMPT_VERSION,
    GeminiAnswerer,
    Generation,
    build_context,
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


def _capture_gemini_request(monkeypatch, response_json=None, status=200):
    import httpx

    if response_json is None:
        response_json = {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}
    sent = {}

    def fake_post(url, headers, json, timeout):
        sent.update(url=url, headers=headers, json=json, timeout=timeout)
        request = httpx.Request("POST", url)
        return httpx.Response(status, json=response_json, request=request)

    monkeypatch.setattr(httpx, "post", fake_post)
    return sent


def test_gemini_answerer_posts_generate_content(monkeypatch):
    sent = _capture_gemini_request(
        monkeypatch,
        {"candidates": [{"content": {"parts": [{"text": "Trample carries over [1]."}]}}]},
    )
    answer = GeminiAnswerer(
        "secret",
        "gemini-3.5-flash",
        base_url="https://generativelanguage.googleapis.com/",
        timeout=30.0,
    ).generate("q", "ctx")
    assert answer.text == "Trample carries over [1]."
    assert sent["url"] == (
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash:generateContent"
    )
    assert sent["headers"] == {"x-goog-api-key": "secret"}
    assert sent["timeout"] == 30.0
    assert sent["json"]["systemInstruction"] == {"parts": [{"text": _SYSTEM_PROMPT}]}
    assert sent["json"]["contents"][0]["role"] == "user"
    assert "ctx" in sent["json"]["contents"][0]["parts"][0]["text"]
    assert "generationConfig" not in sent["json"]


def test_gemini_answerer_sends_temperature_and_max_tokens(monkeypatch):
    sent = _capture_gemini_request(monkeypatch)
    GeminiAnswerer("k", "m", temperature=0.0, max_tokens=256).generate("q", "ctx")
    assert sent["json"]["generationConfig"] == {"temperature": 0.0, "maxOutputTokens": 256}


def test_gemini_answerer_skips_thought_parts(monkeypatch):
    parts = [
        {"text": "Let me think...", "thought": True},
        {"text": "Yes "},
        {"text": "[1]."},
    ]
    _capture_gemini_request(monkeypatch, {"candidates": [{"content": {"parts": parts}}]})
    assert GeminiAnswerer("k", "m").generate("q", "ctx").text == "Yes [1]."


def test_gemini_answerer_raises_when_blocked(monkeypatch):
    _capture_gemini_request(monkeypatch, {"promptFeedback": {"blockReason": "SAFETY"}})
    with pytest.raises(RuntimeError, match="SAFETY"):
        GeminiAnswerer("k", "m").generate("q", "ctx")


def test_gemini_answerer_raises_on_http_error(monkeypatch):
    import httpx

    _capture_gemini_request(monkeypatch, status=429)
    with pytest.raises(httpx.HTTPStatusError):
        GeminiAnswerer("k", "m").generate("q", "ctx")


def test_gemini_answerer_exposes_its_model():
    assert GeminiAnswerer("k", "gemini-3.5-flash").model == "gemini-3.5-flash"


def test_prompt_version_is_an_int():
    assert isinstance(PROMPT_VERSION, int)


def test_gemini_answerer_sends_thinking_level(monkeypatch):
    sent = _capture_gemini_request(monkeypatch)
    GeminiAnswerer("k", "m", max_tokens=2048, thinking_level="low").generate("q", "ctx")
    assert sent["json"]["generationConfig"] == {
        "maxOutputTokens": 2048,
        "thinkingConfig": {"thinkingLevel": "low"},
    }


def test_gemini_answerer_returns_token_usage(monkeypatch):
    _capture_gemini_request(
        monkeypatch,
        {
            "candidates": [{"content": {"parts": [{"text": "ok"}]}}],
            "usageMetadata": {
                "promptTokenCount": 1200,
                "candidatesTokenCount": 150,
                "thoughtsTokenCount": 300,
                "totalTokenCount": 1650,
            },
        },
    )
    result = GeminiAnswerer("k", "m").generate("q", "ctx")
    assert result == Generation(
        text="ok", input_tokens=1200, output_tokens=150, thinking_tokens=300
    )
    assert result.usage() == {"input_tokens": 1200, "output_tokens": 150, "thinking_tokens": 300}


def test_gemini_answerer_usage_defaults_to_zero(monkeypatch):
    _capture_gemini_request(monkeypatch)  # no usageMetadata
    result = GeminiAnswerer("k", "m").generate("q", "ctx")
    assert (result.input_tokens, result.output_tokens, result.thinking_tokens) == (0, 0, 0)


def test_gemini_answerer_parses_finish_reason(monkeypatch):
    _capture_gemini_request(
        monkeypatch,
        {
            "candidates": [
                {"content": {"parts": [{"text": "cut off"}]}, "finishReason": "MAX_TOKENS"}
            ]
        },
    )
    result = GeminiAnswerer("k", "m").generate("q", "ctx")
    assert result.finish_reason == "MAX_TOKENS"


def test_gemini_answerer_finish_reason_defaults_to_none_when_absent(monkeypatch):
    _capture_gemini_request(
        monkeypatch, {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}
    )
    result = GeminiAnswerer("k", "m").generate("q", "ctx")
    assert result.finish_reason is None
