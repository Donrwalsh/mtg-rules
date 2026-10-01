# 5 · Make the answerer seam real

> Architecture review, 2026-09-30, branch `feature/streaming-answers`.
> Strength: **Worth exploring** · Dependency category: **true external** (Gemini). Port + production adapter + scripted test adapter
> Companion docs: [01 answer allowance](01-answer-allowance.md) (consumes the cost estimates), [02 query pipeline](02-query-pipeline.md) (the only caller).

## Summary

The pipeline calls the LLM through a duck-typed `answerer.stream(query, context)`. Its type is the concrete `GeminiAnswerer` everywhere it is named (`QueryDeps.answerer`, `_AnswerWork.answerer`, `build_answerer`, `get_answerer`). What the stream *means* leaks into the pipeline and into `usage.py`:

- `"STOP"` is the definition of a complete answer, checked in `main.py:526`.
- `StreamAccumulator`'s fields (`received`, `finish_reason`, the token counts) are read by `usage.estimate_generation`.
- `prompt_chars` measures Gemini's system prompt.
- The rule that "a missing count after the final chunk means zero" encodes Gemini's `usageMetadata` behaviour, and it lives in `usage.py`.

Meanwhile the test suite has **six hand-written fake answerers and a bridge class**, spread over four files and imported across them.

There are already two adapters in practice: Gemini in production and the fakes in tests. One adapter would make a seam hypothetical; two make it real. This one just has no declared **interface**, so each fake learns the contract by copying another fake. This doc proposes:

- a small `Answerer` port whose result is provider-neutral (text deltas, then an `Outcome` with `complete`, `stop_reason` and a `Generation`);
- Gemini's semantics moved behind it;
- one `ScriptedAnswerer` in `conftest.py`;
- a contract test that runs both adapters through the same cases, so the fake can't drift from Gemini's real behaviour.

## Why do this

**Right away**

- **The test suite shrinks and stops importing across files.** `test_query_stream.py` imports fakes from `test_gating_flow` and `test_query`. `test_gating_flow` and `test_eval_mode` import from `test_query`. A test module that other test modules import from is shared fixture code that hasn't been named as such.
- **Two fakes model the stream differently.** `_ChunksAnswerer` streams real chunks. Everything else goes through `StreamsFromGenerate`, which turns one `Generation` into one chunk. So the "failure after partial text" path is only exercised by `_ChunksAnswerer`, and the "cut off by `MAX_TOKENS`" path only by `_CountingAnswerer(finish_reason=...)` through the bridge. A single scripted adapter makes every combination one constructor call.
- **`generate()` is dead weight.** Decision 13 kept `generate()` for evals, but evals call `/api/v1/query` over HTTP (`evals/src/mtg_evals/client.py:42`). The only production-shaped caller left is `test_llm.py`. The test fakes *define* `generate()` only so the bridge can call it.

**Long term**

- **Locality for provider changes.** Any change to how the model is called lands in one adapter: Groq (an earlier spec, `2026-09-04-groq-answer-generation-design.md`), a Gemini model whose finish reasons or usage fields differ, retries on 503, or a cheaper model for short questions.
- **Leverage for the pipeline.** It handles "complete / cut off / failed with text / failed without text" in provider-neutral terms. Docs 01 and 02 get simpler because they stop reading accumulator internals.
- **Honest fakes.** The contract test keeps `ScriptedAnswerer` behaving like Gemini, so tests written against it keep meaning something.

## Current state

### What the pipeline must know about Gemini today

```python
# main.py:504–529 (_run_answer)
acc = StreamAccumulator()                                   # llm.py: Gemini stream bookkeeping
for chunk in work.answerer.stream(work.request.query, work.context):
    acc.add(chunk)
    if chunk.text:
        emit("delta", {"text": chunk.text})
...
generation = estimate_generation(acc, prompt_chars(work.request.query, work.context), s)
...
complete = failure is None and acc.finish_reason == "STOP"  # Gemini's word for "done"
if answer and not complete and error is None:
    error = f"answer cut off (finish reason: {acc.finish_reason or 'none'})"
```

