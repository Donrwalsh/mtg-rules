from datetime import UTC, datetime

import fakes
import pytest
import yaml
from fakes import FakeApi, FakeJudge, rule_result

from mtg_evals.client import PreflightError
from mtg_evals.runner import Caches, RunOptions, execute, run_filename

CASES = [
    {
        "id": "trample",
        "question": "Trample?",
        "tags": ["keyword"],
        "split": "dev",
        "required_sources": {"rules": ["702.19"]},
        "gold_answer": "Yes.",
        "verdict": "yes",
    },
    {
        "id": "ward",
        "question": "Ward?",
        "tags": ["keyword"],
        "split": "dev",
        "required_sources": {"rules": ["702.21a"]},
        "gold_answer": "It's countered unless they pay.",
    },
    {
        "id": "meta",
        "question": "Best deck?",
        "tags": ["negative"],
        "split": "dev",
        "should_decline": True,
    },
    {
        "id": "held-out",
        "question": "Held out?",
        "tags": ["keyword"],
        "split": "test",
        "required_sources": {"rules": ["100"]},
    },
]

RESULTS = {"Trample?": [rule_result("702.19b")], "Ward?": [rule_result("702.2c")]}
NOW = datetime(2026, 9, 26, 12, 30, 5, tzinfo=UTC)


@pytest.fixture
def eval_file(tmp_path):
    path = tmp_path / "eval.yaml"
    path.write_text(yaml.safe_dump(CASES), encoding="utf-8")
    return path


def _run(tmp_path, eval_file, api, mode="retrieval", judge=None, **opts):
    opts.setdefault("allow_gemini", True)
    options = RunOptions(mode=mode, eval_file=eval_file, api_url="http://fake", **opts)
    return execute(
        options,
        api=api,
        judge=judge,
        caches=Caches.at(tmp_path / "cache"),
        runs_dir=tmp_path / "runs",
        now=NOW,
        git=("abc1234", False),
    )


def test_run_filename():
    assert run_filename(NOW, "abc1234", False, "retrieval", None) == (
        "20260926T123005Z-abc1234-retrieval.json"
    )
    assert run_filename(NOW, "abc1234", True, "full", "dense70") == (
        "20260926T123005Z-abc1234-dirty-full-dense70.json"
    )


def test_retrieval_run_never_generates_and_writes_a_run_file(tmp_path, eval_file):
    api = FakeApi(RESULTS)
    run, path = _run(tmp_path, eval_file, api)

    assert path == tmp_path / "runs" / "20260926T123005Z-abc1234-retrieval.json"
    assert path.exists()
    assert all(call["generate"] is False for call in api.calls)
    assert set(run["cases"]) == {"trample", "ward", "meta"}  # dev split only
    assert run["cases"]["trample"]["retrieval"]["pass"] is True
    assert run["cases"]["ward"]["retrieval"]["pass"] is False
    assert run["cases"]["meta"]["retrieval"]["pass"] is None
    assert run["cases"]["trample"]["results"][0]["rule_id"] == "702.19b"
    assert run["aggregates"]["overall"]["pass_rate"] == 0.5
    meta = run["metadata"]
    assert meta["git_sha"] == "abc1234"
    assert meta["api_config"]["collection"]["points_count"] == 1000
    assert len(meta["eval_sha256"]) == 64


def test_overrides_are_sent_on_every_call(tmp_path, eval_file):
    api = FakeApi(RESULTS)
    _run(tmp_path, eval_file, api, experiment="topk15", overrides={"hybrid_top_k": 15})
    assert all(call["overrides"] == {"hybrid_top_k": 15} for call in api.calls)


