from __future__ import annotations

import json
import shutil
import sys
from enum import Enum
from pathlib import Path
from typing import Annotated

import typer

from mtg_evals.cases import load_cases
from mtg_evals.client import PreflightError
from mtg_evals.experiments import ExperimentError, load_experiment
from mtg_evals.metrics import aggregate
from mtg_evals.paths import (
    EVALS_DIR,
    RUNS_DIR,
    baseline_path,
    default_api_url,
    default_eval_file,
    default_parsed_dir,
)
from mtg_evals.report import diff_runs, exit_code, fmt, header, metric_rows, render
from mtg_evals.runner import RunOptions, execute
from mtg_evals.validate import validate as validate_eval

app = typer.Typer(help="Run eval.yaml against the running mtg-api and diff against a baseline.")

EXIT_REGRESSION = 1
EXIT_USAGE = 2


class Mode(str, Enum):
    retrieval = "retrieval"
    full = "full"


class Split(str, Enum):
    dev = "dev"
    test = "test"
    all = "all"


@app.callback()
def _setup() -> None:
    # ▲/▼/→ in the report must not crash a cp1252 Windows console.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def _fail(message: str) -> typer.Exit:
    typer.echo(f"error: {message}", err=True)
    return typer.Exit(EXIT_USAGE)


def _load(path: Path) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _resolve_run(name: str) -> Path:
    """A path, a run filename in evals/runs/ (with or without .json), or a
    baseline name like "baseline-retrieval"."""
    for candidate in (
        Path(name),
        RUNS_DIR / name,
        RUNS_DIR / f"{name}.json",
        EVALS_DIR / name,
        EVALS_DIR / f"{name}.json",
    ):
        if candidate.is_file():
            return candidate
    raise _fail(f"no run file {name!r} (looked in {RUNS_DIR} and {EVALS_DIR})")


def _latest_run(mode: str) -> Path:
    for path in sorted(RUNS_DIR.glob("*.json"), reverse=True):
        try:
            if _load(path)["metadata"]["mode"] == mode:
                return path
        except (json.JSONDecodeError, KeyError, OSError):
            continue
    raise _fail(f"no {mode} runs in {RUNS_DIR}; run `mtg-evals run --mode {mode}` first")


def _baseline(mode: str) -> dict | None:
    path = baseline_path(mode)
    return _load(path) if path.exists() else None


def _run_one(
    mode: str,
    split: str,
    tags: list[str],
    ids: list[str],
    exp: str | None,
    api_url: str,
    concurrency: int | None,
    eval_file: Path,
) -> tuple[dict, Path]:
    overrides = {}
    if exp:
        try:
            experiment = load_experiment(exp)
        except ExperimentError as exc:
            raise _fail(str(exc)) from exc
        overrides = experiment["overrides"]
    options = RunOptions(
        mode=mode,
        eval_file=eval_file,
        api_url=api_url,
        split=split,
        tags=tags,
        ids=ids,
        experiment=exp,
        overrides=overrides,
        concurrency=concurrency,
    )
    typer.echo(f"running {mode} · {split} · {api_url}" + (f" · exp={exp}" if exp else ""), err=True)
    try:
        return execute(options, progress=lambda line: typer.echo(line, err=True))
    except PreflightError as exc:
        raise _fail(f"preflight failed: {exc}") from exc


TagOpt = Annotated[list[str] | None, typer.Option("--tag", help="Only cases with this tag.")]
IdOpt = Annotated[list[str] | None, typer.Option("--id", help="Only this case id.")]
ApiOpt = Annotated[str, typer.Option("--api-url", help="mtg-api base URL (EVAL_API_URL).")]
EvalOpt = Annotated[Path | None, typer.Option("--eval-file", help="Question set (EVAL_FILE).")]
ConcOpt = Annotated[
    int | None, typer.Option("--concurrency", help="Parallel cases (default 4 retrieval, 1 full).")
]


@app.command()
def run(
    mode: Annotated[Mode, typer.Option("--mode")] = Mode.retrieval,
    split: Annotated[Split, typer.Option("--split")] = Split.dev,
    tag: TagOpt = None,
    id: IdOpt = None,  # mirrors the --id flag
    exp: Annotated[str | None, typer.Option("--exp", help="Experiment name.")] = None,
    api_url: ApiOpt = "",
    concurrency: ConcOpt = None,
    no_compare: Annotated[bool, typer.Option("--no-compare")] = False,
    eval_file: EvalOpt = None,
) -> None:
    """Run the eval set, write a run file and compare it to the baseline."""
    new, path = _run_one(
        mode.value,
        split.value,
        tag or [],
        id or [],
        exp,
        api_url or default_api_url(),
        concurrency,
        eval_file or default_eval_file(),
    )
    base = None if no_compare else _baseline(mode.value)
    diff = diff_runs(base, new) if base else None
    typer.echo("")
    skipped = "comparison disabled (--no-compare)" if no_compare else None
    typer.echo(render(new, base, diff, skipped=skipped))
    typer.echo(f"\nrun written to {path}")
    raise typer.Exit(exit_code(diff))


