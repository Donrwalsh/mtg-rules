# Eval Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `make eval` runs `eval.yaml` against the running stack, scores it, compares to a committed baseline and prints regressions.

**Architecture:** Eval-only API additions in `mtg-api` (gated by `MTG_API_EVAL_MODE`): per-request setting overrides via `settings.model_copy`, `generate=false`, `context_hash`/`prompt_version`/`generator`, `GET /api/v1/config`. A new standalone `evals/` package (Typer CLI) calls the API over HTTP, scores retrieval deterministically, optionally generates answers (cached) and grades them with an OpenAI-compatible judge (cached), writes a run JSON file and diffs it against a baseline.

**Tech Stack:** FastAPI, pydantic-settings, pytest (mtg-api); Python ≥3.12, Typer, httpx, PyYAML, pytest (evals).

**Spec:** `docs/superpowers/specs/2026-09-26-eval-harness-design.md`

## Global Constraints

- `eval.yaml` contents are never modified. Its path is a setting (`EVAL_FILE`), default `<repo root>/eval.yaml`.
- All request additions are optional and backward compatible; `QueryResult.source` stays.
- Overrides are never applied by mutating the global `settings` object.
- `/api/v1/config` never returns secrets: built from an explicit key allowlist.
- `MTG_API_EVAL_MODE` defaults to false everywhere (`${MTG_API_EVAL_MODE:-false}` in compose).
- Eval requests (`source == "eval"`) are not saved to `query_history`.
- Runner exit codes: 0 ok, 1 regression, 2 preflight/usage failure.
- `evals/runs/` and `evals/.cache/` are gitignored; `evals/baseline-<mode>.json` are committed.
- Conventional commits, each ending with the `Co-Authored-By` trailer.
- Line length 100 (ruff), `from __future__ import annotations` at the top of each module, matching existing packages.

## File Structure

mtg-api (modify): `config.py` (eval_mode, generation settings, allowlists), `models.py` (request/response fields, `source_type`), `llm.py` (`PROMPT_VERSION`, answerer options), `main.py` (overrides, generate flag, history skip, config endpoint). Tests: `tests/test_eval_mode.py` (new), `tests/test_llm.py`, `tests/test_models.py`.

evals (create):

| file | responsibility |
|---|---|
| `pyproject.toml` | package `mtg-evals`, script `mtg-evals` |
| `src/mtg_evals/paths.py` | repo root, eval file, runs/cache/experiments/baseline paths, env settings |
| `src/mtg_evals/cases.py` | load + schema-validate eval.yaml, filter by split/tag/id |
| `src/mtg_evals/validate.py` | eval.yaml vs parsed data files |
| `src/mtg_evals/scoring.py` | retrieval scoring, citation scoring, verdict parsing |
| `src/mtg_evals/cache.py` | cache keys + JSON-file cache |
| `src/mtg_evals/judge.py` | judge prompts, OpenAI-compatible client, grade parsing |
| `src/mtg_evals/client.py` | API client, preflight |
| `src/mtg_evals/runner.py` | per-case evaluation, concurrency, run file |
| `src/mtg_evals/report.py` | aggregates, diff, rendering, exit code |
| `src/mtg_evals/experiments.py` | load experiment yaml |
| `src/mtg_evals/cli.py`, `__main__.py` | Typer commands |
| `experiments/{dense70,topk15,temp0}.yaml` | examples |
| `tests/` | fixture-based unit tests |

Root: `Makefile`, `.gitignore`, `.env.example`, `docker-compose.yml`, `README.md`.

---

### Task 1: mtg-api settings, `source_type`, answerer options, PROMPT_VERSION

**Files:** Modify `mtg-api/src/mtg_api/config.py`, `models.py`, `llm.py`; Test `tests/test_llm.py`, `tests/test_models.py`.