```python
# usage.py:110 — Gemini's usageMetadata semantics, in the ledger module
def estimate_generation(acc: StreamAccumulator, prompt_chars: int, s: Settings) -> Generation:
    if not acc.received or acc.finish_reason is not None:
        return acc.generation()            # "a missing count after the final chunk means zero"
    return Generation(..., thinking_tokens=acc.thinking_tokens if ... else _max_output(s))
```

`usage.py` imports `StreamAccumulator` from `llm.py` only for this function. The ledger shouldn't need to know how Gemini reports tokens.

### The fakes

| Fake | File | Shape | Used by |
|---|---|---|---|
| `StreamsFromGenerate` | `conftest.py:47` | bridge: `generate()` → one chunk | base of 4 fakes |
| `_FakeAnswerer` | `test_query.py:72` | fixed text, fixed usage, optional raise | test_query, test_eval_mode |
| `_Recording` | `test_query.py:600` | records the context | one test |
| `_CountingAnswerer` | `test_gating_flow.py:17` | counts calls, configurable finish reason, raise | test_gating_flow, test_query_stream |
| `_ChunksAnswerer` | `test_gating_flow.py:310` | real chunks, then optional raise | test_gating_flow, test_query_stream |
| `_Peeking` | `test_gating_flow.py:348` | reads the DB mid-generation | one test |
| `_RecordingAnswerer` | `test_eval_mode.py:27` | records queries | test_eval_mode |

Cross-file imports: `test_query_stream → test_gating_flow, test_query`. `test_gating_flow → test_query`. `test_eval_mode → test_query`.

### The deletion test

Delete the `GeminiAnswerer` type annotation and nothing changes, because Python duck-types. So the type adds no safety today. Delete the fakes and the same behaviours have to be rebuilt per test. That shows the missing piece is a shared adapter with a declared interface.

## Proposed design

### The port

```python
# mtg_api/answerer.py  (sketch)

@dataclass(frozen=True)
class Outcome:
    """How one answer attempt ended, in provider-neutral terms."""
    text: str                      # everything received, even if it failed later
    complete: bool                 # the model finished normally
    stop_reason: str | None        # provider's reason when not complete ("MAX_TOKENS", …)
    generation: Generation         # token counts: real where reported, estimated otherwise


class AnswerRun(Protocol):
    def __iter__(self) -> Iterator[str]:
        """Answer text as it arrives. Raises on transport/provider failure;
        whatever arrived before the raise is still in outcome().text."""
    def outcome(self) -> Outcome:
        """Valid once iteration has ended, normally or by raising."""


class Answerer(Protocol):
    label: str                                     # "gemini:gemini-3.5-flash:think=low"
    def answer(self, query: str, context: str) -> AnswerRun: ...
    def worst_case(self, query: str, context: str) -> Generation:
        """The most this answer can use, for the reservation (doc 01)."""
```

The pipeline afterwards:

```python
run = answerer.answer(query, numbered.context)
emit(Thinking())
failure = None
try:
    for text in run:
        emit(Delta(text))
except Exception as exc:
    logger.exception("Answer generation failed (%s)", answerer.label)
    failure = exc
outcome = run.outcome()
spend.settle(outcome.generation, failed=failure is not None)

answer = outcome.text if (outcome.text or failure is None) else None
error = str(failure) if failure else (
    None if outcome.complete else f"answer cut off (finish reason: {outcome.stop_reason or 'none'})")
```

The pipeline no longer imports `StreamAccumulator`, `StreamChunk`, `estimate_generation` or `prompt_chars`, and no longer contains the string `"STOP"`.

### The Gemini adapter

`GeminiAnswerer` keeps its HTTP code (`_body`, `_client`, the SSE line loop and its deadline) and gains `answer()`, which returns a `GeminiRun` holding a `StreamAccumulator`:

