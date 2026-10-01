from __future__ import annotations

import hashlib
import json
import subprocess
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from mtg_evals.cache import JsonCache, answer_key, judge_key
from mtg_evals.cases import Case, select
from mtg_evals.client import ApiClient, ApiError, PreflightError, preflight
from mtg_evals.judge import JUDGE_PROMPT_VERSION, Judge, JudgeConfig, is_judged
from mtg_evals.metrics import aggregate
from mtg_evals.paths import CACHE_DIR, REPO_ROOT, RUNS_DIR
from mtg_evals.scoring import parse_verdict, score_citations, score_retrieval

# Overrides that change the answer without changing the retrieved context
# (mirrors mtg_api.config.GENERATION_SETTINGS). Only these join the answer
# cache key; retrieval overrides are already captured by context_hash.
GENERATION_KEYS = frozenset(
    {
        "gemini_model",
        "generation_temperature",
        "generation_max_tokens",
        "generation_thinking_level",
    }
)

DEFAULT_CONCURRENCY = {"retrieval": 4, "full": 1}

_RESULT_FIELDS = ("source_type", "rule_id", "card_name", "oracle_id", "match_type")


@dataclass
class RunOptions:
    mode: str
    eval_file: Path
    api_url: str
    split: str = "dev"
    tags: list[str] = field(default_factory=list)
    ids: list[str] = field(default_factory=list)
    experiment: str | None = None
    overrides: dict = field(default_factory=dict)
    concurrency: int | None = None


@dataclass
class Caches:
    answers: JsonCache
    judge: JsonCache

    @classmethod
    def at(cls, root: Path) -> Caches:
        return cls(JsonCache(root, "answers"), JsonCache(root, "judge"))


def result_identifiers(results: list[dict]) -> list[dict]:
    return [
        {"rank": rank, **{k: r.get(k) for k in _RESULT_FIELDS}, "score": r.get("score")}
        for rank, r in enumerate(results, start=1)
    ]


def _judge(case: Case, answer: str | None, judge: Judge, cache: JsonCache) -> tuple:
    """(judge result | None, cached, latency_ms)."""
    if not is_judged(case):
        return None, False, None
    if answer is None:
        return {"grade": "no_answer", "reason": "answer generation failed"}, False, None
    key = judge_key(
        case.question,
        case.gold_answer,
        answer,
        judge.config.model,
        JUDGE_PROMPT_VERSION,
        case.should_decline,
    )
    cached = cache.get(key)
    if cached is not None:
        return cached, True, None
    started = time.perf_counter()
    result = judge.grade(case, answer)
    latency_ms = (time.perf_counter() - started) * 1000
    # An unparseable reply is worth retrying next run, not caching.
    if result["grade"] != "error":
        cache.put(key, result)
    return result, False, latency_ms


def _retrieve(case: Case, api: ApiClient, overrides: dict) -> tuple[dict, dict | None]:
    """The case record with its retrieval scored, and the API response (None
    when the call failed; the record then carries the error)."""
    record = {
        "id": case.id,
        "category": case.category,
        "tags": list(case.tags),
        "question": case.question,
        "retrieval": None,
        "results": [],
        "retrieve_ms": None,
        "full": None,
        "error": None,
    }
    try:
        response, record["retrieve_ms"] = api.query(
            case.question, generate=False, overrides=overrides
        )
        record["retrieval"] = score_retrieval(case, response["results"])
        record["results"] = result_identifiers(response["results"])
    except Exception as exc:  # noqa: BLE001 -- one bad case must not sink the run
        record["error"] = f"{exc.__class__.__name__}: {exc}"
        return record, None
    return record, response


def _generate(
    case: Case,
    retrieval_response: dict,
    api: ApiClient,
    overrides: dict,
    caches: Caches,
) -> dict:
    """One case's answer: from the cache, or generated now."""
    generation_overrides = {k: v for k, v in overrides.items() if k in GENERATION_KEYS}
    key = answer_key(
        case.question,
        retrieval_response["context_hash"],
        retrieval_response["generator"],
        retrieval_response["prompt_version"],
        generation_overrides,
    )
    cached = caches.answers.get(key)
    context_drift = False
    generate_ms = None
    if cached is not None:
        entry = cached
    else:
        response, generate_ms = api.query(case.question, generate=True, overrides=overrides)
        entry = {
            "answer": response["answer"],
            "citations": response.get("citations") or [],
            "citation_stats": response.get("citation_stats") or {},
            "usage": response.get("usage"),
        }
        # Retrieval is deterministic; a different context between the two
        # calls means the answer doesn't belong to the scored context.
        context_drift = response["context_hash"] != retrieval_response["context_hash"]
        if entry["answer"] is not None and not context_drift:
            caches.answers.put(key, entry)
    return {
        "entry": entry,
        "answer_cached": cached is not None,
        "context_drift": context_drift,
        "generator": retrieval_response["generator"],
        "generate_ms": generate_ms,
    }


def _grade(case: Case, generated: dict, judge: Judge, caches: Caches) -> dict:
    """The case's `full` record: the answer from _generate, judged and scored."""
    entry = generated["entry"]
    answer = entry["answer"]
    judged, judge_cached, judge_ms = _judge(case, answer, judge, caches.judge)
    verdict_parsed = parse_verdict(answer) if case.verdict else None
    return {
        "answer": answer,
        "answer_cached": generated["answer_cached"],
        "context_drift": generated["context_drift"],
        "generator": generated["generator"],
        "should_decline": case.should_decline,
        "verdict_expected": case.verdict,
        "verdict_parsed": verdict_parsed,
        "verdict_correct": verdict_parsed == case.verdict if case.verdict else None,
        "judge": judged,
        "judge_cached": judge_cached,
        "citations": score_citations(case, entry["citations"], entry["citation_stats"]),
        "generate_ms": generated["generate_ms"],
        "judge_ms": judge_ms,
        "usage": entry.get("usage"),
    }