def test_full_run_generates_then_hits_both_caches(tmp_path, eval_file):
    api, judge = FakeApi(RESULTS), FakeJudge()
    first, _ = _run(tmp_path, eval_file, api, mode="full", judge=judge)

    assert [c["generate"] for c in api.calls].count(True) == 3
    trample = first["cases"]["trample"]["full"]
    assert trample["answer_cached"] is False
    assert trample["judge"] == {"grade": "correct", "reason": "Fake."}
    assert trample["verdict_parsed"] == "yes"
    assert trample["verdict_correct"] is True
    assert trample["citations"]["cited_satisfies_required"] is True
    assert first["cases"]["meta"]["full"]["judge"]["grade"] == "declined_properly"
    assert first["aggregates"]["overall"]["answer_cache_hit_rate"] == 0.0

    api2, judge2 = FakeApi(RESULTS), FakeJudge()
    second, _ = _run(tmp_path, eval_file, api2, mode="full", judge=judge2)

    assert all(call["generate"] is False for call in api2.calls)
    assert judge2.calls == []
    assert all(c["full"]["answer_cached"] for c in second["cases"].values())
    assert all(c["full"]["judge_cached"] for c in second["cases"].values())
    assert second["aggregates"]["overall"]["answer_cache_hit_rate"] == 1.0
    assert second["aggregates"]["overall"]["judge_cache_hit_rate"] == 1.0


def test_generation_override_misses_the_answer_cache(tmp_path, eval_file):
    _run(tmp_path, eval_file, FakeApi(RESULTS), mode="full", judge=FakeJudge())
    api = FakeApi(RESULTS)
    _run(
        tmp_path,
        eval_file,
        api,
        mode="full",
        judge=FakeJudge(),
        overrides={"generation_temperature": 0},
    )
    assert [c["generate"] for c in api.calls].count(True) == 3


def test_failed_generation_is_not_cached_and_grades_no_answer(tmp_path, eval_file):
    api = FakeApi(RESULTS, answer=None)
    run, _ = _run(tmp_path, eval_file, api, mode="full", judge=FakeJudge(), ids=["trample"])
    assert run["cases"]["trample"]["full"]["judge"]["grade"] == "no_answer"
    assert not (tmp_path / "cache" / "answers").exists()


def test_a_failing_case_is_recorded_not_raised(tmp_path, eval_file):
    class FlakyApi(FakeApi):
        def query(self, question, *, generate, overrides):
            if question == "Ward?":
                raise RuntimeError("boom")
            return super().query(question, generate=generate, overrides=overrides)

    run, _ = _run(tmp_path, eval_file, FlakyApi(RESULTS))
    assert run["cases"]["ward"]["error"] == "RuntimeError: boom"
    assert run["aggregates"]["overall"]["errors"] == 1
    assert run["aggregates"]["overall"]["pass_rate"] == 0.5


@pytest.mark.parametrize(
    ("api", "mode", "message"),
    [
        (FakeApi(health={"status": "ok", "qdrant": "unreachable"}), "retrieval", "qdrant"),
        (FakeApi(config_status=404), "retrieval", "eval mode is off"),
        (FakeApi(config={"collection": {"name": "c", "points_count": 0}}), "retrieval", "empty"),
    ],
)
def test_preflight_failures(tmp_path, eval_file, api, mode, message):
    with pytest.raises(PreflightError, match=message):
        _run(tmp_path, eval_file, api, mode=mode)


def test_full_mode_requires_a_judge_model(tmp_path, eval_file):
    class NoModelJudge(FakeJudge):
        class config:
            model = ""

    with pytest.raises(PreflightError, match="EVAL_JUDGE_MODEL"):
        _run(tmp_path, eval_file, FakeApi(RESULTS), mode="full", judge=NoModelJudge())


def test_schema_errors_fail_preflight(tmp_path):
    path = tmp_path / "eval.yaml"
    path.write_text(yaml.safe_dump([{"id": "x"}]), encoding="utf-8")
    with pytest.raises(PreflightError, match="schema validation"):
        _run(tmp_path, path, FakeApi())


def test_no_matching_cases_fails(tmp_path, eval_file):
    with pytest.raises(PreflightError, match="no cases match"):
        _run(tmp_path, eval_file, FakeApi(), ids=["nope"])


