# Query pipeline extraction, with evals on phi4

Status: design agreed, awaiting implementation plan
Date: 2026-10-01
Branch: `refactor/query-pipeline` (to be cut from `main` after the prerequisites below)

## Purpose

Two goals, landed in one PR:

1. **Pull the query pipeline out of `main.py`.** This is architecture review
   [doc 02](../../architecture/2026-09-30-streaming-review/02-query-pipeline.md)
   (proposed design, interface, step-by-step moves). This spec does not
   repeat that design. It records the decisions taken on doc 02's open
   questions and what this PR adds around it.
2. **Run evals without paying Gemini.** Full-mode evals generate with
   `phi4` on the local Ollama. The judge stays `qwen2.5:14b`. The eval answer
   cache can't help: no cached answers are known to match the current
   config, and a new generator label makes new cache keys anyway.

The refactor itself changes no behaviour. Everything that does change
behaviour (the Ollama adapter, harness changes) lands in commits *before*
the refactor commits, so the before and after eval runs use the same
generator.

## Prerequisites (separate small PRs, before this one)

1. **`exec` fix** in `docker-compose.prod.yml`: `exec uvicorn …`, so uvicorn
   gets SIGTERM and shuts down gracefully (review doc 06).
2. **429-mid-stream fix**, frontend only: disable "ask" while an answer is
   streaming (review doc 03, open question 1, option b). Server-side slot
   takeover (option d) waits for doc 01.
3. **Merge `docs/architecture-review`** to `main`, so the review docs and this
   spec are on the branch the work is cut from.

## Scope decisions

| # | Decision |
|---|---|
| Doc 01 (answer allowance) | **Separate PR, after this one.** It changes behaviour. This PR leaves the `admission` seam marked where it will plug in. |
| Doc 02 Q1: blocking endpoint | **Stays detached.** `/api/v1/query` runs the answer on the worker thread like the stream does, through one code path. `collect` just drains events. No `detached` flag. |
| Doc 02 Q2: `QueryDeps` | **Not split.** `start()` takes the whole `QueryDeps`. Only `retrieve()` takes a narrow `RetrievalDeps`. |
| Doc 02 Q3: evals in-process | **No.** Evals keep calling over HTTP; that is what proves `collect` kept the JSON contract. |
| Shared test fakes | **Done in this PR**, in `conftest.py`: the Qdrant/embedder fakes from `test_query.py` **and** the answerer fakes doc 05 would otherwise move, plus a `deps()` fixture. Doc 05 later changes only the answerer port. |
| Prod | **Untouched.** `docker-compose.prod.yml` is standalone and sets every variable it uses. The provider defaults to `gemini`, prod has no route to Ollama, and no prod file changes. |

## phi4 answer generation (Ollama adapter)

### Selection

Selected for the whole environment, never per request, so a run can't fall
back to Gemini because someone forgot an override:

```
MTG_API_ANSWER_PROVIDER=ollama           # default: gemini
MTG_API_OLLAMA_URL=http://host.docker.internal:11434
MTG_API_OLLAMA_MODEL=phi4:latest
```

`answer_provider` is **not** added to `OVERRIDABLE_SETTINGS`. The local web
UI streams from phi4 too when this is set.

With `provider=ollama`, no Gemini key is needed:

- `build_answerer` requires `MTG_API_GEMINI_API_KEY` only when the provider is `gemini`.
- `docker-compose.yml` (dev) changes `MTG_API_GEMINI_API_KEY` from `:?` to `:-`.
- The local `.env` leaves the key blank. That alone makes Gemini spend impossible.

### Adapter

`OllamaAnswerer` in `llm.py`, beside `GeminiAnswerer`, with the same surface
the pipeline uses today: `.model` and `.stream(query, context) ->
Iterator[StreamChunk]`. It uses the same system prompt and user message.

- **Endpoint: native `POST /api/chat`** with `stream: true`, not the
  OpenAI-compatible `/v1/chat/completions`. Only the native endpoint accepts
  `options.num_ctx` per request and reports `prompt_eval_count` /
  `eval_count`, which map to `input_tokens` / `output_tokens`.
