import json
import time

import httpx
import pytest

from mtg_api.llm import (
    _SYSTEM_PROMPT,
    CONTEXT_OVERFLOW_PREFIX,
    ContextOverflow,
    OllamaAnswerer,
    StreamAccumulator,
)


def _ndjson(*lines: dict) -> bytes:
    return b"".join(json.dumps(line).encode() + b"\n" for line in lines)


_DONE = {
    "message": {"role": "assistant", "content": ""},
    "done": True,
    "done_reason": "stop",
    "prompt_eval_count": 1945,
    "eval_count": 8,
}

# Ollama 0.32's body for a prompt longer than num_ctx when truncate is false.
_OVERFLOW = json.dumps(
    {
        "error": json.dumps(
            {
                "error": {
                    "code": 400,
                    "message": "request (1945 tokens) exceeds the available context size "
                    "(512 tokens), try increasing it",
                    "type": "exceed_context_size_error",
                    "n_prompt_tokens": 1945,
                    "n_ctx": 512,
                }
            }
        )
    }
).encode()


def _ollama(lines=None, *, status=200, body=None, sent=None, **kwargs) -> OllamaAnswerer:
    """An OllamaAnswerer whose HTTP calls go to an in-memory Ollama that
    streams `lines` as NDJSON (or `body`: raw bytes or an iterator of them)."""
    lines = [{"message": {"content": "ok"}, "done": False}, _DONE] if lines is None else lines

    def handler(request: httpx.Request) -> httpx.Response:
        if sent is not None:
            sent.update(url=str(request.url), json=json.loads(request.content))
        content = body if body is not None else _ndjson(*lines)
        return httpx.Response(
            status, content=content, headers={"content-type": "application/x-ndjson"}
        )

    model = kwargs.pop("model", "phi4:latest")
    return OllamaAnswerer(
        model, "http://ollama:11434/", transport=httpx.MockTransport(handler), **kwargs
    )


def test_posts_native_chat_with_options_and_no_truncation():
    sent = {}
    answerer = _ollama(sent=sent, num_ctx=6144, seed=0, temperature=0.0, num_predict=1024)
    list(answerer.stream("Q?", "[1] Rule 1: x"))
    assert sent["url"] == "http://ollama:11434/api/chat"
    body = sent["json"]
    assert body["model"] == "phi4:latest"
    assert body["stream"] is True
    assert body["truncate"] is False
    assert body["options"] == {"num_ctx": 6144, "seed": 0, "num_predict": 1024, "temperature": 0.0}
    assert body["messages"] == [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": "Context:\n[1] Rule 1: x\n\nQuestion: Q?"},
    ]


def test_temperature_none_is_not_sent():
    sent = {}
    list(_ollama(sent=sent, temperature=None).stream("q", "c"))
    assert "temperature" not in sent["json"]["options"]


def test_streams_text_then_usage_and_finish_reason():
    answerer = _ollama(
        [
            {"message": {"content": "Yes "}, "done": False},
            {"message": {"content": "[1]."}, "done": False},
            _DONE,
        ]
    )
    acc = StreamAccumulator()
    for chunk in answerer.stream("q", "c"):
        acc.add(chunk)
    generation = acc.generation()
    assert generation.text == "Yes [1]."
    assert (generation.input_tokens, generation.output_tokens) == (1945, 8)
    assert generation.thinking_tokens == 0
    assert generation.finish_reason == "STOP"


def test_length_maps_to_max_tokens():
    chunks = list(_ollama([dict(_DONE, done_reason="length")]).stream("q", "c"))
    assert chunks[-1].finish_reason == "MAX_TOKENS"


def test_overlong_prompt_raises_context_overflow():
    with pytest.raises(ContextOverflow) as exc:
        list(_ollama(status=400, body=_OVERFLOW).stream("q", "c"))
    assert (exc.value.prompt_tokens, exc.value.num_ctx) == (1945, 512)
    assert str(exc.value) == f"{CONTEXT_OVERFLOW_PREFIX} prompt of 1945 tokens exceeds num_ctx 512"


def test_other_http_errors_raise_with_ollama_message():
    body = json.dumps({"error": "model 'phi9' not found"}).encode()
    with pytest.raises(RuntimeError, match="Ollama HTTP 404: model 'phi9' not found") as exc:
        list(_ollama(status=404, body=body).stream("q", "c"))
    assert not isinstance(exc.value, ContextOverflow)


def test_error_line_mid_stream_raises():
    lines = [{"message": {"content": "Ye"}, "done": False}, {"error": "out of memory"}]
    stream = _ollama(lines).stream("q", "c")
    assert next(stream).text == "Ye"
    with pytest.raises(RuntimeError, match="Ollama error: out of memory"):
        next(stream)


def test_stream_enforces_the_total_deadline():
    def slow_body():
        yield _ndjson({"message": {"content": "a"}, "done": False})
        time.sleep(0.2)
        yield _ndjson({"message": {"content": "b"}, "done": False})

    stream = _ollama(body=slow_body(), timeout=0.1).stream("q", "c")
    assert next(stream).text == "a"
    with pytest.raises(TimeoutError, match="0.1s"):
        next(stream)


def test_chunk_timeout_is_the_read_timeout():
    with OllamaAnswerer("phi4:latest", chunk_timeout=120.0)._client() as client:
        assert client.timeout.read == 120.0
        assert client.timeout.connect == 10.0


def test_exposes_its_model():
    assert OllamaAnswerer("phi4:latest").model == "phi4:latest"