```python
class GeminiRun:
    def __init__(self, chunks: Iterator[StreamChunk], prompt_chars: int, max_output: int): ...
    def __iter__(self):
        for chunk in self._chunks:
            self._acc.add(chunk)
            if chunk.text:
                yield chunk.text
    def outcome(self) -> Outcome:
        acc = self._acc
        return Outcome(
            text=acc.text,
            complete=acc.finish_reason == "STOP",
            stop_reason=acc.finish_reason,
            generation=_estimate(acc, self._prompt_chars, self._max_output),  # moved from usage.py
        )
```

- `estimate_generation` moves from `usage.py` into `llm.py` as the private `_estimate`, together with `estimate_tokens` (or `usage.py` keeps `estimate_tokens` as a generic helper).
- `prompt_chars` becomes private to `llm.py`. `worst_case()` uses it.
- `usage.worst_case_cost(prompt_chars, s)` becomes `cost_usd(answerer.worst_case(q, ctx), s)`.
- `stream()` stays as an internal method that `GeminiRun` wraps. `generate()` is deleted.

### The scripted adapter

```python
# tests/conftest.py
class ScriptedAnswerer:
    """An Answerer that plays a script: these text pieces, then a normal
    finish (stop="STOP"), an early stop (stop="MAX_TOKENS"), or a raise."""

    label = "scripted"

    def __init__(self, *pieces: str, stop: str | None = "STOP", raise_after: Exception | None = None,
                 usage: Generation | None = None, on_answer: Callable[[str, str], None] | None = None):
        self.calls: list[tuple[str, str]] = []
        ...

    def answer(self, query, context) -> AnswerRun:
        self.calls.append((query, context))
        if self._on_answer:
            self._on_answer(query, context)          # replaces _Peeking / _Recording
        return _ScriptedRun(self._pieces, self._stop, self._raise_after, self._usage)

    def worst_case(self, query, context) -> Generation:
        return Generation("", input_tokens=len(context) // 4, output_tokens=2048)
```

How the old fakes map onto it:

| Old | New |
|---|---|
| `_FakeAnswerer("text")` | `ScriptedAnswerer("text", usage=Generation("", 1000, 100, 200))` |
| `_FakeAnswerer(raises=E)` | `ScriptedAnswerer(raise_after=E)` |
| `_CountingAnswerer(finish_reason="MAX_TOKENS")` | `ScriptedAnswerer("Yes [1].", stop="MAX_TOKENS")`, then `len(a.calls)` |
| `_ChunksAnswerer([...], RuntimeError("reset"))` | `ScriptedAnswerer("Yes, it does [1", raise_after=RuntimeError("reset"))` |
| `_Peeking` | `ScriptedAnswerer("Yes [1].", on_answer=lambda q, c: seen.extend(rows(engine)))` |
| `_Recording` / `_RecordingAnswerer` | `ScriptedAnswerer(...)`, then `a.calls` |

### The contract test

One parametrized suite, two adapters. The Gemini adapter runs over `httpx.MockTransport`, reusing `test_llm.py`'s existing `_gemini` / `_sse` helpers and recorded bodies:

```python
@pytest.fixture(params=["gemini", "scripted"])
def make(request):
    """make(script) -> Answerer that plays `script` through that adapter."""
    ...

def test_complete_answer(make):
    run = make(Script(pieces=["Yes ", "[1]."], stop="STOP")).answer("q", "ctx")
    assert "".join(run) == "Yes [1]."
    o = run.outcome()
    assert (o.complete, o.text) == (True, "Yes [1].")

def test_cut_off(make):            # MAX_TOKENS → complete False, stop_reason kept
def test_breaks_after_text(make):  # raises; outcome keeps text; generation estimated > 0
def test_fails_before_anything(make):  # raises; text ""; generation is zero cost
def test_worst_case_bounds_the_real_cost(make):  # outcome.generation ≤ worst_case on every script
```

## Implementation plan