**Produces:**
- `Settings.eval_mode: bool = False`, `generation_temperature: float | None = None`, `generation_max_tokens: int | None = None`
- `config.OVERRIDABLE_SETTINGS: tuple[str, ...]` = hybrid_dense_weight, hybrid_sparse_weight, hybrid_top_k, hybrid_per_branch_limit, hybrid_score_threshold, card_ruling_limit, collection_name, ollama_model, generation_temperature, generation_max_tokens
- `config.GENERATION_SETTINGS = frozenset({"ollama_model", "generation_temperature", "generation_max_tokens"})`
- `QueryResult.source_type: str | None = None`, filled from `source` by a `model_validator(mode="after")`
- `llm.PROMPT_VERSION: int = 1`
- `OllamaAnswerer(base_url, model, num_ctx=16384, temperature=None, max_tokens=None)`; `model` property; options include `temperature`/`num_predict` only when not None.

- [ ] **Step 1: failing tests**

```python
# test_models.py
def test_query_result_source_type_mirrors_source():
    r = QueryResult(source="oracle", title="Shock", text="", score=1.0, match_type="vector_hit")
    assert r.source_type == "oracle"
    assert r.model_dump()["source_type"] == "oracle"

# test_llm.py
def _capture_ollama(monkeypatch):
    sent = {}
    class _Resp:
        def raise_for_status(self): pass
        def json(self): return {"message": {"content": "ok"}}
    def fake_post(url, json, timeout):
        sent.update(json); return _Resp()
    monkeypatch.setattr(httpx, "post", fake_post)
    return sent

def test_ollama_answerer_omits_unset_generation_options(monkeypatch):
    sent = _capture_ollama(monkeypatch)
    OllamaAnswerer("http://x", "phi4").generate("q", "ctx")
    assert sent["options"] == {"num_ctx": 16384}

def test_ollama_answerer_sends_temperature_and_max_tokens(monkeypatch):
    sent = _capture_ollama(monkeypatch)
    OllamaAnswerer("http://x", "phi4", temperature=0.0, max_tokens=256).generate("q", "ctx")
    assert sent["options"] == {"num_ctx": 16384, "temperature": 0.0, "num_predict": 256}
```

- [ ] **Step 2:** `cd mtg-api && python -m pytest tests/test_llm.py tests/test_models.py -q` → FAIL.
- [ ] **Step 3:** implement (see Produces). Validator:

```python
@model_validator(mode="after")
def _mirror_source(self) -> "QueryResult":
    self.source_type = self.source
    return self
```

- [ ] **Step 4:** full `python -m pytest -q` → PASS.
- [ ] **Step 5:** commit `feat(mtg-api): add eval-mode and generation settings, source_type, prompt version`.

### Task 2: query endpoint — generate flag, overrides, eval fields, history skip

**Files:** Modify `main.py`, `models.py`; Test `tests/test_eval_mode.py` (new; imports `_override`, `_FakeHit`, `_FakeAnswerer` from `test_query`).

**Consumes:** Task 1. **Produces:**
- `QueryRequest.generate: bool = True`, `overrides: dict[str, Any] = {}`, `source: str | None = None`
- `QueryResponse.context_hash / prompt_version / generator: ... | None = None`
- `main.build_answerer(s: Settings) -> OllamaAnswerer`; `get_groq_answerer()` returns `build_answerer(settings)`
- `main.resolve_settings(overrides: dict) -> Settings` raising `HTTPException` 403/422

```python
def resolve_settings(overrides: dict[str, Any]) -> Settings:
    if not overrides:
        return settings
    if not settings.eval_mode:
        raise HTTPException(403, "overrides are only accepted when MTG_API_EVAL_MODE is on")
    unknown = sorted(set(overrides) - set(OVERRIDABLE_SETTINGS))
    if unknown:
        raise HTTPException(422, f"unknown override keys: {', '.join(unknown)}")
    # model_copy(update=) does no validation, so coerce each value against
    # its field's annotation first.
    validated = {}
    for key, value in overrides.items():
        try:
            validated[key] = TypeAdapter(Settings.model_fields[key].annotation).validate_python(value)
        except ValidationError as exc:
            raise HTTPException(422, f"invalid override {key}: {exc.errors()[0]['msg']}") from exc
    return settings.model_copy(update=validated)
```

