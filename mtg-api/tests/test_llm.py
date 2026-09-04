import pytest

from mtg_api.llm import GroqAnswerer, build_context
from mtg_api.models import QueryResult


def test_build_context_formats_each_result_as_a_block():
    results = [
        QueryResult(
            source="rule", title="702.19", text="Trample lets...", score=0.9, match_type="vector_hit"
        ),
        QueryResult(
            source="card",
            title="Craterhoof Behemoth",
            text="Trample. When...",
            score=1.0,
            match_type="card_name_match",
        ),
    ]
    context = build_context(results)
    assert "[rule] 702.19\nTrample lets..." in context
    assert "[card] Craterhoof Behemoth\nTrample. When..." in context


def test_build_context_empty_list_returns_empty_string():
    assert build_context([]) == ""


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
    answerer = GroqAnswerer(client, "llama-3.3-70b-versatile")
    answer = answerer.generate("how does trample work", "[rule] 702.19\nTrample text")
    assert answer == "Trample means excess damage carries over."


def test_generate_sends_system_and_user_messages_with_model():
    client = _FakeGroqClient("answer")
    answerer = GroqAnswerer(client, "llama-3.3-70b-versatile")
    answerer.generate("q", "ctx")
    call = client.chat.completions.calls[0]
    assert call["model"] == "llama-3.3-70b-versatile"
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

    answerer = GroqAnswerer(_RaisingClient(), "llama-3.3-70b-versatile")
    with pytest.raises(RuntimeError, match="rate limited"):
        answerer.generate("q", "ctx")