1. **Define the port** in `mtg_api/answerer.py` (`Outcome`, `AnswerRun`, `Answerer`). Annotate `QueryDeps.answerer`, `build_answerer` and `get_answerer` with `Answerer`.
2. **Gemini adapter.** Add `GeminiRun` and `GeminiAnswerer.answer()` / `.worst_case()` / `.label`. `label` replaces `config.generator_label`, or wraps it; keep `generator_label(s)` for `/api/v1/config`, which has no answerer instance. Move `estimate_generation` (and its four tests in `test_usage.py`) into `llm.py` and `test_llm.py`.
3. **Contract test first, Gemini half.** Write `tests/test_answerer_contract.py` with only the `gemini` param and make it green against the new adapter.
4. **`ScriptedAnswerer`** in `conftest.py`. Add the `scripted` param to the contract test and make it green.
5. **Switch the pipeline** (`_run_answer`, or doc 02's `_AnswerWriter`) to `answer()`/`outcome()`. Switch the reservation to `answerer.worst_case()` (or doc 01's `start_answer(worst_case=…)`).
6. **Migrate the fakes**, one test file per commit: `test_query.py`, `test_gating_flow.py`, `test_eval_mode.py`, `test_query_stream.py`. Delete `StreamsFromGenerate` and the six fakes. Remove the cross-file imports. While doing so, move `_FakeHit`/`_FakeQdrantClient`/`_FakeDenseModel`/`_FakeSparseModel`/`_override` into `conftest.py` (shared with doc 02).
7. **Delete `GeminiAnswerer.generate()`** and `test_llm.py::test_generate_joins_the_stream_and_takes_the_last_usage`. Its assertion becomes a contract case (usage taken from the last chunk). Update the streaming spec's decision 13 note.
8. **Verify.** `uv run pytest`, `ruff`, an eval run (`generator` label unchanged, so the eval caches still hit), and one real Gemini answer through the stack. Check the admin usage page shows the same cost as before for a comparable question.

Rough size: `answerer.py` +45, `llm.py` +40 (net, with `estimate_generation` moved in), `main.py` −15, `usage.py` −25. Tests: −120 (the fakes), +90 (`ScriptedAnswerer` + contract).

## Test plan

- **New:** `test_answerer_contract.py` as above, about 6 cases × 2 adapters.
- **Moved:** the `estimate_generation` tests go to `test_llm.py` (Gemini adapter internals: an internal seam test, allowed because it is the adapter's own test file).
- **Rewritten (same assertions):** every test that used a fake now uses `ScriptedAnswerer`.
- **Deleted:** `StreamsFromGenerate`, six fake classes, the `generate()` test.
- **Unchanged:** `test_llm.py`'s HTTP-level tests (request body, headers, thinking level, deadline, chunk timeout, blocked prompt) still test `GeminiAnswerer` as an HTTP client.

## Risks

- **A port with one production adapter could be premature.** The test adapter is the second adapter, and it already exists six times over, so the seam isn't hypothetical. Keep the port small: `answer` and `worst_case`. Don't add `Groq` speculatively.
- **The meaning of the cost estimate.** Moving `estimate_generation` must not change any number. The four existing `test_usage.py` cases move unchanged and must pass.
- **The `label` vs `generator_label(s)` split.** Eval caches key on `generator`. Assert that `answerer.label == generator_label(s)` for every built answerer, so the two can't diverge.

## Open questions (for grilling)

1. Should `Outcome` carry `stop_reason` as Gemini's raw string, or as a small enum (`complete | length | safety | other`) that the UI could word differently? The raw string goes into history today ("answer cut off (finish reason: MAX_TOKENS)").
2. Should the system prompt (and `PROMPT_VERSION`) move behind the port too? The prompt is about citations, not about Gemini, so a second provider would want the same prompt. That argues for keeping it in a `prompt.py` that the adapters import.
3. Is deleting `generate()` safe for any script outside the repo (notebooks, ad-hoc eval tooling)? Grep says no in-repo callers.
