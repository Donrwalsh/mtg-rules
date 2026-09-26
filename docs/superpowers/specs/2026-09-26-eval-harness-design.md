# Eval harness

Status: approved, implemented on feature/eval-harness
Date: 2026-09-26

## Purpose

One command (`make eval`) runs the question set in `eval.yaml` against the
running docker compose stack, scores it, compares the result to a
committed baseline and prints regressions. Two modes:

- **retrieval**: no LLM calls at all. Fast; the default.
- **full**: retrieval + answer generation + an LLM judge.

The harness is a new top-level package, `evals/`, plus a small set of
eval-only API additions in `mtg-api`, gated behind `MTG_API_EVAL_MODE`.

## Investigation (current `main`, a56ddfd)

### 1. Result shape

`QueryResponse` (`mtg_api/models.py`):

| field | notes |
|---|---|
| `query` | echoed |
| `results: list[QueryResult]` | ordered: card matches, card rulings, keyword rules, vector hits |
| `answer: str \| None` | `None` if generation failed |
| `citations: list[Citation]` | only the sources the answer cites, by number |
| `rule_references: list[str]` | rule numbers in the answer prose that exist |
| `citation_stats: {cited_count, invalid_count, uncited_answer}` | |

`QueryResult` fields: `source, title, text, score, match_type, oracle_id,
rule_id, card_name, published_at, scryfall_uri, cited`.