In `query()`: `s = resolve_settings(request.overrides)`; replace every retrieval `settings.x` with `s.x`; if `set(request.overrides) & GENERATION_SETTINGS` then `answerer = build_answerer(s)`; skip `answerer.generate` when `not request.generate`; `context_hash = sha256(context)`; skip `save_history` when `request.source == "eval"`; history `model=s.ollama_model`; eval fields populated only when `settings.eval_mode`.

- [ ] **Step 1: failing tests** in `test_eval_mode.py`:
  - `test_generate_false_skips_answerer` (answerer fake that raises if called; answer is None, citations [])
  - `test_overrides_rejected_when_eval_mode_off` → 403
  - `test_unknown_override_key_is_422`, `test_bad_override_type_is_422`
  - `test_override_applies_per_request_only` (two dense points; `hybrid_top_k: 1` → 1 vector hit; next request without overrides → 2; global `settings.hybrid_top_k` unchanged)
  - `test_generation_override_builds_per_request_answerer` (monkeypatch `main.build_answerer` to record the settings it got; temperature 0 seen)
  - `test_eval_source_not_saved_to_history`, `test_normal_source_still_saved`
  - `test_context_hash_stable_and_matches_build_context`
  - `test_eval_fields_absent_when_eval_mode_off` (all three None)
  - Eval mode toggled with `monkeypatch.setattr(main.settings, "eval_mode", True)`.
- [ ] **Step 2:** run → FAIL. **Step 3:** implement. **Step 4:** full suite PASS.
- [ ] **Step 5:** commit `feat(mtg-api): per-request overrides, generate flag and eval fields on /api/v1/query`.

### Task 3: `GET /api/v1/config`, compose and env

**Files:** Modify `main.py`, `docker-compose.yml`, `.env.example`; Test `tests/test_eval_mode.py`.

**Produces:** `GET /api/v1/config` → `{"settings", "generator", "prompt_version", "collection": {"name", "points_count", "error"?}, "data_files": {"rules","cards","rulings"}}`. `CONFIG_EXPOSED_SETTINGS = OVERRIDABLE_SETTINGS + ("dense_model_name", "sparse_model_name", "ollama_url")`. Points via `client.count(collection_name=..., exact=True).count`.

- [ ] **Step 1: failing tests:** `test_config_404_outside_eval_mode`; `test_config_returns_effective_settings_and_counts` (fake client with `count`, tmp parsed dir with dated files, asserts latest names); `test_config_never_includes_secrets` (set `groq_api_key="sk-secret"`, `postgres_dsn` with password, assert neither the keys nor the values appear in `resp.text`).
- [ ] **Step 2-4:** fail → implement → pass.
- [ ] compose: `MTG_API_EVAL_MODE: ${MTG_API_EVAL_MODE:-false}`; `.env.example`: `MTG_API_EVAL_MODE=false` and `EVAL_JUDGE_BASE_URL=http://localhost:11434/v1`, `EVAL_JUDGE_MODEL=`, `EVAL_JUDGE_API_KEY=ollama` with comments.
- [ ] **Step 5:** commit `feat(mtg-api): add eval-mode GET /api/v1/config`.

### Task 4: evals package scaffold, paths, case loading and schema

**Files:** Create `evals/pyproject.toml`, `src/mtg_evals/{__init__,__main__,paths,cases}.py`, `tests/test_cases.py`, `tests/conftest.py`.