def test_rejected_overrides_stop_the_run_before_it_starts(tmp_path, eval_file):
    from mtg_evals.client import ApiError

    class RejectingApi(FakeApi):
        def query(self, question, *, generate, overrides):
            self.calls.append(question)
            raise ApiError("HTTP 422: unknown override keys: hybrid_topk")

    api = RejectingApi(RESULTS)
    with pytest.raises(PreflightError, match="rejected the overrides.*hybrid_topk"):
        _run(tmp_path, eval_file, api, overrides={"hybrid_topk": 15})
    assert len(api.calls) == 1


def test_full_run_records_token_usage_and_keeps_it_through_the_cache(tmp_path, eval_file):
    expected = {"input_tokens": 100, "output_tokens": 20, "thinking_tokens": 30}
    first, _ = _run(tmp_path, eval_file, FakeApi(RESULTS), mode="full", judge=FakeJudge())
    assert first["cases"]["trample"]["full"]["usage"] == expected

    second, _ = _run(tmp_path, eval_file, FakeApi(RESULTS), mode="full", judge=FakeJudge())
    assert second["cases"]["trample"]["full"]["answer_cached"] is True
    assert second["cases"]["trample"]["full"]["usage"] == expected


def test_thinking_level_is_a_generation_key():
    from mtg_evals.runner import GENERATION_KEYS

    assert "generation_thinking_level" in GENERATION_KEYS


def test_full_run_generates_every_answer_before_judging_any(tmp_path, eval_file):
    log = []

    class LoggingApi(FakeApi):
        def query(self, question, *, generate, overrides):
            if generate:
                log.append("generate")
            return super().query(question, generate=generate, overrides=overrides)

    class LoggingJudge(FakeJudge):
        def grade(self, case, answer):
            log.append("judge")
            return super().grade(case, answer)

    _run(tmp_path, eval_file, LoggingApi(RESULTS), mode="full", judge=LoggingJudge())
    assert log == ["generate"] * 3 + ["judge"] * 3


def test_fresh_answers_generate_again_but_still_fill_the_cache(tmp_path, eval_file):
    _run(tmp_path, eval_file, FakeApi(RESULTS), mode="full", judge=FakeJudge())
    api = FakeApi(RESULTS)
    run, _ = _run(tmp_path, eval_file, api, mode="full", judge=FakeJudge(), fresh_answers=True)
    assert [c["generate"] for c in api.calls].count(True) == 3
    assert not any(c["full"]["answer_cached"] for c in run["cases"].values())


def test_full_run_against_gemini_needs_allow_gemini(tmp_path, eval_file):
    with pytest.raises(PreflightError, match="--allow-gemini"):
        _run(
            tmp_path,
            eval_file,
            FakeApi(RESULTS),
            mode="full",
            judge=FakeJudge(),
            allow_gemini=False,
        )


def test_retrieval_run_against_gemini_is_not_guarded(tmp_path, eval_file):
    _run(tmp_path, eval_file, FakeApi(RESULTS), allow_gemini=False)


def test_local_generator_needs_no_flag(tmp_path, eval_file):
    config = dict(fakes.CONFIG, generator="ollama:phi4:latest")
    _run(
        tmp_path,
        eval_file,
        FakeApi(RESULTS, config=config),
        mode="full",
        judge=FakeJudge(),
        allow_gemini=False,
    )


def test_context_overflow_is_flagged_and_counted(tmp_path, eval_file):
    api = FakeApi(
        RESULTS,
        answer=None,
        generation_error="context overflow: prompt of 7000 tokens exceeds num_ctx 6144",
    )
    run, _ = _run(tmp_path, eval_file, api, mode="full", judge=FakeJudge(), ids=["trample"])
    full = run["cases"]["trample"]["full"]
    assert full["context_overflow"] is True
    assert full["generation_error"].startswith("context overflow:")
    assert run["aggregates"]["overall"]["context_overruns"] == 1


def test_max_prompt_tokens_and_context_hash_are_recorded(tmp_path, eval_file):
    run, _ = _run(tmp_path, eval_file, FakeApi(RESULTS), mode="full", judge=FakeJudge())
    assert run["aggregates"]["overall"]["max_prompt_tokens"] == 100
    assert run["aggregates"]["overall"]["context_overruns"] == 0
    assert len(run["cases"]["trample"]["context_hash"]) == 64
