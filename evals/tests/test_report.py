import copy

from mtg_evals.metrics import aggregate
from mtg_evals.report import arrow, diff_runs, exit_code, render


def _case(ranks, passed=None, forbidden=(), missing=(), category="keyword", full=None, error=None):
    recall = sum(r is not None for r in ranks.values()) / len(ranks) if ranks else None
    hits = [r for r in ranks.values() if r is not None]
    return {
        "category": category,
        "error": error,
        "retrieval": {
            "pass": passed if passed is not None else (all(hits) and len(hits) == len(ranks)),
            "recall": recall,
            "first_hit_rank": min(hits) if hits else None,
            "requirement_ranks": ranks,
            "forbidden_hits": list(forbidden),
            "expected_missing": list(missing),
            "result_count": 5,
            "context_chars": 100,
        },
        "full": full,
    }


def _full(grade, decline=False, verdict=None):
    return {
        "answer": "Yes.",
        "answer_cached": False,
        "should_decline": decline,
        "verdict_expected": "yes" if verdict is not None else None,
        "verdict_parsed": verdict,
        "verdict_correct": (verdict == "yes") if verdict is not None else None,
        "judge": {"grade": grade, "reason": "r"},
        "judge_cached": False,
        "citations": {"invalid_citations": 0},
        "generate_ms": 1000.0,
    }


def _run(cases, mode="retrieval", overrides=None, settings=None, eval_hash="h", **meta):
    return {
        "metadata": {
            "git_sha": "abc1234",
            "dirty": False,
            "mode": mode,
            "split": "dev",
            "tags": [],
            "ids": [],
            "experiment": None,
            "overrides": overrides or {},
            "eval_sha256": eval_hash,
            "judge_model": "judge" if mode == "full" else None,
            "api_config": {
                "settings": settings or {"hybrid_top_k": 10, "hybrid_dense_weight": 0.5},
                "generator": "gemini:gemini-3.5-flash",
                "prompt_version": 1,
                "collection": {"name": "mtg_rules", "points_count": 1000},
                "data_files": {"rules": "rules_2026-08-25.jsonl"},
            },
            **meta,
        },
        "cases": cases,
        "aggregates": aggregate(cases, mode),
    }


BASE = {
    "trample": _case({"rules:702.19b": 3}),
    "ward": _case({"rules:702.21a": None}),
    "hexproof": _case({"rules:702.11b": 1}, forbidden=["Lightning"], passed=False),
}


def test_lost_requirement_is_a_regression_with_its_old_rank():
    new = copy.deepcopy(BASE)
    new["trample"] = _case({"rules:702.19b": None})
    diff = diff_runs(_run(BASE), _run(new))
    assert diff.regressions == [("trample", "702.19b was rank 3, now missing")]
    assert diff.fixed == []
    assert exit_code(diff) == 1


def test_found_requirement_and_cleared_forbidden_card_are_fixed():
    new = copy.deepcopy(BASE)
    new["ward"] = _case({"rules:702.21a": 2})
    new["hexproof"] = _case({"rules:702.11b": 1})
    diff = diff_runs(_run(BASE), _run(new))
    assert diff.fixed == [
        ("ward", "702.21a now found at rank 2"),
        ("hexproof", "forbidden card 'Lightning' no longer matched"),
    ]
    assert diff.regressions == []
    assert exit_code(diff) == 0


def test_rank_shuffles_are_not_regressions():
    new = copy.deepcopy(BASE)
    new["trample"] = _case({"rules:702.19b": 7})
    diff = diff_runs(_run(BASE), _run(new))
    assert diff.regressions == diff.fixed == []


def test_new_forbidden_card_and_lost_expected_card_regress():
    new = copy.deepcopy(BASE)
    new["trample"] = _case({"rules:702.19b": 3}, forbidden=["Trample"], missing=["Dreadmaw"])
    [(case_id, reason)] = diff_runs(_run(BASE), _run(new)).regressions
    assert case_id == "trample"
    assert "forbidden card 'Trample' now matched" in reason
    assert "expected card 'Dreadmaw' no longer matched" in reason


