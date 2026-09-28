from __future__ import annotations

from statistics import mean

# Judge grades, best first. "no_answer" (generation failed) ranks with incorrect.
ANSWER_ORDER = {"correct": 2, "partial": 1, "incorrect": 0, "no_answer": 0}
DECLINE_ORDER = {"declined_properly": 1, "answered_anyway": 0, "no_answer": 0}


def _mean(values: list[float]) -> float | None:
    return mean(values) if values else None


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def is_pass(case: dict) -> bool | None:
    """Retrieval pass, with API errors counted as failures."""
    if case.get("error"):
        return False
    return case["retrieval"]["pass"]


def _retrieval(cases: list[dict]) -> dict:
    passes = [p for p in (is_pass(c) for c in cases) if p is not None]
    ok = [c["retrieval"] for c in cases if not c.get("error")]
    return {
        "cases": len(cases),
        "errors": sum(1 for c in cases if c.get("error")),
        "scored": len(passes),
        "passed": sum(passes),
        "pass_rate": _rate(sum(passes), len(passes)),
        "mean_recall": _mean([r["recall"] for r in ok if r["recall"] is not None]),
        "mean_first_hit_rank": _mean(
            [r["first_hit_rank"] for r in ok if r["first_hit_rank"] is not None]
        ),
        "forbidden_hits": sum(len(r["forbidden_hits"]) for r in ok),
        "expected_missing": sum(len(r["expected_missing"]) for r in ok),
        "mean_results": _mean([r["result_count"] for r in ok]),
        "mean_context_chars": _mean([r["context_chars"] for r in ok]),
    }


def _full(cases: list[dict]) -> dict:
    full = [c["full"] for c in cases if c.get("full")]
    graded = [f for f in full if f["judge"] and not f["should_decline"]]
    grades = [f["judge"]["grade"] for f in graded if f["judge"]["grade"] != "error"]
    declines = [f for f in full if f["should_decline"] and f["judge"]]
    decline_grades = [f["judge"]["grade"] for f in declines if f["judge"]["grade"] != "error"]
    verdicts = [f for f in full if f["verdict_expected"]]
    judged = [f for f in full if f["judge"] and f["judge"]["grade"] != "no_answer"]
    return {
        "answered": sum(1 for f in full if f["answer"] is not None),
        "judge_errors": sum(1 for f in full if f["judge"] and f["judge"]["grade"] == "error"),
        "judged": len(grades),
        "correct_rate": _rate(grades.count("correct"), len(grades)),
        "partial_rate": _rate(grades.count("partial"), len(grades)),
        "incorrect_rate": _rate(grades.count("incorrect") + grades.count("no_answer"), len(grades)),
        "verdict_accuracy": _rate(sum(1 for f in verdicts if f["verdict_correct"]), len(verdicts)),
        "decline_accuracy": _rate(decline_grades.count("declined_properly"), len(decline_grades)),
        "answer_cache_hit_rate": _rate(sum(1 for f in full if f["answer_cached"]), len(full)),
        "judge_cache_hit_rate": _rate(sum(1 for f in judged if f["judge_cached"]), len(judged)),
        "invalid_citations": sum(f["citations"]["invalid_citations"] for f in full),
        "mean_generate_ms": _mean([f["generate_ms"] for f in full if f["generate_ms"] is not None]),
    }


def aggregate(cases: dict[str, dict], mode: str) -> dict:
    rows = list(cases.values())
    overall = _retrieval(rows)
    if mode == "full":
        overall |= _full(rows)
    by_category: dict[str, dict] = {}
    for category in sorted({c["category"] for c in rows}):
        subset = [c for c in rows if c["category"] == category]
        stats = _retrieval(subset)
        by_category[category] = {k: stats[k] for k in ("cases", "scored", "passed", "pass_rate")}
        if mode == "full":
            full = _full(subset)
            by_category[category] |= {
                "correct_rate": full["correct_rate"],
                "decline_accuracy": full["decline_accuracy"],
            }
    return {"overall": overall, "by_category": by_category}