**Produces:**
- `paths.REPO_ROOT`, `EVALS_DIR`, `RUNS_DIR`, `CACHE_DIR`, `EXPERIMENTS_DIR`, `baseline_path(mode) -> Path`, `default_eval_file() -> Path` (env `EVAL_FILE`), `default_api_url()` (env `EVAL_API_URL`, `http://localhost:8000`), `default_parsed_dir()` (env `EVAL_PARSED_DIR`)
- `cases.CATEGORIES`, `cases.load_raw(path) -> list[dict]`, `cases.schema_errors(raw: list) -> list[str]`, `cases.Case` dataclass (id, question, tags, split, rules, rulings, cards, expected_cards, forbidden_cards, gold_answer, verdict, should_decline; `category` property; `has_required` property), `cases.load_cases(path) -> list[Case]` (raises `CaseFileError` with all errors), `cases.select(cases, split, tags, ids) -> list[Case]`.

- [ ] Tests: valid file loads; each schema error (missing id, bad split, bad category, unknown key, unknown required_sources key, bad verdict, duplicate id) produces a message naming the case; `select` by split/all, tag (any-of), id; the real `eval.yaml` loads cleanly (50 cases).
- [ ] fail → implement → pass (`cd evals && pip install -e ".[dev]" && python -m pytest -q`).
- [ ] commit `feat(evals): scaffold runner package with eval.yaml loading and schema checks`.

### Task 5: `validate` against parsed data

**Files:** Create `src/mtg_evals/validate.py`, `tests/test_validate.py`, `tests/fixtures/{parsed/*.jsonl, bad_eval.yaml}`.

**Produces:** `validate.validate(eval_path: Path, parsed_dir: Path) -> list[str]` (empty = clean). Uses latest `rules_*`, `cards_*`, `rulings_*` by sorted filename; rulings keyed by `oracle_id`, names mapped via cards file.

- [ ] Tests: fixture with bad rule id `999.9z`, card `Not A Card`, duplicate id, and a rulings card with no rulings → one message each; clean fixture → [].
- [ ] fail → implement → pass; commit `feat(evals): validate eval.yaml against parsed rules, cards and rulings`.

### Task 6: scoring

**Files:** Create `src/mtg_evals/scoring.py`, `tests/test_scoring.py`.

**Produces:**
- `requirement_satisfied(req_kind: str, value: str, item: dict) -> bool` (item has `source_type`, `rule_id`, `card_name`)
- `score_retrieval(case: Case, results: list[dict]) -> dict` → `{"pass", "sources_pass", "recall", "first_hit_rank", "requirement_ranks": {"rules:702.19b": 3 | None}, "matched_cards", "expected_missing", "forbidden_hits", "result_count", "context_chars"}`; `pass`/`sources_pass`/`recall` are None when `not case.has_required`, except `pass` becomes `False` when forbidden hits or expected missing exist.
- `score_citations(case, citations: list[dict], stats: dict) -> dict` → `{"cited_satisfies_required": bool | None, "invalid_citations": int}`
- `parse_verdict(answer: str | None) -> "yes" | "no" | "unclear"`

- [ ] Tests: prefix match (`702.19` satisfied by `702.19b`, not by `702.1`… note `702.1` *is* a string prefix of `702.19b` — prefix semantics as specified, test documents it); ruling needs source_type ruling; card satisfied by `card` and `oracle`, not by ruling; recall 1/2; first-hit rank min; forbidden detection only from `card_name_match`; verdict table: "Yes.", "**No**, because", "No —", "It depends", "Yes and no", "", None, "Nope" → unclear, "Not quite; ..." → unclear, "The answer is no." → no.
- [ ] fail → implement → pass; commit `feat(evals): score retrieval, citations and verdicts`.

### Task 7: caches and judge

**Files:** Create `src/mtg_evals/cache.py`, `judge.py`, tests `test_cache.py`, `test_judge.py`.