- **Options:**
  - `num_ctx` from `ollama_num_ctx`
  - `temperature` from `generation_temperature`
  - `seed` from `ollama_seed`
  - `num_predict` from `generation_max_tokens`, falling back to `ollama_num_predict_default`
- `generation_thinking_level` is ignored. phi4 has no thinking, so no
  `thinking_tokens` and no thinking parts.
- **Finish reason:** map Ollama's `done_reason` (`stop` / `length`) to the
  existing `finish_reason` field, so "answer cut off" behaves as it does for
  Gemini.
- **Timeouts:** its own total and per-chunk timeouts. Loading a 9 GB model
  before the first token can take close to a minute on this machine (see the
  measurements below).
- **Label:** `generator_label` becomes provider-aware: `ollama:<model>` (for
  example `ollama:phi4:latest`). Gemini labels are unchanged, so existing
  caches and the production answer cache keep their keys.

### Context overruns are loud

Ollama silently truncates a prompt longer than `num_ctx`. It drops the
oldest tokens first, which would be our system prompt and its citation
rules. A truncated answer must never pass for a weak one.

- The adapter raises `ContextOverflow("prompt of N tokens exceeds num_ctx M")`
  when Ollama reports it hit the window. That flows through the normal
  generation-failure path: the case's `error`, history and the `Failed` event.
- **Open fact to settle in the adapter commit:** exactly how Ollama 0.32
  signals truncation, most likely `prompt_eval_count` at the limit. Verify it
  with a deliberately oversized prompt before relying on it. Pin the result in
  a test that uses a fake transport.
- The eval report adds a `context overruns: k` line (listing the case IDs when
  k > 0) and a `max_prompt_tokens` aggregate, so the remaining headroom is
  visible before anything fails.

### Settings

All of these are settings, not hard-coded values. All except `ollama_url`
are added to `CONFIG_EXPOSED_SETTINGS`, so every run file, and so
`baseline-full-phi4.json`, records them under `api_config.settings`.

| Setting | Default |
|---|---|
| `answer_provider` | `gemini` |
| `ollama_url` | `http://host.docker.internal:11434` (not exposed: it describes the machine, not the result) |
| `ollama_model` | `phi4:latest` |
| `ollama_num_ctx` | `6144` |
| `ollama_seed` | `0` |
| `ollama_num_predict_default` | `1024` |
| `ollama_timeout_seconds` | `180` |
| `ollama_stream_chunk_timeout_seconds` | `120` |

Eval runs use `generation_temperature=0` (already a setting).

## Eval harness changes

### Two-pass full runs

The harness currently generates and judges each case before moving to the
next. On this machine phi4 and `qwen2.5:14b` can't both be resident, so a
full run would swap models about 76 times per 38-case dev run.

A full run becomes:

1. Retrieval for all cases.
2. Generation for all cases (phi4 resident).
3. Judging for all cases (qwen resident).

That's two swaps per run. Per-case records, caches and report output stay
the same; only the order of calls changes.

### Baseline per generator

`baseline_path(mode)` becomes `baseline_path(mode, generator)`:

- `gemini:*` uses `baseline-<mode>.json`, so the existing files are unchanged.
- Any other generator uses `baseline-<mode>-<model>.json` (phi4 →
  `baseline-full-phi4.json`).
- There is **no fallback**: a phi4 run is never compared against the Gemini
  baseline. The `baseline` command writes to the file matching the run's
  generator.
- Retrieval mode doesn't involve a generator and keeps `baseline-retrieval.json`.

The report names the baseline it used:

```
baseline: baseline-full-phi4.json (ollama:phi4:latest · <git sha> · <date>)
```

When there's no baseline for this generator, the existing "no baseline"
message names the file it looked for.

### Gemini guard

Full-mode preflight reads `generator` from `/api/v1/config` and refuses
`gemini:*` unless `--allow-gemini` is passed. The blank key already prevents
spend; the guard makes the reason clear.

### Fresh answers

`--fresh-answers` on `run` bypasses the answer cache for reading (results
are still written), so a run actually goes through generation. This is
needed for the before/after check, because a cached replay never exercises
the worker hand-off.

