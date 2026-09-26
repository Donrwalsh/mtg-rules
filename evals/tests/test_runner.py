from datetime import UTC, datetime

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
    {"id": "meta", "question": "Best deck?", "tags": ["negative"], "split": "dev",
     "should_decline": True},
    {"id": "held-out", "question": "Held out?", "tags": ["keyword"], "split": "test",
     "required_sources": {"rules": ["100"]}},
]

RESULTS = {"Trample?": [rule_result("702.19b")], "Ward?": [rule_result("702.2c")]}
NOW = datetime(2026, 9, 26, 12, 30, 5, tzinfo=UTC)


@pytest.fixture
def eval_file(tmp_path):
    path = tmp_path / "eval.yaml"
    path.write_text(yaml.safe_dump(CASES), encoding="utf-8")
    return path


def _run(tmp_path, eval_file, api, mode="retrieval", judge=None, **opts):
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
    _run(tmp_path, eval_file, api, mode="full", judge=FakeJudge(),
         overrides={"generation_temperature": 0})
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