**Produces:**
- `cache.answer_key(question, context_hash, generator, prompt_version, generation_overrides: dict) -> str`
- `cache.judge_key(question, gold_answer, answer, judge_model, judge_prompt_version) -> str`
- `cache.JsonCache(root: Path, kind: str)` with `get(key) -> dict | None`, `put(key, value: dict)`
- `judge.JUDGE_PROMPT_VERSION = 1`, `judge.JudgeConfig(base_url, model, api_key)` + `from_env()`, `judge.build_messages(case, answer) -> list[dict]`, `judge.parse_grade(text, decline: bool) -> dict {"grade", "reason"}`, `judge.Judge(config, http: httpx.Client)` with `grade(case, answer) -> dict`.
- [ ] Tests: keys stable across calls and dict ordering; change each component → different key; cache roundtrip in tmp_path; parse_grade with fenced JSON, bare JSON, invalid grade → "error"; Judge posts temperature 0 to `{base}/chat/completions` (httpx.MockTransport).
- [ ] fail → implement → pass; commit `feat(evals): answer and judge caches and an OpenAI-compatible judge`.

### Task 8: API client, preflight, runner, run file

**Files:** Create `client.py`, `runner.py`, `experiments.py`, tests `test_runner.py`.

**Produces:**
- `client.ApiClient(base_url, http=None)`: `health()`, `config()`, `query(question, *, generate, overrides) -> (dict, latency_ms)`
- `client.PreflightError`, `client.preflight(api: ApiClient, eval_path: Path, mode: str, judge: JudgeConfig | None) -> dict` (returns config)
- `experiments.load_experiment(name) -> dict {"name","description","overrides"}`
- `runner.RunOptions` dataclass (mode, split, tags, ids, experiment, api_url, concurrency, eval_file)
- `runner.evaluate_case(case, api, mode, overrides, answer_cache, judge_cache, judge) -> dict`
- `runner.execute(opts) -> tuple[dict, Path]` (the run dict and the written path)
- `runner.run_filename(started: datetime, sha, dirty, mode, exp) -> str`
- [ ] Tests with a fake ApiClient: retrieval mode never calls generate=True; full mode miss → two calls and cache put, second run → cache hit, no generate call, judge cached; per-case exception recorded as `error`; filename format.
- [ ] fail → implement → pass; commit `feat(evals): preflight checks and run execution with answer caching`.

### Task 9: report, diff, exit code

**Files:** Create `report.py`, tests `test_report.py`.

**Produces:** `aggregate(cases: dict, mode) -> {"overall": {...}, "by_category": {...}}`; `diff_runs(base: dict, new: dict) -> {"regressions": [(id, reason)], "fixed": [...], "added": [...], "removed": [...], "eval_hash_changed": bool, "config_diffs": [key]}`; `render(new, base | None, diff | None) -> str`; `exit_code(diff | None) -> int`.
- [ ] Tests: retrieval pass→fail is a regression with the rank reason ("702.19b was rank 3, now missing"); fail→pass fixed; judge correct→incorrect regression; added/removed ids reported, not regressions; config diff excludes overridden keys; exit 1 with regressions, 0 without, 0 with no baseline; render includes ▲/▼ and "no baseline".
- [ ] fail → implement → pass; commit `feat(evals): aggregate, diff against baseline and render the report`.

### Task 10: CLI commands and experiments

**Files:** Create `cli.py`, `experiments/*.yaml`, tests `test_cli.py` (Typer CliRunner for `validate`, `compare`, `baseline` with tmp run files).
- [ ] `run`, `baseline`, `compare`, `sweep`, `validate` per spec; `PreflightError` → message + exit 2.
- [ ] commit `feat(evals): Typer CLI with run, baseline, compare, sweep and validate`.

### Task 11: Makefile, .gitignore, README

- [ ] Makefile per spec, `.gitignore` adds `evals/runs/`, `evals/.cache/`; README "Evals" section (enable eval mode, install, targets, baselines, cache, history exclusion, make on Windows). Commit `docs: add Makefile eval targets and README Evals section`.

### Task 12: live verification

- [ ] Rebuild `mtg-api` with `MTG_API_EVAL_MODE=true`; `validate`; retrieval dev run (no baseline); promote baseline; `--exp dense70` comparison; `full --tag negative` twice (second run all cache hits). Commit `evals/baseline-retrieval.json`. Record headline numbers and failing IDs.
