import pytest

from mtg_api.llm import (
    _SYSTEM_PROMPT,
    PROMPT_VERSION,
    GroqAnswerer,
    OllamaAnswerer,
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


class _FakeMessage:
    def __init__(self, content):
        self.content = content


class _FakeChoice:
    def __init__(self, content):
        self.message = _FakeMessage(content)


class _FakeCompletionResponse:
    def __init__(self, content):
        self.choices = [_FakeChoice(content)]


class _FakeCompletions:
    def __init__(self, content):
        self._content = content
        self.calls = []

    def create(self, *, model, messages):
        self.calls.append({"model": model, "messages": messages})
        return _FakeCompletionResponse(self._content)


class _FakeChat:
    def __init__(self, content):
        self.completions = _FakeCompletions(content)


class _FakeGroqClient:
    def __init__(self, content):
        self.chat = _FakeChat(content)


def test_generate_returns_the_completion_text():
    client = _FakeGroqClient("Trample means excess damage carries over.")
    answerer = GroqAnswerer(client, "openai/gpt-oss-120b")
    answer = answerer.generate("how does trample work", "[rule] 702.19\nTrample text")
    assert answer == "Trample means excess damage carries over."


def test_generate_sends_system_and_user_messages_with_model():
    client = _FakeGroqClient("answer")
    answerer = GroqAnswerer(client, "openai/gpt-oss-120b")
    answerer.generate("q", "ctx")
    call = client.chat.completions.calls[0]
    assert call["model"] == "openai/gpt-oss-120b"
    assert call["messages"][0]["role"] == "system"
    assert call["messages"][1]["role"] == "user"
    assert "ctx" in call["messages"][1]["content"]
    assert "q" in call["messages"][1]["content"]


def test_generate_propagates_client_exceptions():
    class _RaisingCompletions:
        def create(self, *, model, messages):
            raise RuntimeError("rate limited")

    class _RaisingChat:
        completions = _RaisingCompletions()

    class _RaisingClient:
        chat = _RaisingChat()

    answerer = GroqAnswerer(_RaisingClient(), "openai/gpt-oss-120b")
    with pytest.raises(RuntimeError, match="rate limited"):
        answerer.generate("q", "ctx")


def _capture_ollama_request(monkeypatch):
    import httpx

    sent = {}

    class _Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"message": {"content": "ok"}}

    def fake_post(url, json, timeout):
        sent.update(json)
        return _Response()

    monkeypatch.setattr(httpx, "post", fake_post)
    return sent


def test_ollama_answerer_omits_unset_generation_options(monkeypatch):
    sent = _capture_ollama_request(monkeypatch)
    OllamaAnswerer("http://ollama", "phi4").generate("q", "ctx")
    assert sent["model"] == "phi4"
    assert sent["options"] == {"num_ctx": 16384}


def test_ollama_answerer_sends_temperature_and_max_tokens(monkeypatch):
    sent = _capture_ollama_request(monkeypatch)
    OllamaAnswerer("http://ollama", "phi4", temperature=0.0, max_tokens=256).generate("q", "ctx")
    assert sent["options"] == {"num_ctx": 16384, "temperature": 0.0, "num_predict": 256}


def test_ollama_answerer_exposes_its_model():
    assert OllamaAnswerer("http://ollama", "phi4").model == "phi4"


def test_prompt_version_is_an_int():
    assert isinstance(PROMPT_VERSION, int)