- **Source type** is the field `source` (not `source_type`). Values:
  `rule`, `ruling`, `card` (card-name matcher hits, built in `main.py`) and
  `oracle` (vector hits on card text; the embed payload's `source_type`).
- **Rule** identity: `rule_id` (set on keyword and vector rule hits).
- **Card name**: `card_name`, set on `card`, `oracle` and `ruling` results
  (the rulings payload carries the card name).
- **Match type**: `match_type`, one of `card_name_match`,
  `card_ruling_match`, `keyword_rule_match`, `vector_hit`.
- **Keyword matcher exists** (`keyword_matcher.py`): named keywords pull their
  702.x rules in as `keyword_rule_match`, and vector hits with the same
  `rule_id` are de-duplicated away.
- **Citations exist** (`citations.py`): the answer is post-processed,
  `QueryResult.cited` is set, and `citation_stats.invalid_count` counts
  citations of numbers not in the context. Full mode therefore records
  citation metrics (below).

Change: add `source_type: str` to `QueryResult`, always equal to `source`
(set by a model validator so no construction site changes). Existing
consumers keep reading `source`. Every other field the runner needs is
already present.

### 2. Settings

`config.py` defines one `Settings(BaseSettings)` with `env_prefix="MTG_API_"`
and a module-level `settings = Settings()`. `main.py` imports that object
and reads `settings.<x>` inline inside `query()`; `lru_cache`d dependency
factories (`get_card_matcher`, `get_groq_answerer`, ...) read it once.

Per-request overrides: `query()` computes
`effective = settings.model_copy(update=validated_overrides)` and reads
every retrieval/generation value from `effective` instead of `settings`.
`model_copy` returns a new object, so nothing global is mutated and the
next request starts from the untouched defaults. Validation happens before
the copy (allowlist + type coercion via a pydantic model), because
`model_copy(update=...)` itself does not validate.

### 3. Answer generation

`get_groq_answerer()` (cached) always returns an `OllamaAnswerer(settings.ollama_url,
settings.ollama_model)` despite its name. `OllamaAnswerer.generate(query, context)`
POSTs to `/api/chat` with `stream: false` and `options: {num_ctx: 16384}`
(hard-coded). **Temperature and max tokens are not configurable**; the model
uses Ollama defaults. History records `model=settings.ollama_model`.
`GroqAnswerer` exists but is unused. (Compose still requires
`MTG_API_GROQ_API_KEY`; out of scope, left alone.)

Change:
- New settings `generation_temperature: float | None = None` and
  `generation_max_tokens: int | None = None`. `None` means "don't send it",
  so production behaviour is unchanged. `OllamaAnswerer` accepts both and
  adds `temperature` / `num_predict` to `options` only when set.
- `build_answerer(s: Settings) -> OllamaAnswerer` in `main.py`;
  `get_groq_answerer()` becomes `build_answerer(settings)` (still cached).
  When a request overrides any generation-affecting key, `query()` uses
  `build_answerer(effective)` for that request only. Tests monkeypatch
  `main.build_answerer`.
- `PROMPT_VERSION = 1` constant in `llm.py`, bumped by hand when
  `_SYSTEM_PROMPT` or the user-message template changes.
- Generator identifier: `f"ollama:{effective.ollama_model}"`.

## API changes (mtg-api)

### Eval mode

- `eval_mode: bool = False` (`MTG_API_EVAL_MODE`).
- `docker-compose.yml`: `MTG_API_EVAL_MODE: ${MTG_API_EVAL_MODE:-false}`.
  Enabled locally with `MTG_API_EVAL_MODE=true` in `.env`, never in prod.

### Request (all optional)

```python
class QueryRequest(BaseModel):
    query: str
    generate: bool = True
    overrides: dict[str, Any] = {}
    source: str | None = None
```

- `generate=False`: the answerer is never called; `answer=None`, no
  citations. Context is still built (for `context_hash`).
- `overrides` non-empty and eval mode off: **403**. Unknown key: **422**
  listing the unknown keys. Wrong type (e.g. `"hybrid_top_k": "x"`): **422**.
- Allowlist (`OVERRIDABLE_SETTINGS` in `config.py`):
  `hybrid_dense_weight, hybrid_sparse_weight, hybrid_top_k,
  hybrid_per_branch_limit, hybrid_score_threshold, card_ruling_limit,
  collection_name, ollama_model, generation_temperature,
  generation_max_tokens`.
  The last three are "generation-affecting" (`GENERATION_SETTINGS`).
- History: **requests with `source == "eval"` are not saved.** Chosen over
  tag-and-filter because it needs no migration and eval runs would
  otherwise dominate the table. Documented in the README. `source` is a
  harmless label, so this applies whether or not eval mode is on.

### Response additions

Always: `QueryResult.source_type` (above).

Eval mode only (fields are `None` otherwise, so the normal response shape
is unchanged apart from three null keys):
- `context_hash`: sha256 hex of the exact context string passed (or that
  would be passed) to `answerer.generate`.
- `prompt_version`: `llm.PROMPT_VERSION`.
- `generator`: `"ollama:<model>"`.

### `GET /api/v1/config`

404 unless eval mode. Returns:

```json
{
  "settings": { "<every OVERRIDABLE_SETTINGS key>": ...,
                "dense_model_name": ..., "sparse_model_name": ...,
                "ollama_url": ... },
  "generator": "ollama:phi4",
  "prompt_version": 1,
  "collection": {"name": "mtg_rules", "points_count": 123456},
  "data_files": {"rules": "rules_2026-08-25.jsonl",
                 "cards": "cards_2026-09-26.jsonl",
                 "rulings": "rulings_2026-08-25.jsonl"}
}
```

Built from an explicit allowlist of keys, never by dumping `Settings`, so
`groq_api_key`, `postgres_dsn` (contains a password), broker URLs etc. can
never leak. `data_files` are the latest files per pattern in `parsed_dir`
(the same `_latest` rule the API uses to load them). `points_count` via
`client.count(collection_name, exact=True)`; `null` plus an `error` string
if Qdrant fails (the runner's preflight then fails on it).

## Runner (`evals/`)

Own package, same conventions as the others: `evals/pyproject.toml`
(hatchling, `src/mtg_evals`, `[dev]` extra with pytest), Typer CLI exposed
as `mtg-evals` and `python -m mtg_evals`. Dependencies: typer, httpx,
pyyaml. Plain aligned-text output (no rich).

```
evals/
  pyproject.toml
  experiments/{dense70,topk15,temp0}.yaml
  baseline-retrieval.json   (committed once promoted)
  baseline-full.json
  runs/     (gitignored)
  .cache/   (gitignored)
  src/mtg_evals/
    cli.py        Typer app: run, baseline, compare, sweep, validate
    settings.py   paths + env (EVAL_FILE, EVAL_API_URL, EVAL_JUDGE_*)
    cases.py      load eval.yaml, schema validation, split/tag/id filters
    validate.py   eval.yaml vs parsed data
    client.py     httpx API client + preflight
    scoring.py    retrieval scoring, verdict parsing
    judge.py      OpenAI-compatible judge client + prompts
    cache.py      JSON-file answer/judge caches
    runner.py     orchestration, concurrency, run-file writing
    report.py     aggregates, diffing, terminal report, exit code
  tests/
```

### Settings

| setting | env | default |
|---|---|---|
| eval file | `EVAL_FILE` / `--eval-file` | `<repo root>/eval.yaml` |
| API URL | `EVAL_API_URL` / `--api-url` | `http://localhost:8000` |
| parsed data dir (validate) | `EVAL_PARSED_DIR` | `<repo>/mtg-worker/mtg-ingestion/data/parsed` |
| judge | `EVAL_JUDGE_BASE_URL`, `EVAL_JUDGE_MODEL`, `EVAL_JUDGE_API_KEY` | `http://localhost:11434/v1`, none, `ollama` |

Repo root = two parents above the package's `evals/` dir (resolved from
`__file__`, so it works from any cwd). Full mode fails preflight if
`EVAL_JUDGE_MODEL` is unset.

### Case schema

Validated in `cases.py` (hand-written checks, precise messages):
`id` (str, unique), `question` (str), `tags` (non-empty list; first tag in
`keyword | cr-only | card-ruling | interaction | negative | messy`),
`split` (`dev | test`), optional `required_sources` (keys only
`rules | rulings | cards`, each a list of str), `expected_cards`,
`forbidden_cards` (lists of str), `gold_answer` (str), `verdict`
(`yes | no`), `should_decline` (bool). Unknown keys are errors.

### Commands

- `run --mode retrieval|full --split dev|test|all --tag T... --id I...
  --exp NAME --api-url URL --concurrency N --no-compare`.
  Concurrency default 4 (retrieval) / 1 (full). Tag filter matches any of a
  case's tags; tag and id filters are ANDed with split.
- `baseline [RUN] --mode M`: copies RUN (default: newest run file of mode M
  in `runs/`) to `evals/baseline-<mode>.json`.
- `compare A B`: report B against A (paths, or bare names looked up in
  `runs/`). Same diff/exit code as `run`.
- `sweep EXP... --mode --split ...`: runs each named experiment in turn
  (a no-override run is not added implicitly), then
  prints one table: rows = metrics, columns = experiments, plus a column
  for the current baseline if present. Exits non-zero if any experiment
  regressed against the baseline.
- `validate`: see below. Exit 1 on any problem.

### Preflight (every run)

In order, stopping at the first failure with a one-line reason and a hint:
1. `eval.yaml` passes schema validation.
2. `GET /health` → `qdrant == "ok"` (hint: is the stack up?).
3. `GET /api/v1/config` → 200 (404 hint: set `MTG_API_EVAL_MODE=true` and
   recreate `mtg-api`).
4. `collection.points_count > 0` (hint: run the embed pipeline).
5. Full mode: `EVAL_JUDGE_MODEL` is set.

### Retrieval scoring (per case)

`results` are ranked 1..N in response order.

- rule requirement `R`: satisfied by the first result with
  `source_type == "rule"` and `rule_id == R or rule_id.startswith(R)`.
  (Pure string prefix, as the eval.yaml header specifies.)
- rulings requirement `C`: first result with `source_type == "ruling"` and
  `card_name == C`.
- cards requirement `C`: first result with `source_type in {"card","oracle"}`
  and `card_name == C`.
- **recall** = satisfied / required; **pass** = all satisfied.
- **first_hit_rank** = min rank over satisfied requirements (null if none).
  Also stored per requirement (`{"rules:702.19b": 3, ...}`) so diffs can
  say "702.19 was rank 3, now missing".
- **matcher**: `card_name_match` result names; `expected_missing` =
  expected − matched; `forbidden_hits` = forbidden ∩ matched.
- **context**: result count, total `text` characters.
- Cases with no required sources (the `should_decline` negatives) have
  `pass/recall = null` in retrieval mode and are excluded from pass-rate
  and recall aggregates; forbidden/expected card checks still run.

A case **passes retrieval** when all requirements are satisfied, no
forbidden card matched and no expected card is missing. (Recall-only pass
is also stored as `sources_pass` so the metrics stay separable.)

### Full-mode scoring

Per case:
1. `POST` with `generate=false` → `context_hash`, `generator`,
   `prompt_version`, results (retrieval scoring comes from this call).
2. Answer cache key = sha256 of JSON
   `[question, context_hash, generator, prompt_version, {generation overrides}]`.
   Hit → reuse. Miss → `POST generate=true`; if the second response's
   `context_hash` differs from the first, record `context_drift: true`
   (retrieval is deterministic, so this indicates a bug) and cache under
   the second hash. Latency recorded for each call; `answer_cached` flag.
3. **Verdict** (cases with `verdict`): look at the first sentence (up to
   the first `.`, `!`, `?` or newline, max 200 chars), lowercased, markdown
   stripped. `yes` if it starts with `yes`; `no` if it starts with `no`
   (word boundary) / `no,`; otherwise search that sentence for a
   standalone `yes`/`no` and use it only if exactly one kind appears; else
   `unclear`. `verdict_correct` = parsed == expected.
4. **Judge**: OpenAI-compatible `POST {base}/chat/completions`, temperature
   0, `response_format` not relied on; the prompt asks for a JSON object
   `{"grade": ..., "reason": ...}` and the parser extracts the first
   `{...}` block, falling back to `grade: "error"`.
   - normal: `correct | partial | incorrect` vs `gold_answer`.
   - `should_decline`: `declined_properly | answered_anyway`.
   - `JUDGE_PROMPT_VERSION` constant. Cache key = sha256 of
     `[question, gold_answer, answer, judge_model, judge_prompt_version]`.
   - Warning banner if `EVAL_JUDGE_MODEL` equals the generator's model
     name (with or without the `ollama:` prefix).
5. **Citations**: `cited_satisfies_required` = any `citations[]` entry
   satisfies a requirement (same matching rules, using `source_type`,
   `rule_id`, `card_name` from the Citation); `invalid_citations` =
   `citation_stats.invalid_count`.

Caches: one JSON file per entry in `evals/.cache/answers/<key>.json` and
`evals/.cache/judge/<key>.json` (safe under concurrency, easy to delete).

### Experiments

`evals/experiments/<name>.yaml`: `description`, `overrides`. Loaded and
the keys sent as-is; the API's 422 is surfaced as a preflight-style error
naming the bad key.

- `dense70`: `hybrid_dense_weight: 0.7`, `hybrid_sparse_weight: 0.3`
- `topk15`: `hybrid_top_k: 15`
- `temp0`: `generation_temperature: 0`

### Run file

`evals/runs/<YYYYMMDDTHHMMSSZ>-<sha7>[-dirty]-<mode>[-<exp>].json`:

```json
{
  "metadata": {"started_at", "git_sha", "dirty", "mode", "split", "tags",
               "ids", "experiment", "overrides", "api_config",
               "eval_file", "eval_sha256", "judge_model", "generator",
               "prompt_version"},
  "cases": {"<id>": {"category", "tags", "retrieval": {...},
                     "full": {...} | null, "results": [{"rank",
                     "source_type", "rule_id", "card_name", "oracle_id",
                     "match_type", "score"}], "latency_ms", "error"}},
  "aggregates": {"overall": {...}, "by_category": {"<tag>": {...}}}
}
```

A per-case API error is recorded as `error` and counts as a failure, not a
crash; the run still writes.

### Report and comparison

```
eval  retrieval · dev (38) · a56ddfd-dirty · mtg_rules · exp=dense70
                        baseline      this run
pass rate               71.1%         73.7%  ▲
mean recall             0.842         0.855  ▲
mean first-hit rank     2.41          2.38   ▲
forbidden-card hits     1             1
by category (pass)
  keyword               8/9 → 9/9
  ...
REGRESSIONS (1)
  ward-targeting        702.21a was rank 4, now missing
FIXED (2)
  ...
```

- ▲/▼ denote better/worse (lower is better for rank and violations).
- Full-mode metrics: judge correct/partial/incorrect rates, verdict
  accuracy, decline accuracy, answer and judge cache hit rates, mean
  latency.
- **Regression** (per case in both runs): retrieval pass → fail;
  any requirement satisfied → missing; forbidden hit added; expected card
  lost; full mode: judge grade worsened (correct > partial > incorrect;
  declined_properly > answered_anyway), verdict correct → wrong/unclear.
  **Fixed** is the mirror. Reasons are short strings ("702.19b was rank 3,
  now missing", "judge: correct → incorrect").
- Warnings: case IDs added/removed vs baseline; `eval_sha256` differs;
  config keys (`api_config.settings`, `generator`, `prompt_version`,
  `data_files`, `points_count`) that differ and are not explained by this
  run's experiment overrides.
- No baseline file: print "no baseline for <mode>; skipping comparison"
  and exit 0.
- Exit code: 1 if any case regressed, 2 on preflight/usage failure, else 0.

### `validate`

Against the latest `rules_*.jsonl`, `cards_*.jsonl`, `rulings_*.jsonl` in
the parsed dir. Reports every problem (not just the first):
- schema errors (including duplicate IDs)
- rule requirement matching no `rule_id` by prefix
- `required_sources.cards`, `rulings`, `expected_cards`, `forbidden_cards`
  names that are not exact card names
- rulings requirement whose card (name → oracle_id via cards file) has no
  rulings.

## Makefile (repo root)

```make
EVALS := python -m mtg_evals
ARGS  = $(if $(EXP),--exp $(EXP)) $(foreach t,$(TAG),--tag $(t)) $(foreach i,$(ID),--id $(i))
eval:          ; $(EVALS) run --mode retrieval --split dev $(ARGS)
eval-full:     ; $(EVALS) run --mode full --split dev $(ARGS)
eval-test:     ; $(EVALS) run --mode retrieval --split test $(ARGS) && $(EVALS) run --mode full --split test $(ARGS)
eval-baseline: ; $(EVALS) baseline --mode $(MODE)
eval-compare:  ; $(EVALS) compare $(A) $(B)
eval-sweep:    ; $(EVALS) sweep $(EXPS) $(ARGS)
eval-validate: ; $(EVALS) validate
```

Assumes `pip install -e "evals[dev]"` (README, alongside the other
packages). **`make` is not currently installed on this Windows machine**;
the README notes installing GNU make (e.g. `winget install ezwinports.make`) or running
`python -m mtg_evals ...` directly, which every target is a thin wrapper
around).

## Tests

- `mtg-api/tests/test_eval_mode.py` (fakes from `test_query.py`):
  generate=false never calls the answerer; overrides 403 when eval mode
  off, 422 for unknown key and bad type; an override (`hybrid_top_k=1`)
  changes the response and a following request without overrides gets
  the default; generation override builds a per-request answerer;
  `/api/v1/config` 404 off, 200 on, and contains none of
  `groq_api_key/postgres_dsn/broker_url/result_backend` or their values;
  `source="eval"` not in history; `context_hash` identical across two
  identical requests and equal to sha256 of `build_context(...)[0]`;
  `source_type` mirrors `source`. Eval mode toggled by monkeypatching
  `main.settings`.
- `mtg-api/tests/test_llm.py`: temperature/max tokens only sent when set.
- `evals/tests/`: fixture responses (no live stack) for rule prefix,
  rulings and cards matching; recall; first-hit rank; forbidden and
  expected cards; verdict parsing (yes/no/unclear table); answer and judge
  cache keys (stable, sensitive to each component); regression/fixed
  diffing incl. added/removed cases; exit codes; `validate` on a fixture
  data dir + eval file with a bad rule ID, a missing card and a duplicate
  ID.

## Other files

- `.gitignore`: `evals/runs/`, `evals/.cache/`.
- `.env.example`: `MTG_API_EVAL_MODE=false`, `EVAL_JUDGE_BASE_URL`,
  `EVAL_JUDGE_MODEL`, `EVAL_JUDGE_API_KEY`.
- README: new "Evals" section (enable eval mode, install, targets,
  baselines, caches, history exclusion).

## Out of scope

Fixing any failing eval cases; editing `eval.yaml`; wiring Groq back in;
CI integration.
