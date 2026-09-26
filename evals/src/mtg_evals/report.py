from __future__ import annotations

from dataclasses import dataclass, field

from mtg_evals.metrics import ANSWER_ORDER, DECLINE_ORDER, aggregate


@dataclass
class Diff:
    regressions: list[tuple[str, str]] = field(default_factory=list)
    fixed: list[tuple[str, str]] = field(default_factory=list)
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    shared: int = 0
    filtered: bool = False
    eval_hash_changed: bool = False
    mode_mismatch: bool = False
    config_diffs: list[tuple[str, object, object]] = field(default_factory=list)


def _requirement_name(label: str) -> str:
    kind, _, value = label.partition(":")
    return value if kind == "rules" else f"{value} ({kind})"


def _retrieval_changes(base: dict, new: dict) -> tuple[list[str], list[str]]:
    worse: list[str] = []
    better: list[str] = []
    if new.get("error") and not base.get("error"):
        return [f"API error: {new['error']}"], []
    if base.get("error") and not new.get("error"):
        better.append("API error cleared")
    b, n = base.get("retrieval"), new.get("retrieval")
    if not b or not n:
        return worse, better
    for label, new_rank in n["requirement_ranks"].items():
        if label not in b["requirement_ranks"]:
            continue
        old_rank = b["requirement_ranks"][label]
        name = _requirement_name(label)
        if old_rank is not None and new_rank is None:
            worse.append(f"{name} was rank {old_rank}, now missing")
        elif old_rank is None and new_rank is not None:
            better.append(f"{name} now found at rank {new_rank}")
    for card in sorted(set(n["forbidden_hits"]) - set(b["forbidden_hits"])):
        worse.append(f"forbidden card {card!r} now matched")
    for card in sorted(set(b["forbidden_hits"]) - set(n["forbidden_hits"])):
        better.append(f"forbidden card {card!r} no longer matched")
    for card in sorted(set(n["expected_missing"]) - set(b["expected_missing"])):
        worse.append(f"expected card {card!r} no longer matched")
    for card in sorted(set(b["expected_missing"]) - set(n["expected_missing"])):
        better.append(f"expected card {card!r} now matched")
    if b["pass"] is True and n["pass"] is False and not worse:
        worse.append("retrieval pass → fail")
    if b["pass"] is False and n["pass"] is True and not better:
        better.append("retrieval fail → pass")
    return worse, better


def _full_changes(base: dict, new: dict) -> tuple[list[str], list[str]]:
    worse: list[str] = []
    better: list[str] = []
    b, n = base.get("full"), new.get("full")
    if not b or not n:
        return worse, better
    bj, nj = b.get("judge"), n.get("judge")
    if bj and nj:
        order = DECLINE_ORDER if n["should_decline"] else ANSWER_ORDER
        old, cur = bj["grade"], nj["grade"]
        if old in order and cur in order and old != cur:
            reason = f"judge: {old} → {cur}"
            (worse if order[cur] < order[old] else better).append(reason)
    if b.get("verdict_correct") is True and n.get("verdict_correct") is False:
        worse.append(f"verdict: {b['verdict_parsed']} → {n['verdict_parsed']}")
    if b.get("verdict_correct") is False and n.get("verdict_correct") is True:
        better.append(f"verdict: {b['verdict_parsed']} → {n['verdict_parsed']}")
    return worse, better


def _effective_settings(run: dict) -> dict:
    meta = run["metadata"]
    return {**meta["api_config"].get("settings", {}), **(meta.get("overrides") or {})}


def _config_view(run: dict) -> dict:
    meta = run["metadata"]
    config = meta["api_config"]
    view = {f"settings.{k}": v for k, v in _effective_settings(run).items()}
    view |= {f"data_files.{k}": v for k, v in (config.get("data_files") or {}).items()}
    view["generator"] = config.get("generator")
    view["prompt_version"] = config.get("prompt_version")
    view["collection.name"] = config.get("collection", {}).get("name")
    view["collection.points_count"] = config.get("collection", {}).get("points_count")
    if meta["mode"] == "full":
        view["judge_model"] = meta.get("judge_model")
        view["judge_prompt_version"] = meta.get("judge_prompt_version")
    return view


