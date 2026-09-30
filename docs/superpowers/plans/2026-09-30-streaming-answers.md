# Streaming Answers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stream Gemini answers to the search page over SSE: evidence first, then "Thinking…", then the answer text as it arrives, then a validated final event. Answers keep generating on the server after the visitor leaves, and spend is reserved up front so the quotas still hold.

**Architecture:** The body of `POST /api/v1/query` splits into two phases. Phase 1 runs in the request thread: validation, cache, gate, a generation slot, retrieval, and the spend reservation. Phase 2 runs in a worker thread (`AnswerJob`) that reads `GeminiAnswerer.stream()` into a queue of events and does all the bookkeeping. The new `POST /api/v1/query/stream` relays that queue as SSE. The existing JSON endpoint drains the same queue and returns the same `QueryResponse` as before. The SvelteKit page reads the SSE stream with `fetch`.

**Tech Stack:** FastAPI/Starlette `StreamingResponse`, httpx streaming (`MockTransport` in tests), SQLAlchemy, pytest; SvelteKit 2 + Svelte 5 runes, Vitest, Playwright; nginx.

**Spec:** `docs/superpowers/specs/2026-09-30-streaming-answers-design.md`. Read it before starting. The decisions table there is the source of truth.

## Global Constraints

- Branch: `feature/streaming-answers` (already cut from `origin/main`). Never stage `.env.example` or `docker-compose.yml`; they hold the user's own uncommitted changes.
- End every commit message with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Backend tests: `cd mtg-api && pytest`. Lint: `cd mtg-api && ruff check . && ruff format --check .` (line length 100).
- Frontend: `cd mtg-web && npm run check && npm test`. E2E: `cd mtg-web && npm run test:e2e`.
- `/api/v1/query` keeps its JSON contract. The only change is the new `answer_complete` field. Evals depend on it.
- Model output is never rendered with `{@html}`.
- Settings defaults: `gemini_stream_chunk_timeout_seconds = 30.0`, `gemini_timeout_seconds` stays `60.0` (now the total cap), `max_concurrent_generations = 4`, `max_concurrent_generations_per_ip = 1`.
- Estimates: 4 chars per token, rounded up. With no `generation_max_tokens`, assume 2048.
- Eval mode skips both concurrency caps. Admins skip the per-IP cap only.
- SSE framing: `event: <name>\ndata: <json>\n\n`. Response headers: `Cache-Control: no-cache`, `X-Accel-Buffering: no`.
- Event order: `results`, then `thinking`, then `delta`×N, then optionally `error`, then `done`. **`done` is always last.** `error` comes only when generation failed with no text at all.

## File Map

Backend (`mtg-api/src/mtg_api/`):
- `llm.py`: adds `StreamChunk`, `StreamAccumulator`, `prompt_chars()`, and `GeminiAnswerer.stream()`. `generate()` becomes a collector over `stream()`.
- `config.py`: new settings for the chunk timeout and the concurrency caps.
- `usage.py`: the `pending` outcome, `estimate_tokens`, `worst_case_cost`, `estimate_generation`, `reserve_usage`, `finalize_usage`.
- `streaming.py` (new): `sse_event`, `AnswerJob`, `join_all`, `GenerationSlots`. Generic, with no knowledge of queries.
- `models.py`: `QueryResponse.answer_complete`.
- `main.py`: `QueryDeps`, `_retrieve`, `_start_query` (phase 1), `_run_answer` (phase 2), both endpoints, and replay's `answer_complete`.

Frontend (`mtg-web/src/`):
- `lib/sse.ts` (new): SSE frame parser.
- `lib/api.ts`: the `streamQuery`, `StreamHead`, `StreamDone` and `StreamHandlers` types, plus `answer_complete`.
- `lib/stream.ts` (new): `visibleDraft`, `liveCitations`, `liveResults`.
- `lib/fixtures/index.ts`: `mockStream` and the streaming fixtures.
- `routes/+page.svelte`: streaming view, "Thinking…", and the cut-off notice.
- `routes/history/+page.svelte`: a "cut off" chip.
- `routes/admin/usage/+page.svelte`: the `pending` outcome label.
- `e2e/streaming.desktop.spec.ts` (new).

Infra/docs: `mtg-web/nginx.conf`, `README.md`.

---

### Task 1: Gemini streaming client

**Files:**
- Modify: `mtg-api/src/mtg_api/llm.py`
- Modify: `mtg-api/src/mtg_api/config.py` (after `gemini_timeout_seconds`)
- Modify: `mtg-api/src/mtg_api/main.py:119-133` (`build_answerer`)
- Test: `mtg-api/tests/test_llm.py` (rewrite everything from `_capture_gemini_request` down)

**Interfaces:**
- Produces:
  - `StreamChunk(text: str = "", input_tokens: int | None = None, output_tokens: int | None = None, thinking_tokens: int | None = None, finish_reason: str | None = None)`, a frozen dataclass.
  - `StreamAccumulator` with `.add(chunk)`, `.text: str`, `.received: bool`, `.input_tokens/.output_tokens/.thinking_tokens: int | None`, `.finish_reason: str | None`, `.generation() -> Generation` (missing counts become 0).
  - `prompt_chars(query: str, context: str) -> int`
  - `GeminiAnswerer(..., timeout=60.0, chunk_timeout=30.0, transport: httpx.BaseTransport | None = None)`, `.stream(query, context) -> Iterator[StreamChunk]`, `.generate(query, context) -> Generation` (unchanged signature).
  - `Settings.gemini_stream_chunk_timeout_seconds: float = 30.0`

- [ ] **Step 1: Write the failing tests.** In `mtg-api/tests/test_llm.py`, add `import json` and `import httpx` at the top. Then replace everything from `def _capture_gemini_request` to the end of the file with:

```python
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
    chunks = [{"candidates": [{"content": {"parts": [{"text": "cut"}]}, "finishReason": "MAX_TOKENS"}]}]
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
```

Update the import block at the top of the file to:

```python
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
```

- [ ] **Step 2: Run the tests and confirm they fail.**

Run: `cd mtg-api && pytest tests/test_llm.py -q`
Expected: an ImportError for `StreamAccumulator`.

- [ ] **Step 3: Implement.** In `llm.py`, add `import json`, `import time` and `from collections.abc import Iterator` to the imports. Add `from typing import TYPE_CHECKING` and, below the imports:

```python
if TYPE_CHECKING:
    import httpx
```

Add `_user_message` and `prompt_chars` after `build_context`:

```python
def _user_message(query: str, context: str) -> str:
    return f"Context:\n{context}\n\nQuestion: {query}"


def prompt_chars(query: str, context: str) -> int:
    """Characters sent to Gemini: the basis for estimating input tokens."""
    return len(_SYSTEM_PROMPT) + len(_user_message(query, context))
```

After the `Generation` class, add:

```python
@dataclass(frozen=True)
class StreamChunk:
    """One streamGenerateContent event: its answer text (thought parts left
    out) and whatever usage and finish reason it carried."""

    text: str = ""
    input_tokens: int | None = None
    output_tokens: int | None = None
    thinking_tokens: int | None = None
    finish_reason: str | None = None


class StreamAccumulator:
    """Collects a stream as it arrives. Counts keep the last value Gemini
    sent; None means Gemini never sent one."""

    def __init__(self) -> None:
        self._parts: list[str] = []
        self.received = False
        self.input_tokens: int | None = None
        self.output_tokens: int | None = None
        self.thinking_tokens: int | None = None
        self.finish_reason: str | None = None

    def add(self, chunk: StreamChunk) -> None:
        self.received = True
        self._parts.append(chunk.text)
        for field in ("input_tokens", "output_tokens", "thinking_tokens", "finish_reason"):
            value = getattr(chunk, field)
            if value is not None:
                setattr(self, field, value)

    @property
    def text(self) -> str:
        return "".join(self._parts)

    def generation(self) -> Generation:
        return Generation(
            text=self.text,
            input_tokens=self.input_tokens or 0,
            output_tokens=self.output_tokens or 0,
            thinking_tokens=self.thinking_tokens or 0,
            finish_reason=self.finish_reason,
        )


def _parse_chunk(data: dict) -> StreamChunk:
    candidates = data.get("candidates") or []
    feedback = data.get("promptFeedback") or {}
    if not candidates and feedback.get("blockReason"):
        # A blocked prompt comes back 200 with no candidates.
        raise RuntimeError(f"Gemini returned no answer: {feedback['blockReason']}")
    usage = data.get("usageMetadata") or {}
    candidate = candidates[0] if candidates else {}
    parts = candidate.get("content", {}).get("parts", [])
    return StreamChunk(
        # Skip thought-summary parts; keep only the answer text.
        text="".join(p.get("text", "") for p in parts if not p.get("thought")),
        input_tokens=usage.get("promptTokenCount"),
        output_tokens=usage.get("candidatesTokenCount"),
        thinking_tokens=usage.get("thoughtsTokenCount"),
        finish_reason=candidate.get("finishReason"),
    )
```

Replace `GeminiAnswerer` with:

```python
class GeminiAnswerer:
    """Answerer via the Gemini API's streamGenerateContent."""

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str = "https://generativelanguage.googleapis.com",
        temperature: float | None = None,
        max_tokens: int | None = None,
        thinking_level: str | None = None,
        timeout: float = 60.0,
        chunk_timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ):
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._thinking_level = thinking_level
        # Total time for one answer. chunk_timeout bounds each wait between
        # chunks, which includes the thinking before the first one.
        self._timeout = timeout
        self._chunk_timeout = chunk_timeout
        self._transport = transport

    @property
    def model(self) -> str:
        return self._model

    def _body(self, query: str, context: str) -> dict:
        body: dict = {
            "systemInstruction": {"parts": [{"text": _SYSTEM_PROMPT}]},
            "contents": [{"role": "user", "parts": [{"text": _user_message(query, context)}]}],
        }
        config: dict = {}
        if self._temperature is not None:
            config["temperature"] = self._temperature
        if self._max_tokens is not None:
            # Gemini counts thinking tokens against this limit too.
            config["maxOutputTokens"] = self._max_tokens
        if self._thinking_level is not None:
            config["thinkingConfig"] = {"thinkingLevel": self._thinking_level}
        if config:
            body["generationConfig"] = config
        return body

    def _client(self) -> httpx.Client:
        import httpx

        return httpx.Client(
            transport=self._transport, timeout=httpx.Timeout(self._chunk_timeout, connect=10.0)
        )

    def stream(self, query: str, context: str) -> Iterator[StreamChunk]:
        deadline = time.monotonic() + self._timeout
        with (
            self._client() as client,
            client.stream(
                "POST",
                f"{self._base_url}/v1beta/models/{self._model}:streamGenerateContent",
                params={"alt": "sse"},
                headers={"x-goog-api-key": self._api_key},
                json=self._body(query, context),
            ) as response,
        ):
            if response.is_error:
                response.read()  # so the raised error carries Gemini's message
            response.raise_for_status()
            for line in response.iter_lines():
                if time.monotonic() > deadline:
                    raise TimeoutError(f"Gemini answer took longer than {self._timeout:g}s")
                if line.startswith("data:"):
                    yield _parse_chunk(json.loads(line[5:]))

    def generate(self, query: str, context: str) -> Generation:
        acc = StreamAccumulator()
        for chunk in self.stream(query, context):
            acc.add(chunk)
        return acc.generation()
```

In `config.py`, after `gemini_timeout_seconds: float = 60.0`, add:

```python
    # Longest wait between two chunks of a streamed answer (the thinking
    # before the first chunk included). gemini_timeout_seconds caps the total.
    gemini_stream_chunk_timeout_seconds: float = 30.0
```

