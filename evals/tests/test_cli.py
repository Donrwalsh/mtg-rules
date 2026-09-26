import json

import pytest
import yaml
from test_report import BASE, _case, _run
from typer.testing import CliRunner

from mtg_evals import cli

runner = CliRunner()


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    runs = tmp_path / "runs"
    runs.mkdir()
    monkeypatch.setattr(cli, "RUNS_DIR", runs)
    monkeypatch.setattr(cli, "EVALS_DIR", tmp_path)
    monkeypatch.setattr(cli, "baseline_path", lambda mode: tmp_path / f"baseline-{mode}.json")
    return tmp_path


def _write(path, run):
    path.write_text(json.dumps(run), encoding="utf-8")
    return path


def test_compare_exits_1_on_regression(dirs):
    new = dict(BASE, trample=_case({"rules:702.19b": None}))
    _write(dirs / "runs" / "a.json", _run(BASE))
    _write(dirs / "runs" / "b.json", _run(new))

    result = runner.invoke(cli.app, ["compare", "a", "b.json"])

    assert result.exit_code == 1
    assert "REGRESSIONS (1)" in result.output
    assert "702.19b was rank 3, now missing" in result.output


def test_compare_exits_0_without_regressions(dirs):
    _write(dirs / "runs" / "a.json", _run(BASE))
    result = runner.invoke(cli.app, ["compare", "a", "a"])
    assert result.exit_code == 0
    assert "REGRESSIONS (0)" in result.output


def test_compare_unknown_run_is_a_usage_error(dirs):
    result = runner.invoke(cli.app, ["compare", "nope", "nada"])
    assert result.exit_code == 2


def test_baseline_promotes_the_latest_run_of_the_mode(dirs):
    _write(dirs / "runs" / "20260101T000000Z-abc-retrieval.json", _run(BASE))
    _write(dirs / "runs" / "20260102T000000Z-abc-retrieval.json", _run(BASE, experiment="x"))
    _write(dirs / "runs" / "20260103T000000Z-abc-full.json", _run(BASE, mode="full"))

    result = runner.invoke(cli.app, ["baseline", "--mode", "retrieval"])

    assert result.exit_code == 0, result.output
    assert "20260102T000000Z-abc-retrieval.json" in result.output
    assert "note: this run used experiment x" in result.output
    promoted = json.loads((dirs / "baseline-retrieval.json").read_text(encoding="utf-8"))
    assert promoted["metadata"]["experiment"] == "x"


def test_baseline_refuses_a_run_of_the_other_mode(dirs):
    _write(dirs / "runs" / "f.json", _run(BASE, mode="full"))
    result = runner.invoke(cli.app, ["baseline", "f", "--mode", "retrieval"])
    assert result.exit_code == 2


def test_baseline_without_runs_is_a_usage_error(dirs):
    assert runner.invoke(cli.app, ["baseline", "--mode", "full"]).exit_code == 2


def test_validate_clean_and_dirty(tmp_path):
    parsed = tmp_path / "parsed"
    parsed.mkdir()
    (parsed / "rules_2026-08-25.jsonl").write_text('{"rule_id": "702.11b"}\n', encoding="utf-8")
    (parsed / "cards_2026-09-26.jsonl").write_text(
        '{"oracle_id": "o1", "name": "Lightning Bolt"}\n', encoding="utf-8"
    )
    (parsed / "rulings_2026-08-25.jsonl").write_text('{"oracle_id": "o1"}\n', encoding="utf-8")
    case = {"id": "a", "question": "q?", "tags": ["keyword"], "split": "dev"}
    good = tmp_path / "good.yaml"
    good.write_text(yaml.safe_dump([{**case, "required_sources": {"rules": ["702.11"]}}]))
    bad = tmp_path / "bad.yaml"
    bad.write_text(yaml.safe_dump([{**case, "expected_cards": ["Lightning Blot"]}]))

    ok = runner.invoke(cli.app, ["validate", "--eval-file", str(good), "--parsed-dir", str(parsed)])
    assert ok.exit_code == 0
    assert "clean (1 cases" in ok.output

    problems = runner.invoke(
        cli.app, ["validate", "--eval-file", str(bad), "--parsed-dir", str(parsed)]
    )
    assert problems.exit_code == 1
    assert "'Lightning Blot' does not exist" in problems.output


def test_run_with_unknown_experiment_is_a_usage_error():
    result = runner.invoke(cli.app, ["run", "--exp", "no-such-experiment"])
    assert result.exit_code == 2
    assert "no experiment 'no-such-experiment'" in result.output


def test_example_experiments_load():
    from mtg_evals.experiments import load_experiment

    assert load_experiment("dense70")["overrides"] == {
        "hybrid_dense_weight": 0.7,
        "hybrid_sparse_weight": 0.3,
    }
    assert load_experiment("topk15")["overrides"] == {"hybrid_top_k": 15}
    assert load_experiment("temp0")["overrides"] == {"generation_temperature": 0}