def diff_runs(base: dict, new: dict) -> Diff:
    diff = Diff()
    base_cases, new_cases = base["cases"], new["cases"]
    bm, nm = base["metadata"], new["metadata"]
    diff.added = sorted(set(new_cases) - set(base_cases))
    diff.filtered = bool(nm.get("tags") or nm.get("ids")) or nm.get("split") != bm.get("split")
    # A filtered run leaves most baseline cases out on purpose.
    diff.removed = [] if diff.filtered else sorted(set(base_cases) - set(new_cases))
    shared = [case_id for case_id in new_cases if case_id in base_cases]
    diff.shared = len(shared)
    for case_id in shared:
        worse, better = _retrieval_changes(base_cases[case_id], new_cases[case_id])
        full_worse, full_better = _full_changes(base_cases[case_id], new_cases[case_id])
        worse += full_worse
        better += full_better
        if worse:
            diff.regressions.append((case_id, "; ".join(worse)))
        elif better:
            diff.fixed.append((case_id, "; ".join(better)))
    diff.eval_hash_changed = bm.get("eval_sha256") != nm.get("eval_sha256")
    diff.mode_mismatch = bm.get("mode") != nm.get("mode")
    # Differences an experiment explains are keys either run overrode.
    explained = {f"settings.{k}" for k in (bm.get("overrides") or {})}
    explained |= {f"settings.{k}" for k in (nm.get("overrides") or {})}
    base_view, new_view = _config_view(base), _config_view(new)
    for key in sorted(set(base_view) | set(new_view)):
        if key in explained:
            continue
        if base_view.get(key) != new_view.get(key):
            diff.config_diffs.append((key, base_view.get(key), new_view.get(key)))
    return diff


def exit_code(diff: Diff | None) -> int:
    return 1 if diff and diff.regressions else 0


# ─────────────────────────────── rendering ───────────────────────────────

# (key, label, format, direction): +1 higher is better, -1 lower is better,
# 0 informational (no arrow).
RETRIEVAL_METRICS = [
    ("pass_rate", "pass rate", "pct", 1),
    ("mean_recall", "mean recall", "f3", 1),
    ("mean_first_hit_rank", "mean first-hit rank", "f2", -1),
    ("forbidden_hits", "forbidden-card hits", "int", -1),
    ("expected_missing", "expected cards missing", "int", -1),
    ("errors", "errors", "int", -1),
    ("mean_results", "mean results", "f1", 0),
    ("mean_context_chars", "mean context chars", "int", 0),
]
FULL_METRICS = [
    ("correct_rate", "judge correct", "pct", 1),
    ("partial_rate", "judge partial", "pct", 0),
    ("incorrect_rate", "judge incorrect", "pct", -1),
    ("verdict_accuracy", "verdict accuracy", "pct", 1),
    ("decline_accuracy", "decline accuracy", "pct", 1),
    ("invalid_citations", "invalid citations", "int", -1),
    ("judge_errors", "judge errors", "int", -1),
    ("answer_cache_hit_rate", "answer cache hits", "pct", 0),
    ("judge_cache_hit_rate", "judge cache hits", "pct", 0),
    ("mean_generate_ms", "mean generate ms", "int", 0),
]


def fmt(value, kind: str) -> str:
    if value is None:
        return "-"
    if kind == "pct":
        return f"{value * 100:.1f}%"
    if kind == "int":
        return f"{round(value)}"
    return f"{value:.{kind[1]}f}"


def arrow(old, new, direction: int, kind: str) -> str:
    if old is None or new is None or direction == 0:
        return ""
    if fmt(old, kind) == fmt(new, kind):
        return ""
    return "▲" if (new - old) * direction > 0 else "▼"


def metric_rows(mode: str) -> list[tuple]:
    return RETRIEVAL_METRICS + (FULL_METRICS if mode == "full" else [])