In `main.py` `build_answerer`, add `chunk_timeout=s.gemini_stream_chunk_timeout_seconds,` after `timeout=s.gemini_timeout_seconds,`.

- [ ] **Step 4: Run the tests and confirm they pass.**

Run: `cd mtg-api && pytest tests/test_llm.py -q && pytest -q`
Expected: all pass. `main.py` still calls `generate()`, so the other suites are unaffected.

- [ ] **Step 5: Commit.**

```bash
git add mtg-api/src/mtg_api/llm.py mtg-api/src/mtg_api/config.py mtg-api/src/mtg_api/main.py mtg-api/tests/test_llm.py
git commit -m "feat(api): stream Gemini answers with streamGenerateContent

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Spend reservation and estimates

**Files:**
- Modify: `mtg-api/src/mtg_api/usage.py`
- Modify: `mtg-web/src/routes/admin/usage/+page.svelte:7-13`
- Test: `mtg-api/tests/test_usage.py`

**Interfaces:**
- Consumes: `StreamAccumulator`, `Generation` (Task 1).
- Produces:
  - `ANSWER_OUTCOMES = ("generated", "error", "pending")`
  - `FALLBACK_MAX_TOKENS = 2048`
  - `estimate_tokens(chars: int) -> int`
  - `worst_case_cost(prompt_chars: int, s: Settings) -> float`
  - `estimate_generation(acc: StreamAccumulator, prompt_chars: int, s: Settings) -> Generation`
  - `reserve_usage(engine, *, now, ip_bucket, is_admin, model, cost) -> int` (returns the row id)
  - `finalize_usage(engine, row_id: int, *, outcome: str, generation: Generation, cost: float) -> None`

- [ ] **Step 1: Write the failing tests.** Add `StreamAccumulator, StreamChunk` to the `mtg_api.llm` import in `mtg-api/tests/test_usage.py`, and add `estimate_generation, estimate_tokens, finalize_usage, reserve_usage, worst_case_cost` to the `mtg_api.usage` import. Add `from sqlalchemy import select` and `llm_usage` to the imports. Then append:

```python
def _reserve(engine, *, cost=0.5, bucket="203.0.113.7", at=NOW):
    return reserve_usage(
        engine, now=at, ip_bucket=bucket, is_admin=False, model="gemini-3.5-flash", cost=cost
    )


def test_estimate_tokens_rounds_up():
    assert estimate_tokens(0) == 0
    assert estimate_tokens(1) == 1
    assert estimate_tokens(8) == 2
    assert estimate_tokens(9) == 3


def test_worst_case_cost_is_prompt_plus_max_tokens():
    s = _settings(generation_max_tokens=1000)
    # 4M chars = 1M input tokens at $1; 1000 output tokens at $2/M.
    assert worst_case_cost(4_000_000, s) == pytest.approx(1.0 + 0.002)


def test_worst_case_cost_assumes_2048_without_max_tokens():
    assert worst_case_cost(0, _settings()) == pytest.approx(2048 * 2.0 / 1_000_000)


def test_pending_reservation_counts_against_quota_and_budget():
    engine = memory_engine()
    _reserve(engine, cost=0.6)
    assert answers_since(engine, "203.0.113.7", day_start(NOW)) == 1
    assert spend_since(engine, day_start(NOW)) == pytest.approx(0.6)


def test_finalize_replaces_the_reservation_in_place():
    engine = memory_engine()
    row_id = _reserve(engine, cost=0.6)
    g = Generation("x", input_tokens=10, output_tokens=2, thinking_tokens=3)
    finalize_usage(engine, row_id, outcome="generated", generation=g, cost=0.01)
    with engine.connect() as conn:
        row = conn.execute(select(llm_usage)).mappings().one()
    assert (row["outcome"], row["cost_usd"]) == ("generated", pytest.approx(0.01))
    assert (row["input_tokens"], row["output_tokens"], row["thinking_tokens"]) == (10, 2, 3)


def test_estimate_generation_uses_real_counts_once_the_stream_finished():
    acc = StreamAccumulator()
    acc.add(StreamChunk(text="abcd", input_tokens=100, output_tokens=5, finish_reason="STOP"))
    # No thoughtsTokenCount after a finished stream means no thinking.
    assert estimate_generation(acc, 4000, _settings()) == Generation(
        "abcd", input_tokens=100, output_tokens=5, thinking_tokens=0, finish_reason="STOP"
    )


def test_estimate_generation_fills_gaps_when_the_stream_broke_off():
    acc = StreamAccumulator()
    acc.add(StreamChunk(text="abcdefghi"))  # 9 chars, no usage, no finish reason
    g = estimate_generation(acc, 4001, _settings(generation_max_tokens=512))
    assert (g.input_tokens, g.output_tokens, g.thinking_tokens) == (1001, 3, 512)


def test_estimate_generation_prefers_counts_seen_before_the_break():
    acc = StreamAccumulator()
    acc.add(StreamChunk(text="ab", input_tokens=700, thinking_tokens=40))
    g = estimate_generation(acc, 4000, _settings())
    assert (g.input_tokens, g.output_tokens, g.thinking_tokens) == (700, 1, 40)


def test_estimate_generation_is_free_when_nothing_arrived():
    g = estimate_generation(StreamAccumulator(), 4000, _settings())
    assert (g.input_tokens, g.output_tokens, g.thinking_tokens) == (0, 0, 0)
```

- [ ] **Step 2: Run the tests and confirm they fail.**

Run: `cd mtg-api && pytest tests/test_usage.py -q`
Expected: an ImportError for `estimate_generation`.

- [ ] **Step 3: Implement.** In `usage.py`, add `import math`, change the llm import to `from mtg_api.llm import Generation, StreamAccumulator`, and update the outcome comment and tuple:

```python
    # generated | cached | degraded_ip | degraded_global | error | pending
    # (pending: reserved at worst-case cost while an answer is written)
```

```python
# Outcomes that called Gemini, and so use up a visitor's quota. A pending
# row is an answer still being written.
ANSWER_OUTCOMES = ("generated", "error", "pending")

# Rough chars per token, for estimates only.
_CHARS_PER_TOKEN = 4
# Output assumed when generation_max_tokens is unset.
FALLBACK_MAX_TOKENS = 2048
```

After `cost_usd`, add:

```python
def estimate_tokens(chars: int) -> int:
    return math.ceil(chars / _CHARS_PER_TOKEN)


def _max_output(s: Settings) -> int:
    return s.generation_max_tokens or FALLBACK_MAX_TOKENS


def worst_case_cost(prompt_chars: int, s: Settings) -> float:
    """What one answer can cost at most: the whole prompt plus the full
    output allowance (thinking included), billed as output."""
    return cost_usd(
        Generation("", input_tokens=estimate_tokens(prompt_chars), output_tokens=_max_output(s)),
        s,
    )


def estimate_generation(acc: StreamAccumulator, prompt_chars: int, s: Settings) -> Generation:
    """Token counts for a stream, however it ended. A finished stream (it
    sent a finish reason) reports every count, and a missing one is zero. A
    stream that broke off keeps the counts it did send and estimates the
    rest high. One that never sent anything cost nothing."""
    if not acc.received or acc.finish_reason is not None:
        return acc.generation()
    return Generation(
        text=acc.text,
        input_tokens=acc.input_tokens
        if acc.input_tokens is not None
        else estimate_tokens(prompt_chars),
        output_tokens=acc.output_tokens
        if acc.output_tokens is not None
        else estimate_tokens(len(acc.text)),
        thinking_tokens=acc.thinking_tokens
        if acc.thinking_tokens is not None
        else _max_output(s),
    )
```

After `record_usage`, add:

```python
def reserve_usage(
    engine: Engine,
    *,
    now: datetime,
    ip_bucket: str,
    is_admin: bool,
    model: str,
    cost: float,
) -> int:
    """Insert a pending row at `cost` (the worst case) before generating, so
    quotas and the budget count an answer that is still being written. A
    row never finalized keeps that cost."""
    with engine.begin() as conn:
        result = conn.execute(
            llm_usage.insert().values(
                created_at=now,
                ip_bucket=ip_bucket,
                is_admin=is_admin,
                outcome="pending",
                model=model,
                input_tokens=0,
                output_tokens=0,
                thinking_tokens=0,
                cost_usd=cost,
            )
        )
        return result.inserted_primary_key[0]


def finalize_usage(
    engine: Engine, row_id: int, *, outcome: str, generation: Generation, cost: float
) -> None:
    with engine.begin() as conn:
        conn.execute(
            llm_usage.update()
            .where(llm_usage.c.id == row_id)
            .values(
                outcome=outcome,
                input_tokens=generation.input_tokens,
                output_tokens=generation.output_tokens,
                thinking_tokens=generation.thinking_tokens,
                cost_usd=cost,
            )
        )
```

In `mtg-web/src/routes/admin/usage/+page.svelte`, add `['pending', 'In progress'],` to `OUTCOMES` after `['error', 'Error']`.

- [ ] **Step 4: Run the tests and confirm they pass.**

Run: `cd mtg-api && pytest -q && cd ../mtg-web && npm run check`
Expected: all pass.

- [ ] **Step 5: Commit.**

```bash
git add mtg-api/src/mtg_api/usage.py mtg-api/tests/test_usage.py mtg-web/src/routes/admin/usage/+page.svelte
git commit -m "feat(api): reserve worst-case spend while an answer is written

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Answer jobs, generation slots and SSE framing

**Files:**
- Create: `mtg-api/src/mtg_api/streaming.py`
- Modify: `mtg-api/src/mtg_api/config.py` (after `ip_window_minutes`)
- Test: `mtg-api/tests/test_streaming.py`

**Interfaces:**
- Produces:
  - `sse_event(name: str, data: dict) -> str`
  - `Emit = Callable[[str, dict], None]`
  - `AnswerJob(work: Callable[[Emit], None])` with `.start() -> AnswerJob` and `.events() -> Iterator[tuple[str, dict]]`
  - `join_all(timeout: float = 5.0) -> None` (tests: wait for every job thread)
  - `GenerationSlots()` with `.acquire(bucket: str, *, total_limit: int, ip_limit: int | None) -> Callable[[], None] | None`. `ip_limit=None` skips the per-IP count. The returned release is idempotent.
  - `Settings.max_concurrent_generations: int = 4`, `Settings.max_concurrent_generations_per_ip: int = 1`

- [ ] **Step 1: Write the failing tests** in `mtg-api/tests/test_streaming.py`:

```python
import json
import threading

from mtg_api.streaming import AnswerJob, GenerationSlots, join_all, sse_event


def test_sse_event_frames_name_and_json():
    frame = sse_event("delta", {"text": "a\nb", "at": None})
    assert frame.startswith("event: delta\ndata: ")
    assert frame.endswith("\n\n")
    # The JSON stays on one line, so a newline in the text can't split the frame.
    assert json.loads(frame.split("data: ", 1)[1]) == {"text": "a\nb", "at": None}
    assert frame.count("\n") == 3


def test_job_relays_events_in_order_then_ends():
    def work(emit):
        emit("thinking", {})
        emit("done", {"answer": "x"})

    assert list(AnswerJob(work).start().events()) == [
        ("thinking", {}),
        ("done", {"answer": "x"}),
    ]


def test_job_finishes_without_anyone_reading():
    finished = threading.Event()

    def work(emit):
        emit("delta", {"text": "a"})
        finished.set()

    AnswerJob(work).start()
    join_all()
    assert finished.is_set()


def test_job_that_raises_still_ends_its_events():
    def work(emit):
        emit("thinking", {})
        raise RuntimeError("bug")

    assert list(AnswerJob(work).start().events()) == [("thinking", {})]


def test_slots_cap_per_ip_and_release():
    slots = GenerationSlots()
    release = slots.acquire("a", total_limit=4, ip_limit=1)
    assert release is not None
    assert slots.acquire("a", total_limit=4, ip_limit=1) is None
    assert slots.acquire("b", total_limit=4, ip_limit=1) is not None
    release()
    release()  # idempotent: doesn't free a slot it doesn't hold
    again = slots.acquire("a", total_limit=4, ip_limit=1)
    assert again is not None
    assert slots.acquire("a", total_limit=4, ip_limit=1) is None


def test_slots_cap_the_total_even_without_an_ip_limit():
    slots = GenerationSlots()
    assert slots.acquire("admin", total_limit=2, ip_limit=None) is not None
    assert slots.acquire("admin", total_limit=2, ip_limit=None) is not None
    assert slots.acquire("admin", total_limit=2, ip_limit=None) is None
    assert slots.acquire("visitor", total_limit=2, ip_limit=1) is None
```