def test_api_error_is_a_regression():
    new = copy.deepcopy(BASE)
    new["trample"] = {**new["trample"], "error": "ApiError: HTTP 500", "retrieval": None}
    diff = diff_runs(_run(BASE), _run(new))
    assert diff.regressions == [("trample", "API error: ApiError: HTTP 500")]


def test_added_and_removed_cases_are_reported_not_regressions():
    new = copy.deepcopy(BASE)
    del new["ward"]
    new["brand-new"] = _case({"rules:100": None})
    diff = diff_runs(_run(BASE), _run(new))
    assert diff.added == ["brand-new"]
    assert diff.removed == ["ward"]
    assert diff.regressions == []
    text = render(_run(new), _run(BASE), diff)
    assert "cases not in baseline: brand-new" in text
    assert "baseline cases missing from this run: ward" in text


def test_filtered_run_does_not_report_removed_cases():
    new = {"trample": BASE["trample"]}
    diff = diff_runs(_run(BASE), _run(new, tags=["keyword"]))
    assert diff.removed == []
    assert diff.shared == 1


def test_judge_and_verdict_changes_in_full_mode():
    base = {
        "a": _case({"rules:1": 1}, full=_full("correct", verdict="yes")),
        "b": _case({"rules:1": 1}, full=_full("incorrect")),
        "neg": _case({}, category="negative", full=_full("declined_properly", decline=True)),
    }
    new = {
        "a": _case({"rules:1": 1}, full=_full("incorrect", verdict="unclear")),
        "b": _case({"rules:1": 1}, full=_full("partial")),
        "neg": _case({}, category="negative", full=_full("answered_anyway", decline=True)),
    }
    diff = diff_runs(_run(base, mode="full"), _run(new, mode="full"))
    assert diff.regressions == [
        ("a", "judge: correct → incorrect; verdict: yes → unclear"),
        ("neg", "judge: declined_properly → answered_anyway"),
    ]
    assert diff.fixed == [("b", "judge: incorrect → partial")]


def test_eval_hash_change_is_flagged():
    diff = diff_runs(_run(BASE, eval_hash="a"), _run(BASE, eval_hash="b"))
    assert diff.eval_hash_changed
    assert "eval.yaml has changed" in render(_run(BASE), _run(BASE), diff)


def test_config_diff_ignores_keys_the_experiment_overrode():
    base = _run(BASE)
    new = _run(
        BASE,
        overrides={"hybrid_dense_weight": 0.7},
        settings={"hybrid_top_k": 15, "hybrid_dense_weight": 0.5},
    )
    diff = diff_runs(base, new)
    assert diff.config_diffs == [("settings.hybrid_top_k", 10, 15)]
    assert "settings.hybrid_top_k: 10 → 15" in render(new, base, diff)


def test_no_baseline_says_so_and_exits_zero():
    text = render(_run(BASE))
    assert "no baseline for retrieval; skipping comparison" in text
    assert exit_code(None) == 0


def test_render_shows_arrows_categories_and_failing_cases():
    new = copy.deepcopy(BASE)
    new["ward"] = _case({"rules:702.21a": 2})
    base_run, new_run = _run(BASE), _run(new)
    text = render(new_run, base_run, diff_runs(base_run, new_run))
    assert text.splitlines()[0] == (
        "eval  retrieval · dev (3) · abc1234 · mtg_rules · no experiment"
    )
    assert "pass rate" in text and "▲" in text
    assert "keyword" in text and "1/3 →" in text
    assert "failing retrieval (1): hexproof" in text
    assert "FIXED (1)" in text


def test_arrow_direction():
    assert arrow(0.5, 0.6, 1, "pct") == "▲"
    assert arrow(0.5, 0.4, 1, "pct") == "▼"
    assert arrow(2.0, 3.0, -1, "f2") == "▼"
    assert arrow(2.0, 2.0, -1, "f2") == ""
    assert arrow(None, 2.0, 1, "f2") == ""
    assert arrow(1, 2, 0, "int") == ""


def test_judge_same_as_generator_warns():
    run = _run(BASE, mode="full")
    run["metadata"]["judge_model"] = "gemini-3.5-flash"
    assert "WARNING: the judge (gemini-3.5-flash) is the generator model" in render(run)