### Committed baseline

`evals/baseline-full-phi4.json` is committed. It is the "before" run (see
the commit order below), so every later run in this PR compares against it
automatically.

## Acceptance: "the refactor changed nothing"

Comparing the "after" run with `baseline-full-phi4.json`, both
`--fresh-answers`, phi4 with `temperature=0` and `seed=0`:

- **Must be identical:**
  - retrieval output per case, and `context_hash`
  - the response's key set
  - the retrieval-mode run against `baseline-retrieval.json`
- **Expected identical, investigated if not:** answer text, citations, judge
  verdicts. Local inference at temperature 0 with a fixed seed is usually
  deterministic on the same machine, but GPU scheduling can occasionally
  change a token. A difference here is a reason to look, not an automatic
  failure.
- Plus everything in doc 02's step 10:
  - `uv run pytest`
  - `ruff`
  - the unchanged HTTP tests (`test_query.py`, `test_gating_flow.py`, `test_eval_mode.py`, `test_query_stream.py`)
  - a manual `curl -N` through nginx

## Commit order (one PR)

1. Pin the current behaviour: full suite green, passing list saved.
2. Move the shared fakes into `conftest.py` and add the `deps()` fixture (test code only).
3. Ollama adapter: provider setting, `OllamaAnswerer`, provider-aware
   `generator_label`, optional Gemini key, dev compose `:-`, the
   `ContextOverflow` check (after verifying Ollama's signal), and the exposed settings.
4. Harness: two-pass full runs, a baseline per generator plus the report
   header, the Gemini preflight guard, `--fresh-answers`, and the
   overrun/`max_prompt_tokens` reporting.
5. **"Before" run:** full, fresh, phi4. Commit it as `evals/baseline-full-phi4.json`.
6. Doc 02 steps 2–7, one commit each:
   1. protocol models
   2. move `retrieve`
   3. create `query_pipeline.py`
   4. typed events and adapters
   5. collapse the history saves
   6. split the three strategies
7. Doc 02 step 8: direct pipeline tests in `tests/test_query_pipeline.py`.
8. **"After" run** and the acceptance check above.
9. README: a "Running evals on phi4" section under "Evals" covering:
   - the `.env` lines
   - `host.docker.internal`
   - the blank Gemini key
   - VRAM, model swapping and `num_ctx`, with the measurements below
   - `--fresh-answers`, `--allow-gemini`, and per-generator baselines

## Measurements behind the choices

Taken 2026-10-01 on the dev machine: RTX 3080 (10 GB), Ollama 0.32.6.

**Swapping models** (short prompt, alternating; Ollama `load_duration`):

| Call | Load time |
|---|---|
| phi4 | 23.1 s |
| qwen2.5:14b | 26.8 s |
| phi4 | 18.8 s |
| qwen2.5:14b | 23.1 s |
| phi4 | 55.7 s |
| qwen2.5:14b | 24.1 s |

Only one model was resident after each call. At about 76 swaps per dev run,
that's roughly 30 minutes of loading per run, hence two-pass runs.

**phi4 by context size** (1,880-token prompt). Neither setting fits fully on
the GPU; 7.8 GB is in VRAM either way.

| `num_ctx` | Total size | Generation speed |
|---|---|---|
| 4096 | 9.5 GB | 11.1 tok/s |
| 8192 | 10.3 GB | 8.1–8.6 tok/s |

**Prompt size.** The largest `context_chars` in the last full run was 9,200
(about 2,400 tokens). The system prompt is 1,366 chars. With the question and
up to 1,024 answer tokens, the worst case is about 4,000 tokens, so 4096 is
too tight and 6144 leaves about 50% headroom.

## Out of scope

- Doc 01 (answer allowance), including slot takeover for abandoned answers.
- Doc 03 beyond the prerequisite 429 fix, and docs 04, 05 and 06 beyond the `exec` fix.
- A generic OpenAI-compatible answerer, or Groq.
- Per-request provider overrides.
- Running evals in-process.
- Changes to `baseline-full.json` or to the Gemini judge setup.