def _answer_case(
    case: Case, api: ApiClient, opts: RunOptions, caches: Caches
) -> tuple[dict, dict | None]:
    """Pass 1: retrieval, plus the answer in full mode."""
    record, response = _retrieve(case, api, opts.overrides)
    if opts.mode != "full" or response is None:
        return record, None
    try:
        return record, _generate(case, response, api, opts.overrides, caches)
    except Exception as exc:  # noqa: BLE001 -- one bad case must not sink the run
        record["error"] = f"{exc.__class__.__name__}: {exc}"
        return record, None


def _grade_case(
    case: Case, record: dict, generated: dict | None, judge: Judge, caches: Caches
) -> dict:
    """Pass 2 (full mode): grade the answer pass 1 wrote."""
    if generated is not None:
        try:
            record["full"] = _grade(case, generated, judge, caches)
        except Exception as exc:  # noqa: BLE001 -- one bad case must not sink the run
            record["error"] = f"{exc.__class__.__name__}: {exc}"
    return record


def git_info(root: Path = REPO_ROOT) -> tuple[str, bool]:
    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=root, capture_output=True, text=True, check=False
        ).stdout.strip()

    return git("rev-parse", "--short=7", "HEAD") or "nogit", bool(git("status", "--porcelain"))


def run_filename(started: datetime, sha: str, dirty: bool, mode: str, exp: str | None) -> str:
    parts = [started.strftime("%Y%m%dT%H%M%SZ"), sha]
    if dirty:
        parts.append("dirty")
    parts.append(mode)
    if exp:
        parts.append(exp)
    return "-".join(parts) + ".json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def execute(
    opts: RunOptions,
    api: ApiClient | None = None,
    judge: Judge | None = None,
    caches: Caches | None = None,
    runs_dir: Path = RUNS_DIR,
    progress: Callable[[str], None] | None = None,
    now: datetime | None = None,
    git: tuple[str, bool] | None = None,
) -> tuple[dict, Path]:
    api = api or ApiClient(opts.api_url)
    if opts.mode == "full" and judge is None:
        judge = Judge(JudgeConfig.from_env())
    all_cases, api_config = preflight(api, opts.eval_file, opts.mode, judge and judge.config)
    cases = select(all_cases, opts.split, opts.tags, opts.ids)
    if not cases:
        raise PreflightError(
            f"no cases match split={opts.split} tags={opts.tags or '-'} ids={opts.ids or '-'}"
        )
    if opts.overrides:
        # One cheap retrieval-only probe: a rejected key (403/422) should stop
        # the run up front, not show up as an error on every case.
        try:
            api.query(cases[0].question, generate=False, overrides=opts.overrides)
        except ApiError as exc:
            raise PreflightError(f"the API rejected the overrides: {exc}") from exc
    caches = caches or Caches.at(CACHE_DIR)
    started = now or datetime.now(UTC)
    sha, dirty = git or git_info()
    concurrency = opts.concurrency or DEFAULT_CONCURRENCY[opts.mode]

    def answer(case: Case) -> tuple[dict, dict | None]:
        record, generated = _answer_case(case, api, opts, caches)
        if progress and opts.mode == "full":
            progress(_answer_progress_line(record, generated))
        return record, generated

    # Full mode runs in two passes: every answer, then every grade. One GPU
    # can't keep the generator and the judge loaded at once (phi4 and
    # qwen2.5:14b are ~9 GB each on a 10 GB card), and alternating them
    # reloads a model twice per case, ~25 s each time.
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        answered = list(pool.map(answer, cases))

    records = []
    for case, (record, generated) in zip(cases, answered, strict=True):
        if opts.mode == "full":
            record = _grade_case(case, record, generated, judge, caches)
        if progress:
            progress(_progress_line(record))
        records.append(record)

    by_id = {r["id"]: r for r in records}
    run = {
        "metadata": {
            "started_at": started.isoformat(),
            "git_sha": sha,
            "dirty": dirty,
            "mode": opts.mode,
            "split": opts.split,
            "tags": opts.tags,
            "ids": opts.ids,
            "experiment": opts.experiment,
            "overrides": opts.overrides,
            "api_config": api_config,
            "eval_file": str(opts.eval_file),
            "eval_sha256": _sha256(opts.eval_file),
            "judge_model": judge.config.model if judge else None,
            "judge_prompt_version": JUDGE_PROMPT_VERSION if judge else None,
            "concurrency": concurrency,
        },
        "cases": by_id,
        "aggregates": aggregate(by_id, opts.mode),
    }
    runs_dir.mkdir(parents=True, exist_ok=True)
    path = runs_dir / run_filename(started, sha, dirty, opts.mode, opts.experiment)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(run, fh, ensure_ascii=False, indent=2)
    return run, path


def _progress_line(record: dict) -> str:
    if record["error"]:
        status = f"ERROR {record['error']}"
    else:
        passed = record["retrieval"]["pass"]
        status = {True: "pass", False: "FAIL", None: "-"}[passed]
        full = record["full"]
        if full:
            grade = (full["judge"] or {}).get("grade", "-")
            cache = "cached" if full["answer_cached"] else "generated"
            status += f"  judge={grade} ({cache})"
    return f"  {record['id']:<40} {status}"


def _answer_progress_line(record: dict, generated: dict | None) -> str:
    if generated is None:
        status = f"ERROR {record['error']}" if record["error"] else "no answer"
    else:
        status = "answer cached" if generated["answer_cached"] else "answer generated"
    return f"  {record['id']:<40} {status}"