- [ ] **Step 2: Run the tests and confirm they fail.**

Run: `cd mtg-api && pytest tests/test_streaming.py -q`
Expected: `ModuleNotFoundError: mtg_api.streaming`.

- [ ] **Step 3: Implement** `mtg-api/src/mtg_api/streaming.py`:

```python
"""Plumbing for streamed answers: SSE framing, a worker thread whose events
can be relayed to a client (or to no one), and caps on concurrent
generations. Knows nothing about queries."""

from __future__ import annotations

import json
import logging
import queue
import threading
from collections import Counter
from collections.abc import Callable, Iterator

from fastapi.encoders import jsonable_encoder

logger = logging.getLogger(__name__)

Emit = Callable[[str, dict], None]

_END = object()
_live: set[threading.Thread] = set()
_live_lock = threading.Lock()


def sse_event(name: str, data: dict) -> str:
    # json.dumps escapes newlines, so the data is always one line.
    return f"event: {name}\ndata: {json.dumps(jsonable_encoder(data))}\n\n"


class AnswerJob:
    """Runs `work(emit)` on its own thread. Events go into a queue that
    `events()` relays until the work returns. The work never depends on
    anyone reading: a client that leaves only stops the relay."""

    def __init__(self, work: Callable[[Emit], None]):
        self._work = work
        self._queue: queue.Queue = queue.Queue()
        self._thread = threading.Thread(target=self._run, name="answer-job", daemon=True)

    def start(self) -> AnswerJob:
        with _live_lock:
            _live.add(self._thread)
        self._thread.start()
        return self

    def _run(self) -> None:
        try:
            self._work(lambda name, data: self._queue.put((name, data)))
        except Exception:
            logger.exception("Answer job failed")
        finally:
            self._queue.put(_END)
            with _live_lock:
                _live.discard(self._thread)

    def events(self) -> Iterator[tuple[str, dict]]:
        while (item := self._queue.get()) is not _END:
            yield item


def join_all(timeout: float = 5.0) -> None:
    """Wait for every running job. For tests."""
    with _live_lock:
        threads = list(_live)
    for thread in threads:
        thread.join(timeout)


class GenerationSlots:
    """How many answers are being written, in total and per IP bucket. In
    memory: the API runs as one process."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._total = 0
        self._by_bucket: Counter[str] = Counter()

    def acquire(
        self, bucket: str, *, total_limit: int, ip_limit: int | None
    ) -> Callable[[], None] | None:
        """A release function, or None when a cap is full. ip_limit=None
        leaves this request out of the per-IP count."""
        with self._lock:
            if self._total >= total_limit:
                return None
            if ip_limit is not None and self._by_bucket[bucket] >= ip_limit:
                return None
            self._total += 1
            if ip_limit is not None:
                self._by_bucket[bucket] += 1

        released = False

        def release() -> None:
            nonlocal released
            with self._lock:
                if released:
                    return
                released = True
                self._total -= 1
                if ip_limit is not None:
                    self._by_bucket[bucket] -= 1
                    if not self._by_bucket[bucket]:
                        del self._by_bucket[bucket]

        return release
```

In `config.py`, after `ip_window_minutes: int = 10`, add:

```python
    # Answers being written at once, site-wide and per IP bucket. Over
    # either, a request gets a 429 before anything streams. Eval mode is
    # exempt from both; an admin only from the per-IP cap.
    max_concurrent_generations: int = 4
    max_concurrent_generations_per_ip: int = 1
```

- [ ] **Step 4: Run the tests and confirm they pass.**

Run: `cd mtg-api && pytest tests/test_streaming.py -q && ruff check . && ruff format --check .`
Expected: pass.

- [ ] **Step 5: Commit.**

```bash
git add mtg-api/src/mtg_api/streaming.py mtg-api/src/mtg_api/config.py mtg-api/tests/test_streaming.py
git commit -m "feat(api): answer jobs, generation slots and SSE framing

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Two-phase query pipeline behind the JSON endpoint

The largest task. `/api/v1/query` is rebuilt on phase 1 plus an `AnswerJob`, with no change to its JSON other than `answer_complete`. The existing suites (`test_query.py`, `test_gating_flow.py`, `test_eval_mode.py`) are the safety net, so they must pass unchanged apart from the fake answerers.

**Files:**
- Modify: `mtg-api/src/mtg_api/models.py` (`QueryResponse`)
- Modify: `mtg-api/src/mtg_api/main.py` (imports; replace `query()` at lines 265-527; `_PER_REQUEST_FIELDS` stays)
- Modify: `mtg-api/tests/conftest.py` (add `StreamsFromGenerate`)
- Modify: `mtg-api/tests/test_query.py:72-83`, `mtg-api/tests/test_gating_flow.py:17-33`, `mtg-api/tests/test_eval_mode.py:27-33` (the fakes)
- Test: `mtg-api/tests/test_gating_flow.py` (new tests appended)

**Interfaces:**
- Consumes: Task 1 (`StreamAccumulator`, `prompt_chars`, `answerer.stream`), Task 2 (`reserve_usage`, `finalize_usage`, `worst_case_cost`, `estimate_generation`), Task 3 (`AnswerJob`, `GenerationSlots`).
- Produces (used by Task 5):
  - `QueryResponse.answer_complete: bool | None = None`
  - `main.QueryDeps` (dataclass: `matcher, keyword_matcher, dense_embedder, sparse_embedder, client, answerer, engine, rules_index, data_version`) and `main.get_query_deps`
  - `main._Started(head: dict, rest: Iterator[tuple[str, dict]])`. `head` is the `results` event data. `rest` yields the events after it and always ends with `("done", {...})`.
  - `main._start_query(request: QueryRequest, http_request: Request, d: QueryDeps) -> _Started`
  - `main.generation_slots: GenerationSlots`

- [ ] **Step 1: Teach the fakes to stream.** Append to `mtg-api/tests/conftest.py`:

```python
class StreamsFromGenerate:
    """For fake answerers that define generate(): serves it as a one-chunk
    stream, the way the pipeline reads answers. Raises on first read, as a
    real failed stream does."""

    def stream(self, query, context):
        from mtg_api.llm import StreamChunk

        g = self.generate(query, context)
        yield StreamChunk(
            text=g.text,
            input_tokens=g.input_tokens,
            output_tokens=g.output_tokens,
            thinking_tokens=g.thinking_tokens,
            finish_reason=g.finish_reason,
        )
```

Make each fake subclass it, and give each complete answer `finish_reason="STOP"`. A real finished Gemini stream always sends one, and without it the pipeline treats the answer as cut off.
- `test_query.py`: `from conftest import StreamsFromGenerate, memory_engine`, then `class _FakeAnswerer(StreamsFromGenerate):`, and add `finish_reason="STOP"` to its `Generation(...)`.
- `test_gating_flow.py`: `from conftest import StreamsFromGenerate, admin_client, memory_engine`, then `class _CountingAnswerer(StreamsFromGenerate):`. It already passes `finish_reason`.
- `test_eval_mode.py`: import `StreamsFromGenerate` from conftest, then `class _RecordingAnswerer(StreamsFromGenerate):`, and `return Generation(text="An answer.", finish_reason="STOP")`.

- [ ] **Step 2: Write the failing tests.** Append to `mtg-api/tests/test_gating_flow.py`:

```python
class _ChunksAnswerer:
    """Streams the given chunks, then raises `then_raise` if set."""

    def __init__(self, chunks, then_raise=None):
        self._chunks = chunks
        self._then_raise = then_raise
        self.calls = 0

    def stream(self, query, context):
        self.calls += 1
        yield from self._chunks
        if self._then_raise:
            raise self._then_raise


def _rows(engine):
    with engine.connect() as conn:
        return conn.execute(select(llm_usage).order_by(llm_usage.c.id)).mappings().all()


def test_complete_answer_is_marked_complete():
    _setup()
    assert _post({"query": "trample"}).json()["answer_complete"] is True


def test_cut_off_answer_is_marked_and_saved_with_a_reason(gated):
    engine, _ = _setup(answerer=_CountingAnswerer(finish_reason="MAX_TOKENS"))
    body = _post({"query": "trample"}).json()
    assert body["answer"] == "Yes [1]."
    assert body["answer_complete"] is False
    assert list_history(engine)[0]["error"] == "answer cut off (finish reason: MAX_TOKENS)"
    assert _outcomes(engine) == ["generated"]


def test_reservation_is_pending_while_the_answer_is_written(gated):
    engine = memory_engine()
    seen = []

    class _Peeking(StreamsFromGenerate):
        def generate(self, query, context):
            seen.extend(_rows(engine))
            return Generation("Yes [1].", finish_reason="STOP")

    _setup(answerer=_Peeking(), engine=engine)
    _post({"query": "trample"})
    assert [r["outcome"] for r in seen] == ["pending"]
    assert seen[0]["cost_usd"] > 0
    assert _outcomes(engine) == ["generated"]  # the same row, finalized


def test_failure_after_partial_text_keeps_the_text_and_estimates_cost(gated):
    answerer = _ChunksAnswerer([StreamChunk(text="Yes, it does [1")], RuntimeError("reset"))
    engine, _ = _setup(answerer=answerer)
    body = _post({"query": "trample"}).json()
    assert body["answer"] == "Yes, it does [1"
    assert body["answer_complete"] is False
    (row,) = _rows(engine)
    assert row["outcome"] == "error"
    assert row["cost_usd"] > 0  # estimated: no usage ever arrived
    assert list_history(engine)[0]["error"] == "reset"
    assert body["cached_at"] is None


def test_failure_with_no_text_costs_nothing_and_has_no_answer(gated):
    engine, _ = _setup(answerer=_ChunksAnswerer([], RuntimeError("Gemini 429")))
    body = _post({"query": "trample"}).json()
    assert body["answer"] is None
    assert body["answer_complete"] is None
    (row,) = _rows(engine)
    assert (row["outcome"], row["cost_usd"]) == ("error", 0.0)
```

Add `from mtg_api.llm import Generation, StreamChunk` (replacing the existing `Generation` import).

- [ ] **Step 3: Run the tests and confirm they fail.**

Run: `cd mtg-api && pytest tests/test_gating_flow.py -q`
Expected: the new tests fail (`answer_complete` KeyError, or no `pending` seen). The old tests still pass, because `generate()` on the fakes still works.

- [ ] **Step 4: Add `answer_complete`** to `QueryResponse` in `models.py`, after `citation_stats`:

```python
    # True when Gemini finished the answer, False when it stopped early
    # (token limit, safety, timeout, dropped stream); None without an answer.
    answer_complete: bool | None = None
