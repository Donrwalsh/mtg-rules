from pathlib import Path

import pytest
from typer.testing import CliRunner

from mtg_embed.cli import _latest, app

runner = CliRunner()


def test_latest_picks_the_lexicographically_last_match(tmp_path: Path):
    (tmp_path / "rules_2026-01-01.jsonl").write_text("{}\n")
    (tmp_path / "rules_2026-08-25.jsonl").write_text("{}\n")

    result = _latest(tmp_path, "rules_*.jsonl")

    assert result.name == "rules_2026-08-25.jsonl"


def test_latest_raises_when_no_files_match(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        _latest(tmp_path, "rules_*.jsonl")


def test_run_rejects_unknown_source_before_touching_network():
    result = runner.invoke(app, ["run", "--source", "bogus"])
    assert result.exit_code != 0