def header(run: dict) -> str:
    meta = run["metadata"]
    sha = meta["git_sha"] + ("-dirty" if meta["dirty"] else "")
    collection = meta["api_config"].get("collection", {}).get("name", "?")
    parts = [meta["mode"], f"{meta['split']} ({len(run['cases'])})", sha, collection]
    if meta.get("tags"):
        parts.append("tags=" + ",".join(meta["tags"]))
    if meta.get("ids"):
        parts.append("ids=" + ",".join(meta["ids"]))
    parts.append(f"exp={meta['experiment']}" if meta.get("experiment") else "no experiment")
    if meta["mode"] == "full":
        parts.append(f"judge={meta.get('judge_model')}")
    return "eval  " + " · ".join(parts)


def _pass_cell(stats: dict | None) -> str:
    if not stats or not stats["scored"]:
        return "-"
    return f"{stats['passed']}/{stats['scored']}"


def render(new: dict, base: dict | None = None, diff: Diff | None = None) -> str:
    mode = new["metadata"]["mode"]
    lines = [header(new)]
    if mode == "full" and new["metadata"].get("judge_model"):
        generator = new["metadata"]["api_config"].get("generator", "")
        judge = new["metadata"]["judge_model"]
        if judge in (generator, generator.removeprefix("ollama:")):
            lines.append(f"WARNING: the judge ({judge}) is the generator model; grades are biased.")

    now = new["aggregates"]
    before = None
    if base is not None:
        # Compare like with like: the baseline over this run's cases only.
        shared = {k: v for k, v in base["cases"].items() if k in new["cases"]}
        before = aggregate(shared, base["metadata"]["mode"])
    else:
        lines.append(f"no baseline for {mode}; skipping comparison")

    lines.append("")
    if before:
        lines.append(f"  {'':<24}{'baseline':>10}  {'this run':>10}")
    for key, label, kind, direction in metric_rows(mode):
        cur = now["overall"].get(key)
        if before:
            old = before["overall"].get(key)
            mark = arrow(old, cur, direction, kind)
            lines.append(f"  {label:<24}{fmt(old, kind):>10}  {fmt(cur, kind):>10}  {mark}")
        else:
            lines.append(f"  {label:<24}{fmt(cur, kind):>10}")

    lines.append("")
    lines.append("  pass by category")
    for category, stats in now["by_category"].items():
        cell = _pass_cell(stats)
        if before:
            old = _pass_cell(before["by_category"].get(category))
            cell = f"{old:>7} → {cell}"
        extra = ""
        if mode == "full":
            correct = stats.get("correct_rate")
            decline = stats.get("decline_accuracy")
            if correct is not None:
                extra = f"  judge correct {fmt(correct, 'pct')}"
            elif decline is not None:
                extra = f"  declined {fmt(decline, 'pct')}"
        lines.append(f"    {category:<22}{cell}{extra}")

    failing = [
        case_id
        for case_id, case in new["cases"].items()
        if case.get("error") or (case["retrieval"] or {}).get("pass") is False
    ]
    lines.append("")
    lines.append(f"  failing retrieval ({len(failing)}): {', '.join(failing) or '-'}")

    if diff is not None:
        lines.extend(_render_diff(diff))
    return "\n".join(lines)


def _render_diff(diff: Diff) -> list[str]:
    lines = [""]
    if diff.mode_mismatch:
        lines.append("WARNING: baseline and run are different modes")
    if diff.filtered:
        lines.append(f"(filtered run: compared on {diff.shared} shared cases)")
    width = max([len(i) for i, _ in diff.regressions + diff.fixed] + [10]) + 2
    lines.append(f"REGRESSIONS ({len(diff.regressions)})")
    lines.extend(f"  {case_id:<{width}}{reason}" for case_id, reason in diff.regressions)
    lines.append(f"FIXED ({len(diff.fixed)})")
    lines.extend(f"  {case_id:<{width}}{reason}" for case_id, reason in diff.fixed)
    if diff.added:
        lines.append(f"WARNING: cases not in baseline: {', '.join(diff.added)}")
    if diff.removed:
        lines.append(f"WARNING: baseline cases missing from this run: {', '.join(diff.removed)}")
    if diff.eval_hash_changed:
        lines.append("WARNING: eval.yaml has changed since the baseline")
    if diff.config_diffs:
        lines.append("WARNING: config differs from baseline (not explained by the experiment):")
        lines.extend(f"  {key}: {old!r} → {new!r}" for key, old, new in diff.config_diffs)
    return lines