```

- [ ] **Step 5: Rebuild the endpoint in `main.py`.** Imports: add `from collections.abc import Callable, Iterator` and `from dataclasses import dataclass`, and `from fastapi.encoders import jsonable_encoder`. Add `StreamAccumulator, prompt_chars` to the `mtg_api.llm` import. Add `from mtg_api.streaming import AnswerJob, Emit, GenerationSlots`. Add `estimate_generation, finalize_usage, reserve_usage, worst_case_cost` to the `mtg_api.usage` import. Add `Citation` to the `mtg_api.models` import, and `from mtg_api.citations import cite_answer, citation_for` (see below).

In `citations.py`, rename `_citation` to `citation_for` (it is public now: the stream sends one per source) and update its one caller in `build_citations`.

Replace the whole `@app.post("/api/v1/query") def query(...)` function (keep `_DEGRADED_OUTCOMES`, `_PER_REQUEST_FIELDS`, `_gate`, `_record`, `_cache_get`, `_cache_put`, `_save` as they are) with:

```python
generation_slots = GenerationSlots()


@dataclass
class QueryDeps:
    matcher: CardMatcher
    keyword_matcher: KeywordMatcher
    dense_embedder: Embedder
    sparse_embedder: SparseEmbedder
    client: QdrantClient
    answerer: GeminiAnswerer
    engine: Engine
    rules_index: RulesIndex
    data_version: str


def get_query_deps(
    matcher: CardMatcher = Depends(get_card_matcher),
    keyword_matcher: KeywordMatcher = Depends(get_keyword_matcher),
    dense_embedder: Embedder = Depends(get_dense_embedder),
    sparse_embedder: SparseEmbedder = Depends(get_sparse_embedder),
    client: QdrantClient = Depends(get_qdrant_client),
    answerer: GeminiAnswerer = Depends(get_answerer),
    engine: Engine = Depends(get_db_engine),
    rules_index: RulesIndex = Depends(get_rules_index),
    data_version: str = Depends(get_data_version),
) -> QueryDeps:
    return QueryDeps(
        matcher,
        keyword_matcher,
        dense_embedder,
        sparse_embedder,
        client,
        answerer,
        engine,
        rules_index,
        data_version,
    )


@dataclass
class _Started:
    """Phase 1's result: the `results` event, and the events after it
    (always ending with `done`)."""

    head: dict
    rest: Iterator[tuple[str, dict]]


# Per-request fields: in `results`, not repeated in `done`.
_HEAD_ONLY = ("degraded", "answers_remaining", "cached_at")


def _head(response: QueryResponse, sources: list[Citation]) -> dict:
    data = jsonable_encoder(response)
    return {
        "results": data["results"],
        **{k: data[k] for k in _HEAD_ONLY},
        "sources": jsonable_encoder(sources),
    }


def _done(response: QueryResponse) -> dict:
    data = jsonable_encoder(response)
    return {k: v for k, v in data.items() if k != "query" and k not in _HEAD_ONLY}