@app.command()
def baseline(
    run_file: Annotated[str | None, typer.Argument(help="Run to promote (default: latest).")] = None,
    mode: Annotated[Mode, typer.Option("--mode")] = Mode.retrieval,
) -> None:
    """Promote a run to evals/baseline-<mode>.json."""
    source = _resolve_run(run_file) if run_file else _latest_run(mode.value)
    meta = _load(source)["metadata"]
    if meta["mode"] != mode.value:
        raise _fail(f"{source.name} is a {meta['mode']} run, not {mode.value}")
    target = baseline_path(mode.value)
    shutil.copyfile(source, target)
    typer.echo(f"baseline-{mode.value} ← {source.name}")
    notes = []
    if meta.get("experiment"):
        notes.append(f"experiment {meta['experiment']}")
    if meta.get("tags") or meta.get("ids"):
        notes.append("a tag/id filter")
    if meta.get("dirty"):
        notes.append("a dirty working tree")
    if notes:
        typer.echo(f"note: this run used {', '.join(notes)}")


@app.command()
def compare(
    run_a: Annotated[str, typer.Argument(help="Reference run (e.g. a baseline).")],
    run_b: Annotated[str, typer.Argument(help="Run to judge against it.")],
) -> None:
    """Diff two run files; exits 1 if B regressed any case relative to A."""
    base, new = _load(_resolve_run(run_a)), _load(_resolve_run(run_b))
    diff = diff_runs(base, new)
    typer.echo(render(new, base, diff))
    raise typer.Exit(exit_code(diff))


@app.command()
def sweep(
    exps: Annotated[list[str], typer.Argument(help="Experiment names, run in order.")],
    mode: Annotated[Mode, typer.Option("--mode")] = Mode.retrieval,
    split: Annotated[Split, typer.Option("--split")] = Split.dev,
    tag: TagOpt = None,
    id: IdOpt = None,
    api_url: ApiOpt = "",
    concurrency: ConcOpt = None,
    eval_file: EvalOpt = None,
) -> None:
    """Run several experiments back to back and print them side by side."""
    runs = []
    for exp in exps:
        new, path = _run_one(
            mode.value,
            split.value,
            tag or [],
            id or [],
            exp,
            api_url or default_api_url(),
            concurrency,
            eval_file or default_eval_file(),
        )
        typer.echo(f"  → {path.name}", err=True)
        runs.append((exp, new))

    base = _baseline(mode.value)
    columns = ([("baseline", base)] if base else []) + runs
    diffs = {exp: diff_runs(base, new) for exp, new in runs} if base else {}
    width = 12
    typer.echo("")
    typer.echo(header(runs[0][1]).split(" · exp=")[0] + " · sweep")
    typer.echo(f"  {'':<24}" + "".join(f"{name:>{width}}" for name, _ in columns))
    for key, label, kind, _direction in metric_rows(mode.value):
        cells = []
        for name, run in columns:
            if name == "baseline":
                shared = {k: v for k, v in run["cases"].items() if k in runs[0][1]["cases"]}
                value = aggregate(shared, run["metadata"]["mode"])["overall"].get(key)
            else:
                value = run["aggregates"]["overall"].get(key)
            cells.append(fmt(value, kind))
        typer.echo(f"  {label:<24}" + "".join(f"{c:>{width}}" for c in cells))
    if diffs:
        typer.echo(
            f"  {'regressions / fixed':<24}"
            + f"{'':>{width}}"
            + "".join(
                f"{f'{len(d.regressions)} / {len(d.fixed)}':>{width}}" for d in diffs.values()
            )
        )
        for exp, d in diffs.items():
            for case_id, reason in d.regressions:
                typer.echo(f"  [{exp}] REGRESSION {case_id}: {reason}")
    else:
        typer.echo(f"no baseline for {mode.value}; showing experiments only")
    raise typer.Exit(max((exit_code(d) for d in diffs.values()), default=0))


@app.command()
def validate(
    eval_file: EvalOpt = None,
    parsed_dir: Annotated[Path | None, typer.Option("--parsed-dir")] = None,
) -> None:
    """Check eval.yaml's schema and every rule ID and card name against the parsed data."""
    eval_file = eval_file or default_eval_file()
    parsed_dir = parsed_dir or default_parsed_dir()
    try:
        problems = validate_eval(eval_file, parsed_dir)
    except FileNotFoundError as exc:
        raise _fail(str(exc)) from exc
    if problems:
        typer.echo(f"{eval_file}: {len(problems)} problem(s)")
        for problem in problems:
            typer.echo(f"  {problem}")
        raise typer.Exit(EXIT_REGRESSION)
    typer.echo(f"{eval_file}: clean ({len(load_cases(eval_file))} cases, data in {parsed_dir})")