```

Then the retrieval, moved unchanged out of the old `query()`. Its body is the old lines from `card_results = [` through `all_results = (...)`, with `matcher` → `d.matcher`, `keyword_matcher` → `d.keyword_matcher`, `client` → `d.client`, `dense_embedder` → `d.dense_embedder`, `sparse_embedder` → `d.sparse_embedder`, and `request.query` → `query`:

```python
def _retrieve(query: str, s: Settings, d: QueryDeps) -> list[QueryResult]:
    """Card-name, card-ruling, keyword-rule and hybrid vector matches, in
    context order."""
    card_results = [
        # ... the old body, renamed as above ...
    ]
    # ...
    return (
        card_results + card_ruling_results + keyword_results + rule_search_results + vector_results
    )
```

Next, the worker's inputs and phase 2:

```python
@dataclass
class _AnswerWork:
    """What the worker thread needs to write, record, cache and save one
    answer, whether or not anyone is still listening."""

    s: Settings
    request: QueryRequest
    answerer: GeminiAnswerer
    d: QueryDeps
    context: str
    sources: dict[int, QueryResult]
    results: list[QueryResult]
    record: dict  # now / ip_bucket / is_admin / model for the usage row
    tracked: bool
    reservation: int | None
    key: str | None
    answers_remaining: int | None
    eval_fields: dict
    release: Callable[[], None] | None


def _finalize(work: _AnswerWork, outcome: str, generation: Generation) -> None:
    if not work.tracked:
        return
    cost = cost_usd(generation, work.s)
    if work.reservation is None:
        # The reservation insert failed; record the answer as before.
        _record(work.d.engine, outcome=outcome, generation=generation, cost=cost, **work.record)
        return
    try:
        finalize_usage(
            work.d.engine, work.reservation, outcome=outcome, generation=generation, cost=cost
        )
    except Exception:
        logger.exception("Failed to finalize LLM usage")


def _run_answer(work: _AnswerWork, emit: Emit) -> None:
    """Phase 2, on the job's thread: stream the answer out as deltas, then
    do everything the old endpoint did after generating."""
    s, d = work.s, work.d
    acc = StreamAccumulator()
    failure = None
    try:
        emit("thinking", {})
        try:
            for chunk in work.answerer.stream(work.request.query, work.context):
                acc.add(chunk)
                if chunk.text:
                    emit("delta", {"text": chunk.text})
        except Exception as exc:
            logger.exception("Answer generation failed (%s)", generator_label(s))
            failure = str(exc)

        generation = estimate_generation(acc, prompt_chars(work.request.query, work.context), s)
        _finalize(work, "error" if failure else "generated", generation)

        # Keep whatever was written, even when the stream failed after it.
        answer = acc.text if (acc.text or failure is None) else None
        complete = failure is None and acc.finish_reason == "STOP"
        error = failure
        if answer and not complete and error is None:
            error = f"answer cut off (finish reason: {acc.finish_reason or 'none'})"

        # Must run before the results are dumped: it sets each cited
        # result's `cited` flag.
        cited = cite_answer(answer, work.sources, d.rules_index) if answer is not None else None
        if cited is not None:
            answer = cited.answer
        citations = cited.citations if cited else []
        enrich_citations(citations, d.matcher, d.rules_index)
        rule_references = cited.rule_references if cited else []
        citation_stats = cited.stats if cited else CitationStats()

        _save(
            d.engine,
            s,
            work.request,
            answer=answer,
            results=[r.model_dump() for r in work.results],
            error=error,
            citations=[c.model_dump() for c in citations],
            citation_stats=citation_stats.model_dump(),
            rule_references=rule_references,
        )

        eval_fields = dict(work.eval_fields)
        if settings.eval_mode:
            eval_fields["usage"] = None if failure else generation.usage()
        response = QueryResponse(
            query=work.request.query,
            results=work.results,
            answer=answer,
            citations=citations,
            rule_references=rule_references,
            citation_stats=citation_stats,
            answer_complete=None if answer is None else complete,
            answers_remaining=work.answers_remaining,
            **eval_fields,
        )
        if work.key and answer and complete:
            _cache_put(d.engine, work.key, work.request.query, response, work.record["now"])
        if failure and answer is None:
            emit("error", {"message": failure})
        emit("done", _done(response))
    finally:
        if work.release:
            work.release()
```

Then phase 1:

```python
def _reserve(engine: Engine, record: dict, cost: float) -> int | None:
    try:
        return reserve_usage(engine, cost=cost, **record)
    except Exception:
        logger.exception("Failed to reserve LLM usage")
        return None


def _start_query(request: QueryRequest, http_request: Request, d: QueryDeps) -> _Started:
    """Phase 1, in the request thread: everything that can still refuse the
    request with an HTTP status, then retrieval. Starts the answer job when
    there is an answer to write."""
    s = resolve_settings(request.overrides)
    answerer = d.answerer
    if GENERATION_SETTINGS & request.overrides.keys():
        answerer = build_answerer(s)

    if len(request.query) > s.max_query_chars:
        raise HTTPException(
            status_code=422, detail=f"query is longer than {s.max_query_chars} characters"
        )
    admin = is_admin(http_request)
    if request.fresh and not (admin and has_admin_marker(http_request)):
        raise HTTPException(status_code=403, detail="fresh answers are admin-only")

    engine = d.engine
    now = datetime.now(UTC)
    bucket = ip_bucket(http_request.client.host if http_request.client else "unknown")
    tracked = request.generate and not settings.eval_mode
    record = {"now": now, "ip_bucket": bucket, "is_admin": admin, "model": s.gemini_model}

    gate = Gate(None, 0)
    remaining = None
    if tracked and s.gating_enabled and not admin:
        gate = _gate(engine, s, bucket, now)
        remaining = gate.answers_remaining

    key = cache_key(request.query, s, d.data_version) if tracked and s.answer_cache_enabled else None
    hit = _cache_get(engine, key) if key and not request.fresh else None
    if hit is not None:
        stored, generated_at = hit
        # The global budget being exhausted applies to the next new
        # question too, so don't promise answers a cache hit didn't use.
        cache_remaining = 0 if gate.degraded == "global_budget" else remaining
        try:
            cached_response = QueryResponse(
                **stored,
                query=request.query,
                cached_at=generated_at,
                answers_remaining=cache_remaining,
            )
        except Exception:
            # A row from an older schema (or otherwise malformed): fall
            # through to a normal retrieval + generation below, which
            # overwrites this entry via _cache_put.
            logger.exception("Malformed answer-cache row for key %s; treating as a miss", key)
        else:
            # Only complete answers are cached; rows from before the field.
            if cached_response.answer_complete is None and cached_response.answer:
                cached_response.answer_complete = True
            # Rows cached before enrichment existed lack card/heading.
            enrich_results(cached_response.results, d.matcher, d.rules_index)
            enrich_citations(cached_response.citations, d.matcher, d.rules_index)
            _record(engine, outcome="cached", **record)
            _save(
                engine,
                s,
                request,
                answer=cached_response.answer,
                results=[r.model_dump() for r in cached_response.results],
                error=None,
                citations=[c.model_dump() for c in cached_response.citations],
                citation_stats=cached_response.citation_stats.model_dump(),
                rule_references=cached_response.rule_references,
                cached=True,
            )
            return _Started(_head(cached_response, []), iter([("done", _done(cached_response))]))

    generating = request.generate and gate.degraded is None
    release = None
    if generating and not settings.eval_mode:
        release = generation_slots.acquire(
            bucket,
            total_limit=s.max_concurrent_generations,
            ip_limit=None if admin else s.max_concurrent_generations_per_ip,
        )
        if release is None:
            raise HTTPException(
                status_code=429, detail="Too many answers in progress. Try again in a moment."
            )

    try:
        all_results = _retrieve(request.query, s, d)
        context, sources = build_context(all_results)
        # After build_context: display data must never reach the LLM.
        enrich_results(all_results, d.matcher, d.rules_index)

        eval_fields = {}
        if settings.eval_mode:
            eval_fields = {
                "context_hash": hashlib.sha256(context.encode("utf-8")).hexdigest(),
                "prompt_version": PROMPT_VERSION,
                "generator": generator_label(s),
            }

        if tracked and gate.degraded:
            _record(engine, outcome=_DEGRADED_OUTCOMES[gate.degraded], **record)
        if generating and tracked and remaining is not None:
            remaining = max(0, remaining - 1)

        head_response = QueryResponse(
            query=request.query,
            results=all_results,
            degraded=gate.degraded,
            answers_remaining=remaining,
            **eval_fields,
        )
        if not generating:
            _save(
                engine,
                s,
                request,
                answer=None,
                results=[r.model_dump() for r in all_results],
                error=None,
                citations=[],
                citation_stats=CitationStats().model_dump(),
                rule_references=[],
            )
            return _Started(_head(head_response, []), iter([("done", _done(head_response))]))

        live_sources = [citation_for(n, r) for n, r in sources.items()]
        enrich_citations(live_sources, d.matcher, d.rules_index)
        reservation = (
            _reserve(engine, record, worst_case_cost(prompt_chars(request.query, context), s))
            if tracked
            else None
        )
        work = _AnswerWork(
            s=s,
            request=request,
            answerer=answerer,
            d=d,
            context=context,
            sources=sources,
            results=all_results,
            record=record,
            tracked=tracked,
            reservation=reservation,
            key=key,
            answers_remaining=remaining,
            eval_fields=eval_fields,
            release=release,
        )
        job = AnswerJob(lambda emit: _run_answer(work, emit)).start()
    except BaseException:
        if release:
            release()
        raise
    return _Started(_head(head_response, live_sources), job.events())


@app.post("/api/v1/query", response_model=QueryResponse)
def query(
    request: QueryRequest, http_request: Request, d: QueryDeps = Depends(get_query_deps)
) -> QueryResponse:
    started = _start_query(request, http_request, d)
    fields = {k: v for k, v in started.head.items() if k != "sources"}
    for name, data in started.rest:
        if name == "done":
            fields.update(data)
    return QueryResponse(query=request.query, **fields)
```

Check the old history-save behavior before you move on. The old endpoint saved one history row for a degraded or `generate: false` request (`answer=None`), and one after generation. The code above keeps both. The old `citations` value for "no answer" was `[]`, and `citation_stats` was `CitationStats().model_dump()`. Match those exactly.

- [ ] **Step 6: Run the whole suite.**

Run: `cd mtg-api && pytest -q`
Expected: all pass, the new Task 4 tests included. If `test_query.py` or `test_eval_mode.py` fail, compare the old endpoint (`git show HEAD:mtg-api/src/mtg_api/main.py`) line by line with `_start_query` / `_run_answer`. The behavior must match apart from `answer_complete`.

- [ ] **Step 7: Lint and commit.**

```bash
cd mtg-api && ruff check . && ruff format . && cd ..
git add mtg-api/src/mtg_api/main.py mtg-api/src/mtg_api/models.py mtg-api/src/mtg_api/citations.py mtg-api/tests/conftest.py mtg-api/tests/test_query.py mtg-api/tests/test_gating_flow.py mtg-api/tests/test_eval_mode.py
git commit -m "refactor(api): two-phase query pipeline with answers written on a worker thread

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: The SSE endpoint, caps, and replay's `answer_complete`

**Files:**
- Modify: `mtg-api/src/mtg_api/main.py` (the new endpoint after `query`; `get_query_replay`)
- Test: `mtg-api/tests/test_query_stream.py` (new), `mtg-api/tests/test_queries_endpoint.py`

**Interfaces:**
- Consumes: `_start_query`, `_Started`, `QueryDeps`, `generation_slots` (Task 4); `sse_event`, `join_all` (Task 3).
- Produces: `POST /api/v1/query/stream`; `main._sse(started: _Started) -> Iterator[str]`.

- [ ] **Step 1: Write the failing tests** in `mtg-api/tests/test_query_stream.py`:

```python
import json

import pytest
from conftest import admin_client, memory_engine
from fastapi.testclient import TestClient
from sqlalchemy import select
from starlette.requests import Request
from test_gating_flow import _ChunksAnswerer, _CountingAnswerer, _setup
from test_query import _FakeDenseModel, _FakeHit, _FakeQdrantClient, _FakeSparseModel

from mtg_api import main
from mtg_api.card_matcher import CardMatcher
from mtg_api.embedder import Embedder
from mtg_api.history import list_history
from mtg_api.keyword_matcher import KeywordMatcher
from mtg_api.llm import StreamChunk
from mtg_api.main import app
from mtg_api.models import QueryRequest
from mtg_api.rules_index import RulesIndex
from mtg_api.sparse_embedder import SparseEmbedder
from mtg_api.streaming import join_all
from mtg_api.usage import llm_usage


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def _events(resp):
    out = []
    for frame in resp.text.strip().split("\n\n"):
        name_line, data_line = frame.split("\n")
        out.append((name_line.removeprefix("event: "), json.loads(data_line[len("data: ") :])))
    return out


def _stream(json_body, client=None):
    return (client or TestClient(app)).post("/api/v1/query/stream", json=json_body)


def test_stream_sends_results_thinking_deltas_then_done():
    answerer = _ChunksAnswerer(
        [StreamChunk(text="Yes "), StreamChunk(text="[1].", finish_reason="STOP")]
    )
    _setup(answerer=answerer)
    resp = _stream({"query": "trample"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert resp.headers["x-accel-buffering"] == "no"
    events = _events(resp)
    assert [name for name, _ in events] == ["results", "thinking", "delta", "delta", "done"]
    head, done = events[0][1], events[-1][1]
    assert [s["number"] for s in head["sources"]] == [1]
    assert head["sources"][0]["url"] == "/rules/702.19b"
    assert head["results"][0]["cited"] is False
    assert [e[1]["text"] for e in events if e[0] == "delta"] == ["Yes ", "[1]."]
    assert done["answer"] == "Yes [1]."
    assert done["answer_complete"] is True
    assert done["results"][0]["cited"] is True
    assert "degraded" not in done


def test_cache_hit_streams_results_then_done():
    _setup()
    _stream({"query": "trample"})
    events = _events(_stream({"query": "trample"}))
    assert [name for name, _ in events] == ["results", "done"]
    assert events[0][1]["cached_at"] is not None
    assert events[1][1]["answer"] == "Yes [1]."


def test_degraded_request_streams_results_then_done(monkeypatch):
    monkeypatch.setattr(main.settings, "gating_enabled", True)
    monkeypatch.setattr(main.settings, "ip_daily_llm_limit", 0)
    _setup()
    events = _events(_stream({"query": "trample"}))
    assert [name for name, _ in events] == ["results", "done"]
    assert events[0][1]["degraded"] == "ip_quota"
    assert events[1][1]["answer"] is None


def test_failure_with_no_text_sends_error_then_done():
    _setup(answerer=_ChunksAnswerer([], RuntimeError("Gemini 500")))
    events = _events(_stream({"query": "trample"}))
    assert [name for name, _ in events] == ["results", "thinking", "error", "done"]
    assert events[2][1] == {"message": "Gemini 500"}
    assert events[3][1]["answer"] is None


def test_validation_errors_are_plain_http():
    _setup()
    assert _stream({"query": "x" * 501}).status_code == 422
    assert _stream({"query": "trample", "fresh": True}).status_code == 403


def test_second_answer_from_one_ip_is_429_while_one_is_in_progress():
    _setup()
    release = main.generation_slots.acquire("testclient", total_limit=4, ip_limit=1)
    try:
        assert _stream({"query": "trample"}).status_code == 429
    finally:
        release()
    assert _stream({"query": "trample"}).status_code == 200


def test_global_cap_is_429(monkeypatch):
    monkeypatch.setattr(main.settings, "max_concurrent_generations", 0)
    _setup()
    assert _stream({"query": "trample"}).status_code == 429


def test_cache_hits_and_retrieval_only_need_no_slot(monkeypatch):
    _setup()
    _stream({"query": "trample"})
    monkeypatch.setattr(main.settings, "max_concurrent_generations", 0)
    assert _stream({"query": "trample"}).status_code == 200  # cache hit
    assert _stream({"query": "other", "generate": False}).status_code == 200


def test_eval_mode_skips_the_caps(monkeypatch):
    monkeypatch.setattr(main.settings, "eval_mode", True)
    monkeypatch.setattr(main.settings, "max_concurrent_generations", 0)
    _setup()
    assert _stream({"query": "trample"}).status_code == 200


def test_admin_skips_the_per_ip_cap_but_not_the_global_one(monkeypatch):
    _setup()
    client = admin_client(monkeypatch)
    release = main.generation_slots.acquire("testclient", total_limit=4, ip_limit=1)
    try:
        assert _stream({"query": "trample"}, client).status_code == 200
    finally:
        release()
    monkeypatch.setattr(main.settings, "max_concurrent_generations", 0)
    assert _stream({"query": "trample"}, client).status_code == 429


def test_closing_the_stream_early_still_finishes_the_answer():
    engine = memory_engine()
    hits = [_FakeHit("p1", 1.0, {"source_type": "rule", "rule_id": "702.19b", "text": "T."})]
    deps = main.QueryDeps(
        matcher=CardMatcher([]),
        keyword_matcher=KeywordMatcher([]),
        dense_embedder=Embedder(_FakeDenseModel()),
        sparse_embedder=SparseEmbedder(_FakeSparseModel()),
        client=_FakeQdrantClient(hits),
        answerer=_CountingAnswerer(),
        engine=engine,
        rules_index=RulesIndex([]),
        data_version="v1",
    )
    http_request = Request(
        {"type": "http", "method": "POST", "path": "/", "headers": [], "client": ("1.2.3.4", 1)}
    )
    frames = main._sse(main._start_query(QueryRequest(query="trample"), http_request, deps))
    assert next(frames).startswith("event: results")
    frames.close()  # the visitor left
    join_all()
    with engine.connect() as conn:
        outcomes = [r.outcome for r in conn.execute(select(llm_usage))]
    assert outcomes == ["generated"]
    assert list_history(engine)[0]["answer"] == "Yes [1]."
```

Append to `mtg-api/tests/test_queries_endpoint.py`:

```python
def test_replay_marks_a_cut_off_answer_incomplete(monkeypatch):
    engine = memory_engine()
    save_history(
        engine,
        query="q",
        answer="Half an",
        results=[],
        model="m",
        error="answer cut off (finish reason: MAX_TOKENS)",
    )
    save_history(engine, query="q2", answer="Whole.", results=[], model="m", error=None)
    assert _replay(monkeypatch, engine, 1).json()["answer_complete"] is False
    assert _replay(monkeypatch, engine, 2).json()["answer_complete"] is True
```

- [ ] **Step 2: Run the tests and confirm they fail.**

Run: `cd mtg-api && pytest tests/test_query_stream.py tests/test_queries_endpoint.py -q`
Expected: 404s from the missing route, `AttributeError: _sse`, and a replay `answer_complete` of `None`.

- [ ] **Step 3: Implement.** In `main.py`, add `from fastapi.responses import StreamingResponse` and `from mtg_api.streaming import sse_event` (merged into the existing streaming import). After `query()`, add:

```python
def _sse(started: _Started) -> Iterator[str]:
    """Relays the answer job as SSE. Closing this (the client left) only
    stops the relay; the job finishes on its own thread."""
    yield sse_event("results", started.head)
    for name, data in started.rest:
        yield sse_event(name, data)


@app.post("/api/v1/query/stream")
def query_stream(
    request: QueryRequest, http_request: Request, d: QueryDeps = Depends(get_query_deps)
) -> StreamingResponse:
    # Phase 1 runs here, before any byte is sent, so a 422/403/429 is a
    # plain HTTP error.
    started = _start_query(request, http_request, d)
    return StreamingResponse(
        _sse(started),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
```

In `get_query_replay`, add to the `ReplayResponse(...)` call:

```python
        # No stored finish reason; a cut-off answer is the only row with
        # both an answer and an error.
        answer_complete=not row["error"],
```

- [ ] **Step 4: Run the tests and confirm they pass.**

Run: `cd mtg-api && pytest -q && ruff check . && ruff format --check .`
Expected: all pass.

- [ ] **Step 5: Commit.**

```bash
git add mtg-api/src/mtg_api/main.py mtg-api/tests/test_query_stream.py mtg-api/tests/test_queries_endpoint.py
git commit -m "feat(api): POST /api/v1/query/stream with generation caps

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: nginx passthrough and API docs

**Files:**
- Modify: `mtg-web/nginx.conf` (new location before `location = /api/v1/auth/login`)
- Modify: `README.md` (the Backend API table and response notes)

- [ ] **Step 1: Add the nginx location.** Insert after the `location /api/ { ... }` block:

```nginx
    # Streamed answers: pass each event straight through. The read timeout
    # is per read, so 90s covers the longest gap between events, not the
    # whole answer.
    location = /api/v1/query/stream {
        limit_req zone=api burst=5 nodelay;
        limit_conn api_conn 2;
        proxy_pass http://backend:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto $forwarded_proto;
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 90s;
    }
```

- [ ] **Step 2: Check the config parses.**

Run: `docker run --rm -v "$PWD/mtg-web/nginx.conf:/etc/nginx/conf.d/default.conf:ro" --add-host backend:127.0.0.1 nginx:alpine nginx -t`
Expected: `syntax is ok` / `test is successful`.

- [ ] **Step 3: Update `README.md`.** Add a row after `/api/v1/query` in the Backend API table:

```markdown
| `/api/v1/query/stream` | POST | same as `/api/v1/query` | The same answer as Server-Sent Events: `results` (retrieved sources plus `sources`, a citation for every numbered source), `thinking`, `delta` (`{"text"}`) per chunk, `error` (`{"message"}`, only when generation failed with no text), then always `done` (the rest of the `/api/v1/query` fields). 422/403/429 come back as plain HTTP errors before the stream starts; 429 also when too many answers are being written (4 site-wide, 1 per visitor). The answer keeps generating, and is recorded, cached and saved, if the client disconnects. |
```

In the `/api/v1/query` response field list, add:

```markdown
- `answer_complete` — `true` when Gemini finished the answer, `false` when
  it stopped early (token limit, safety block, timeout, dropped stream; the
  partial text is kept), `null` without an answer.
```

- [ ] **Step 4: Commit.**

```bash
git add mtg-web/nginx.conf README.md
git commit -m "feat(web): pass the answer stream through nginx unbuffered; document it

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: SSE parser and `streamQuery`

**Files:**
- Create: `mtg-web/src/lib/sse.ts`, `mtg-web/src/lib/sse.test.ts`
- Modify: `mtg-web/src/lib/api.ts`
- Test: `mtg-web/src/lib/api.test.ts` (new)

**Interfaces:**
- Produces:
  - `class SseParser { push(chunk: string): SseEvent[] }`, where `SseEvent = { event: string; data: string }`
  - In `api.ts`: `QueryResponse.answer_complete: boolean | null`, `StreamHead`, `StreamDone`, `StreamHandlers`, `StreamEndedError`, and `streamQuery(query: string, opts: { fresh?: boolean; signal?: AbortSignal }, on: StreamHandlers): Promise<void>`

- [ ] **Step 1: Write the failing tests.** `mtg-web/src/lib/sse.test.ts`:

```ts
import { describe, expect, it } from 'vitest';
import { SseParser } from './sse';

describe('SseParser', () => {
  it('parses several events in one chunk', () => {
    const p = new SseParser();
    expect(p.push('event: a\ndata: {"x":1}\n\nevent: b\ndata: {}\n\n')).toEqual([
      { event: 'a', data: '{"x":1}' },
      { event: 'b', data: '{}' }
    ]);
  });

  it('keeps an unfinished event for the next chunk', () => {
    const p = new SseParser();
    expect(p.push('event: delta\nda')).toEqual([]);
    expect(p.push('ta: {"text":"hi"}\n')).toEqual([]);
    expect(p.push('\n')).toEqual([{ event: 'delta', data: '{"text":"hi"}' }]);
  });

  it('defaults the event name to message and skips data-less blocks', () => {
    const p = new SseParser();
    expect(p.push(': comment\n\ndata: 1\n\n')).toEqual([{ event: 'message', data: '1' }]);
  });
});
```

`mtg-web/src/lib/api.test.ts`:

```ts
import { afterEach, describe, expect, it, vi } from 'vitest';
import { RateLimitedError, StreamEndedError, streamQuery, type StreamHandlers } from './api';

function sseResponse(chunks: string[], status = 200): Response {
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      const enc = new TextEncoder();
      for (const c of chunks) controller.enqueue(enc.encode(c));
      controller.close();
    }
  });
  return new Response(body, { status, headers: { 'content-type': 'text/event-stream' } });
}

function recorder() {
  const calls: [string, unknown][] = [];
  const on: StreamHandlers = {
    results: (h) => calls.push(['results', h]),
    thinking: () => calls.push(['thinking', null]),
    delta: (t) => calls.push(['delta', t]),
    error: (m) => calls.push(['error', m]),
    done: (d) => calls.push(['done', d])
  };
  return { calls, on };
}

afterEach(() => vi.unstubAllGlobals());

describe('streamQuery', () => {
  it('dispatches each event, across chunk boundaries', async () => {
    const fetch = vi.fn(async () =>
      sseResponse([
        'event: results\ndata: {"results":[],"sources":[],"degraded":null,',
        '"answers_remaining":null,"cached_at":null}\n\nevent: thinking\ndata: {}\n\n',
        'event: delta\ndata: {"text":"Yes"}\n\nevent: done\ndata: {"answer":"Yes"}\n\n'
      ])
    );
    vi.stubGlobal('fetch', fetch);
    const { calls, on } = recorder();
    await streamQuery('q', {}, on);
    expect(calls.map((c) => c[0])).toEqual(['results', 'thinking', 'delta', 'done']);
    expect(calls[2][1]).toBe('Yes');
    expect(fetch).toHaveBeenCalledWith(
      '/api/v1/query/stream',
      expect.objectContaining({ method: 'POST', body: JSON.stringify({ query: 'q' }) })
    );
  });

  it('maps 429 to RateLimitedError before reading', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('', { status: 429 })));
    await expect(streamQuery('q', {}, recorder().on)).rejects.toBeInstanceOf(RateLimitedError);
  });

  it('rejects when the stream ends without done', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => sseResponse(['event: thinking\ndata: {}\n\n']))
    );
    await expect(streamQuery('q', {}, recorder().on)).rejects.toBeInstanceOf(StreamEndedError);
  });
});
```

- [ ] **Step 2: Run the tests and confirm they fail.**

Run: `cd mtg-web && npm test`
Expected: the imports fail (`./sse`, `streamQuery`).

- [ ] **Step 3: Implement.** `mtg-web/src/lib/sse.ts`:

```ts
// Splits a text/event-stream into events as chunks arrive. Our backend
// frames events as `event: <name>\ndata: <one-line json>\n\n`.

export interface SseEvent {
  event: string;
  data: string;
}

export class SseParser {
  private buffer = '';

  push(chunk: string): SseEvent[] {
    this.buffer += chunk;
    const events: SseEvent[] = [];
    let end: number;
    while ((end = this.buffer.indexOf('\n\n')) !== -1) {
      const block = this.buffer.slice(0, end);
      this.buffer = this.buffer.slice(end + 2);
      let event = 'message';
      const data: string[] = [];
      for (const line of block.split('\n')) {
        if (line.startsWith('event:')) event = line.slice(6).trim();
        else if (line.startsWith('data:')) data.push(line.slice(5).replace(/^ /, ''));
      }
      if (data.length) events.push({ event, data: data.join('\n') });
    }
    return events;
  }
}
```

In `api.ts`, add `import { SseParser } from './sse';` at the top. Add to `QueryResponse`, after `citation_stats`:

```ts
  // true: Gemini finished; false: it stopped early and this is the part
  // that was written; null: no answer.
  answer_complete: boolean | null;
```

After `submitQuery`, add:

```ts
// The first event of a streamed answer: everything but the answer.
export interface StreamHead {
  results: QueryResult[];
  // A citation for every numbered source, so markers work while streaming.
  sources: Citation[];
  degraded: QueryResponse['degraded'];
  answers_remaining: number | null;
  cached_at: string | null;
}

// The last event: the validated answer. `results` again, with `cited` set.
export type StreamDone = Omit<
  QueryResponse,
  'query' | 'degraded' | 'answers_remaining' | 'cached_at'
>;

export interface StreamHandlers {
  results: (head: StreamHead) => void;
  thinking: () => void;
  delta: (text: string) => void;
  // Generation failed with no text; `done` still follows.
  error: (message: string) => void;
  done: (done: StreamDone) => void;
}

export class StreamEndedError extends Error {}

export async function streamQuery(
  query: string,
  { fresh = false, signal }: { fresh?: boolean; signal?: AbortSignal },
  on: StreamHandlers
): Promise<void> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (fresh) headers[ADMIN_HEADER] = '1';
  const resp = await fetch(`${API_URL}/api/v1/query/stream`, {
    method: 'POST',
    headers,
    body: JSON.stringify(fresh ? { query, fresh } : { query }),
    signal
  });
  if (resp.status === 429) {
    throw new RateLimitedError('Too many requests. Wait a few seconds and try again.');
  }
  if (!resp.ok || !resp.body) {
    throw new Error(`query failed: ${resp.status}`);
  }
  const reader = resp.body.pipeThrough(new TextDecoderStream()).getReader();
  const parser = new SseParser();
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    for (const e of parser.push(value)) {
      if (signal?.aborted) return;
      const data = JSON.parse(e.data);
      if (e.event === 'results') on.results(data);
      else if (e.event === 'thinking') on.thinking();
      else if (e.event === 'delta') on.delta(data.text);
      else if (e.event === 'error') on.error(data.message);
      else if (e.event === 'done') return on.done(data);
    }
  }
  throw new StreamEndedError('The answer stopped arriving before it finished.');
}
```

Add `answer_complete: true` to `BASE` in `src/lib/fixtures/index.ts`. `retrievalOnly` and `uncited` spread `BASE`, so set `answer_complete: null` in `retrievalOnly` so its type stays honest.

- [ ] **Step 4: Run the tests and confirm they pass.**

Run: `cd mtg-web && npm run check && npm test`
Expected: pass.

- [ ] **Step 5: Commit.**

```bash
git add mtg-web/src/lib/sse.ts mtg-web/src/lib/sse.test.ts mtg-web/src/lib/api.ts mtg-web/src/lib/api.test.ts mtg-web/src/lib/fixtures/index.ts
git commit -m "feat(web): SSE parser and streamQuery client

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Live draft helpers

**Files:**
- Create: `mtg-web/src/lib/stream.ts`, `mtg-web/src/lib/stream.test.ts`
- Test: `mtg-web/src/lib/answer/layout.test.ts` (one added case)

**Interfaces:**
- Consumes: `Citation`, `QueryResult` (from `api.ts`).
- Produces: `visibleDraft(draft: string): string`, `liveCitations(draft: string, sources: Citation[]): Citation[]`, `liveResults(results: QueryResult[], citations: Citation[]): QueryResult[]`.

- [ ] **Step 1: Write the failing tests.** `mtg-web/src/lib/stream.test.ts`:

```ts
import { describe, expect, it } from 'vitest';
import type { Citation, QueryResult } from './api';
import { liveCitations, liveResults, visibleDraft } from './stream';

const source = (number: number): Citation => ({
  number,
  source_type: 'rule',
  title: `Rule ${number}`,
  rule_id: String(number),
  card_name: null,
  oracle_id: null,
  text: 't',
  url: null,
  published_at: null
});

const result = (title: string): QueryResult => ({
  source: 'rule',
  title,
  text: 't',
  score: 1
});

describe('visibleDraft', () => {
  it('holds back an unfinished marker at the end', () => {
    expect(visibleDraft('Yes [1')).toBe('Yes ');
    expect(visibleDraft('Yes [1, ')).toBe('Yes ');
    expect(visibleDraft('Yes [')).toBe('Yes ');
  });

  it('keeps finished markers and ordinary text', () => {
    expect(visibleDraft('Yes [1].')).toBe('Yes [1].');
    expect(visibleDraft('Rule 702.19')).toBe('Rule 702.19');
  });
});

describe('liveCitations', () => {
  it('returns cited known sources once each, by number', () => {
    const sources = [source(1), source(2), source(3)];
    expect(liveCitations('A [3]. B [1, 3]. C [9]. D [2', sources).map((c) => c.number)).toEqual([
      1, 3
    ]);
  });
});

describe('liveResults', () => {
  it('marks result i as cited when source i + 1 is cited', () => {
    const out = liveResults([result('a'), result('b')], [source(2)]);
    expect(out.map((r) => r.cited)).toEqual([false, true]);
  });
});
```

In `mtg-web/src/lib/answer/layout.test.ts`, add inside the top-level `describe` (or at the top level, matching the file's style):

```ts
it('lays out a draft whose last sentence has not finished', () => {
  const pieces = assignSentences(segmentAnswer('Yes, one each. Trample then assigns', new Set(), new Set()));
  const layout = layoutAnswer(pieces);
  expect(layout.lead).not.toBeNull();
  expect(layout.paragraphs.flat().map((p) => (p.kind === 'text' ? p.text : '')).join('')).toBe(
    'Trample then assigns'
  );
});
```

Import `segmentAnswer` from `'../segments'` if the file doesn't already.

- [ ] **Step 2: Run the tests and confirm they fail.**

Run: `cd mtg-web && npm test`
Expected: `./stream` fails to import. The layout case should already pass. If it doesn't, fix `layoutAnswer` so an unfinished last sentence is kept as body text.

- [ ] **Step 3: Implement** `mtg-web/src/lib/stream.ts`:

```ts
// Helpers for showing an answer while it streams. The server validates
// citations only when the answer is done; until then markers link to the
// `sources` sent up front, and `done` replaces all of this.
import type { Citation, QueryResult } from './api';

// An opening bracket at the very end that hasn't closed yet: "[", "[1", "[1, ".
const OPEN_MARKER = /\[[\d,\s]*$/;
const MARKER = /\[(\s*\d+(?:\s*,\s*\d+)*\s*)\]/g;

/** The draft as it can be shown: an unfinished citation marker at the end
 * waits until it closes, so it never flashes up as "[1". */
export function visibleDraft(draft: string): string {
  return draft.replace(OPEN_MARKER, '');
}

/** The known sources the draft cites so far, ordered by number like the
 * server's list. Unknown numbers stay plain text until `done` removes them. */
export function liveCitations(draft: string, sources: Citation[]): Citation[] {
  const byNumber = new Map(sources.map((s) => [s.number, s]));
  const cited = new Set<number>();
  for (const match of draft.matchAll(MARKER)) {
    for (const part of match[1].split(',')) {
      const n = parseInt(part.trim(), 10);
      if (byNumber.has(n)) cited.add(n);
    }
  }
  return [...cited].sort((a, b) => a - b).map((n) => byNumber.get(n)!);
}

/** Results with `cited` set from the live citations (result i is source
 * i + 1), so a source isn't listed as both cited and "also retrieved". */
export function liveResults(results: QueryResult[], citations: Citation[]): QueryResult[] {
  const numbers = new Set(citations.map((c) => c.number));
  return results.map((r, i) => ({ ...r, cited: numbers.has(i + 1) }));
}
```

- [ ] **Step 4: Run the tests and confirm they pass.**

Run: `cd mtg-web && npm run check && npm test`
Expected: pass.

- [ ] **Step 5: Commit.**

```bash
git add mtg-web/src/lib/stream.ts mtg-web/src/lib/stream.test.ts mtg-web/src/lib/answer/layout.test.ts mtg-web/src/lib/answer/layout.ts
git commit -m "feat(web): live citations and draft helpers for streamed answers

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

(`layout.ts` only if it changed.)

---

### Task 9: Streaming dev fixtures

**Files:**
- Modify: `mtg-web/src/lib/fixtures/index.ts`
- Test: `mtg-web/src/lib/fixtures/fixtures.test.ts`

**Interfaces:**
- Consumes: `StreamHandlers`, `StreamHead`, `StreamDone`, `RateLimitedError` (Task 7); `liveCitations`, `visibleDraft` (Task 8).
- Produces: `mockStream(name: string, on: StreamHandlers, signal?: AbortSignal): Promise<void>`. `FIXTURE_NAMES` gains `'streaming'`, `'thinking'`, `'cutoff'`, `'streamerror'` and `'stalled'`.

- [ ] **Step 1: Write the failing tests.** Append to `fixtures.test.ts` (and import `mockStream` alongside `mockQuery`, plus `vi` from vitest):

```ts
function record() {
  const events: string[] = [];
  let text = '';
  let done: Record<string, unknown> | null = null;
  const on = {
    results: () => events.push('results'),
    thinking: () => events.push('thinking'),
    delta: (t: string) => {
      if (events[events.length - 1] !== 'delta') events.push('delta');
      text += t;
    },
    error: () => events.push('error'),
    done: (d: Record<string, unknown>) => {
      events.push('done');
      done = d;
    }
  };
  return { events, on, text: () => text, done: () => done };
}

describe('mockStream', () => {
  it.each([
    ['streaming', ['results', 'thinking', 'delta', 'done'], true],
    ['cutoff', ['results', 'thinking', 'delta', 'done'], false],
    ['streamerror', ['results', 'thinking', 'error', 'done'], null],
    ['answered', ['results', 'done'], true],
    ['quota', ['results', 'done'], null]
  ])('%s sends %j', async (name, expected, complete) => {
    vi.useFakeTimers();
    const r = record();
    const run = mockStream(name, r.on);
    await vi.runAllTimersAsync();
    await run;
    vi.useRealTimers();
    expect(r.events).toEqual(expected);
    expect((r.done() as { answer_complete: unknown } | null)?.answer_complete).toBe(complete);
  });

  it('streaming deltas add up to the answer', async () => {
    vi.useFakeTimers();
    const r = record();
    const run = mockStream('streaming', r.on);
    await vi.runAllTimersAsync();
    await run;
    vi.useRealTimers();
    expect(r.text()).toBe((r.done() as { answer: string }).answer);
  });

  it('stops when aborted', async () => {
    const controller = new AbortController();
    const r = record();
    const run = mockStream('streaming', r.on, controller.signal);
    controller.abort();
    await expect(run).rejects.toThrow();
    expect(r.events).not.toContain('done');
  });
});
```

- [ ] **Step 2: Run the tests and confirm they fail.**

Run: `cd mtg-web && npm test -- fixtures`
Expected: `mockStream` is not exported.

- [ ] **Step 3: Implement.** In `fixtures/index.ts`, extend the api import with `type StreamDone, type StreamHandlers, type StreamHead`, and add `import { liveCitations, visibleDraft } from '../stream';`. Add the new names to `FIXTURE_NAMES`: `'streaming', 'thinking', 'cutoff', 'streamerror', 'stalled'`. Append:

```ts
// Streamed versions of the fixtures, for the stream the page really reads.
// `streaming`, `thinking`, `cutoff`, `streamerror` and `stalled` write the
// worked example as it arrives; every other name answers at once, like a
// cache hit or a degraded request.

function wait(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(new DOMException('Aborted', 'AbortError'));
    const timer = setTimeout(resolve, ms);
    signal?.addEventListener(
      'abort',
      () => {
        clearTimeout(timer);
        reject(new DOMException('Aborted', 'AbortError'));
      },
      { once: true }
    );
  });
}

function headOf(r: QueryResponse, sources: Citation[]): StreamHead {
  return {
    results: r.results,
    sources,
    degraded: r.degraded,
    answers_remaining: r.answers_remaining,
    cached_at: r.cached_at
  };
}

function doneOf(r: QueryResponse): StreamDone {
  return {
    results: r.results,
    answer: r.answer,
    citations: r.citations,
    rule_references: r.rule_references,
    citation_stats: r.citation_stats,
    answer_complete: r.answer_complete
  };
}

// Word-ish pieces, the way Gemini's chunks break mid-marker and mid-word.
function pieces(text: string, size = 14): string[] {
  const out: string[] = [];
  for (let i = 0; i < text.length; i += size) out.push(text.slice(i, i + size));
  return out;
}

const STREAMED: Record<string, { thinkMs: number; stopAt: number | null; stall?: boolean }> = {
  streaming: { thinkMs: 600, stopAt: null },
  thinking: { thinkMs: 2500, stopAt: null },
  cutoff: { thinkMs: 600, stopAt: 0.6 },
  stalled: { thinkMs: 300, stopAt: 0.4, stall: true },
  streamerror: { thinkMs: 1200, stopAt: 0 }
};

export async function mockStream(
  name: string,
  on: StreamHandlers,
  signal?: AbortSignal
): Promise<void> {
  if (name === 'slow') return new Promise(() => {});
  if (name === 'error') throw new Error('query failed: 502');
  if (name === 'ratelimited') {
    throw new RateLimitedError('Too many requests. Wait a few seconds and try again.');
  }
  const plan = STREAMED[name];
  if (!plan) {
    const r = structuredClone((FIXTURES[name] ?? FIXTURES.answered)());
    await wait(400, signal);
    on.results(headOf(r, []));
    on.done(doneOf(r));
    return;
  }

  const base = structuredClone({ ...BASE, cached_at: null });
  await wait(300, signal);
  on.results(headOf({ ...base, results: uncitedResults }, base.citations));
  on.thinking();
  await wait(plan.thinkMs, signal);

  const full = base.answer!;
  const text = plan.stopAt === null ? full : full.slice(0, Math.floor(full.length * plan.stopAt));
  for (const piece of pieces(text)) {
    on.delta(piece);
    await wait(40, signal);
  }
  if (plan.stall) return new Promise(() => {});

  if (plan.stopAt === 0) {
    on.error('Gemini returned no answer: SAFETY');
    on.done(doneOf({ ...retrievalOnly, answer_complete: null }));
    return;
  }
  if (plan.stopAt === null) {
    on.done(doneOf(base));
    return;
  }
  const answer = visibleDraft(text).trimEnd();
  const citations = liveCitations(answer, base.citations);
  const cited = new Set(citations.map((c) => c.number));
  on.done(
    doneOf({
      ...base,
      answer,
      citations,
      rule_references: base.rule_references.filter((id) => answer.includes(id)),
      results: base.results.map((r, i) => ({ ...r, cited: cited.has(i + 1) })),
      citation_stats: { cited_count: citations.length, invalid_count: 0, uncited_answer: false },
      answer_complete: false
    })
  );
}
```

- [ ] **Step 4: Run the tests and confirm they pass.**

Run: `cd mtg-web && npm run check && npm test`
Expected: pass.

- [ ] **Step 5: Commit.**

```bash
git add mtg-web/src/lib/fixtures/index.ts mtg-web/src/lib/fixtures/fixtures.test.ts
git commit -m "feat(web): streaming dev fixtures

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Search page streams; cut-off notices; E2E

**Files:**
- Modify: `mtg-web/src/routes/+page.svelte`
- Modify: `mtg-web/src/routes/history/+page.svelte:111` (the chips)
- Create: `mtg-web/e2e/streaming.desktop.spec.ts`

**Interfaces:**
- Consumes: `streamQuery`, `StreamHandlers`, `Citation` (Task 7); `visibleDraft`, `liveCitations`, `liveResults` (Task 8); `mockStream` (Task 9).

- [ ] **Step 1: Write the failing E2E tests** in `mtg-web/e2e/streaming.desktop.spec.ts`:

```ts
import { expect, test, type Page } from '@playwright/test';

async function ask(page: Page, fixture: string) {
  // No backend: meta and admin status fall back to their defaults.
  await page.route('**/api/v1/**', (route) => route.fulfill({ status: 503, body: '' }));
  await page.goto(`/?mock=${fixture}`);
  const box = page.getByRole('textbox');
  await box.fill('Does trample plus deathtouch only need 1 damage on each blocker?');
  await box.press('Enter');
}

test('evidence and Thinking… show before any answer text', async ({ page }) => {
  await ask(page, 'thinking');
  await expect(page.getByText('Thinking…')).toBeVisible();
  await expect(page.getByRole('tab', { name: /^Retrieved · \d+$/ })).toBeVisible();
  await expect(page.getByText('Yes — each blocker')).toHaveCount(0);
  await expect(page.getByText('Yes — each blocker')).toBeVisible();
  await expect(page.getByText('Thinking…')).toHaveCount(0);
  await expect(page.getByRole('tab', { name: 'Cited · 5' })).toBeVisible();
});

test('a citation marker works before the answer finishes', async ({ page }) => {
  await ask(page, 'stalled');
  const marker = page.getByRole('link', { name: '1', exact: true });
  await expect(marker).toBeVisible();
  await marker.click();
  await expect(page.locator('#source-1')).toBeVisible();
});

test('a cut-off answer keeps its text and says so', async ({ page }) => {
  await ask(page, 'cutoff');
  await expect(page.getByText(/cut off before it finished/)).toBeVisible();
  await expect(page.getByText('Yes — each blocker')).toBeVisible();
});

test('a stream that fails with no text shows the no-answer notice', async ({ page }) => {
  await ask(page, 'streamerror');
  await expect(page.getByText("Couldn't write an answer this time")).toBeVisible();
});

test('asking again mid-stream replaces the answer instead of mixing them', async ({ page }) => {
  await ask(page, 'thinking');
  await expect(page.getByText('Thinking…')).toBeVisible();
  await page.getByRole('textbox').press('Enter');
  await expect(page.getByText('Cited · 5')).toBeVisible({ timeout: 10_000 });
  await expect(page.getByText('Yes — each blocker only needs 1 damage.')).toHaveCount(1);
});
```

- [ ] **Step 2: Run the tests and confirm they fail.**

Run: `cd mtg-web && npx playwright test e2e/streaming.desktop.spec.ts`
Expected: the tests fail (no "Thinking…" text, and the page still calls `mockQuery`).

- [ ] **Step 3: Wire the page.** In `routes/+page.svelte`:

Imports: replace `submitQuery` with `streamQuery`, and add `type Citation, type StreamHandlers` to the `$lib/api` import. Add `import { liveCitations, liveResults, visibleDraft } from '$lib/stream';`.

State: change the `View` type and add streaming state below `let selection`:

```ts
  type View = 'idle' | 'loading' | 'streaming' | 'result' | 'failed';
```

```ts
  // While an answer streams: the sources its markers may point to, the raw
  // text so far, and a copy of it refreshed at most once per frame.
  let sources = $state<Citation[]>([]);
  let draft = '';
  let shownDraft = $state('');
  let frame = 0;
  let controller: AbortController | null = null;
```

Replace the `notice` and `citedItems` deriveds with:

```ts
  const live = $derived(view === 'streaming');
  // What the page shows: the response, or while streaming, the draft with
  // live citations in place of the validated ones.
  const shown = $derived.by((): QueryResponse | null => {
    if (!response || !live) return response;
    const citations = liveCitations(shownDraft, sources);
    return {
      ...response,
      answer: visibleDraft(shownDraft),
      citations,
      results: liveResults(response.results, citations)
    };
  });
  const notice = $derived(shown && !live ? noticeFor(shown) : null);
  const citedItems = $derived(shown ? shown.citations.map(fromCitation) : []);
```

Replace `run()` with:

```ts
  function showDraftNextFrame() {
    if (frame) return;
    frame = requestAnimationFrame(() => {
      frame = 0;
      shownDraft = draft;
    });
  }

  async function run(
    q: string,
    fresh: boolean,
    signal: AbortSignal,
    progress: { results: boolean }
  ): Promise<void> {
    const on: StreamHandlers = {
      results: ({ sources: s, ...head }) => {
        progress.results = true;
        response = {
          query: q,
          ...head,
          answer: null,
          citations: [],
          rule_references: [],
          citation_stats: { cited_count: 0, invalid_count: 0, uncited_answer: false },
          answer_complete: null
        };
        sources = s;
        draft = '';
        shownDraft = '';
        view = 'streaming';
      },
      thinking: () => {},
      delta: (text) => {
        draft += text;
        showDraftNextFrame();
      },
      // `done` follows with no answer, which shows the "couldn't write" notice.
      error: () => {},
      done: (d) => {
        cancelAnimationFrame(frame);
        frame = 0;
        if (response) response = { ...response, ...d };
        view = 'result';
      }
    };
    if (import.meta.env.DEV && mock) {
      return (await import('$lib/fixtures')).mockStream(mock, on, signal);
    }
    return streamQuery(q, { fresh, signal }, on);
  }
```

In `ask()`, after the replay-ending block and before `const previous`, add:

```ts
    controller?.abort();
    const ctrl = (controller = new AbortController());
    const progress = { results: false };
```

Replace its `try/catch` with:

```ts
    try {
      await run(q, fresh, ctrl.signal, progress);
    } catch (e) {
      if (ctrl.signal.aborted) return;
      if (e instanceof RateLimitedError) {
        rateLimited = e.message;
        view = previous;
      } else if (progress.results && response) {
        // The stream broke after the evidence arrived: keep what was written.
        cancelAnimationFrame(frame);
        frame = 0;
        const answer = visibleDraft(draft).trimEnd() || null;
        const citations = answer ? liveCitations(answer, sources) : [];
        response = {
          ...response,
          answer,
          citations,
          results: liveResults(response.results, citations),
          answer_complete: answer ? false : null
        };
        view = 'result';
      } else {
        failure = e instanceof Error ? e.message : String(e);
        view = 'failed';
      }
    }
```

In `loadReplay`, add `controller?.abort();` as its first line.

Template: replace the two result branches (`{:else if response && notice}` through the end of the `{:else if response?.answer}` block) with:

```svelte
{:else if shown && notice}
  <main class="mx-auto flex max-w-[1280px] flex-col gap-6 px-4 py-6 sm:px-8 desk:px-12 desk:py-9">
    <StatusNotice kind={notice} remaining={shown.answers_remaining} />
    <MatchingSources results={shown.results} phone={phone.current} onopen={openSheet} />
  </main>
{:else if shown && (live || shown.answer)}
  <div class="desk:grid desk:grid-cols-[minmax(0,1fr)_460px] desk:items-start">
    <main
      class="flex flex-col gap-5 border-line px-[18px] py-5 max-desk:border-b sm:px-9 sm:py-8 desk:min-h-[calc(100vh-70px)] desk:border-r desk:px-12 desk:py-9"
    >
      <StatusChips
        response={shown}
        isAdmin={admin.isAdmin}
        loading={live}
        compact={phone.current}
        {replay}
        onfresh={() => ask(true)}
      />
      {#if shown.answer}
        <AnswerBody
          answer={shown.answer}
          citations={shown.citations}
          ruleReferences={shown.rule_references}
          {selection}
          canHover={hover.current}
          onselect={select}
        />
      {:else}
        <div role="status" class="flex items-center gap-2.5 font-mono text-[13px] text-fg-soft">
          <Icon name="spinner" size={16} class="animate-spin text-gold motion-reduce:animate-none" />
          Thinking…
        </div>
      {/if}
      {#if !live && shown.answer_complete === false}
        <div
          class="flex items-start gap-3 rounded-[10px] border border-caution-line bg-caution-bg px-4 py-3.5"
        >
          <Icon name="info" class="mt-0.5 shrink-0 text-caution" />
          <p class="m-0 text-sm leading-normal text-caution-fg">
            This answer was cut off before it finished, so it may be missing something. Check it
            against the passages {desk.current ? 'on the right' : 'below'}.
          </p>
        </div>
      {/if}
      {#if !live && shown.citation_stats.uncited_answer}
        <div
          class="flex items-start gap-3 rounded-[10px] border border-caution-line bg-caution-bg px-4 py-3.5"
        >
          <Icon name="info" class="mt-0.5 shrink-0 text-caution" />
          <p class="m-0 text-sm leading-normal text-caution-fg">
            This answer didn't point to any sources, so treat it with care. Check it against the
            passages {desk.current ? 'on the right' : 'below'}, which were retrieved for your
            question.
          </p>
        </div>
      {/if}
      <RulesReferenced ruleIds={shown.rule_references} boxed={desk.current} />
    </main>
    <div class="desk:sticky desk:top-0 desk:max-h-screen desk:overflow-y-auto">
      <EvidencePanel
        citations={shown.citations}
        results={shown.results}
        selectedNumber={selection?.number ?? null}
        layout={desk.current ? 'side' : phone.current ? 'list' : 'grid'}
        onopen={openSheet}
      />
    </div>
  </div>
{/if}
```

`replay` and `ReplayResponse` stay as they are. A replay's `response` goes through `shown` unchanged, because `live` is false for it.

In `routes/history/+page.svelte`, replace the error chip line:

```svelte
            {#if row.error && row.answer}{@render chip('cut off', 'caution')}{:else if row.error}{@render chip('error', 'danger')}{/if}
```

- [ ] **Step 4: Run everything.**

Run: `cd mtg-web && npm run check && npm test && npm run test:e2e`
Expected: all pass, the existing `smoke`/`zoom` specs included (the `images` fixture now arrives as `results` + `done`).

- [ ] **Step 5: Try it by hand.** Run `npm run dev` and open each of `/?mock=streaming`, `thinking`, `cutoff`, `streamerror`, `stalled`, `answered` and `quota`. Check that the lead sentence settles into the large type once it ends, that markers highlight evidence mid-stream, and that there's no layout jump when `done` lands.

- [ ] **Step 6: Commit.**

```bash
git add mtg-web/src/routes/+page.svelte mtg-web/src/routes/history/+page.svelte mtg-web/e2e/streaming.desktop.spec.ts
git commit -m "feat(web): stream answers on the search page

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: End-to-end check through nginx

No code, unless the check finds a problem.

- [ ] **Step 1: Bring the stack up.**

Run: `docker compose up -d --build backend frontend` (the user's working-tree `docker-compose.yml` changes are fine to *use*, just never commit them).

- [ ] **Step 2: Check that events arrive one at a time.** Find the frontend's published port in `docker compose ps`, then run:

```bash
curl -N -sS -X POST "http://localhost:<web-port>/api/v1/query/stream" \
  -H 'Content-Type: application/json' \
  -d '{"query":"how does trample work"}' | while IFS= read -r line; do printf '%s %s\n' "$(date +%T.%N | cut -c1-12)" "$line"; done
```

Expected: `event: results` straight away, then `thinking`, then `delta` lines spread over several seconds, then `done`, with timestamps spread across the answer, not all at the end. If they all arrive together, check `proxy_buffering off` in the running container (`docker compose exec frontend cat /etc/nginx/conf.d/default.conf`).

- [ ] **Step 3: Check that a disconnect still records.** Start the same curl and press Ctrl-C after the first `delta`. Wait 20 s, then check that the admin usage page (or `SELECT outcome, cost_usd FROM llm_usage ORDER BY id DESC LIMIT 1;` in the postgres container) shows `generated` rather than `pending`. Then ask the same question again and check that it's a cache hit (`results` then `done`, with `cached_at` set).

- [ ] **Step 4: Check the real UI.** Open the site, ask a question, and watch the evidence, then "Thinking…", then text arriving.

- [ ] **Step 5: Report** the results to the user. Don't open a PR until they ask.
