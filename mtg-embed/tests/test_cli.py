from pathlib import Path

import pytest
from typer.testing import CliRunner

from mtg_embed.cli import _format_summary_line, _latest, app
from mtg_embed.pipeline import RunSummary

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


def test_format_summary_line_preserves_source_name_on_zero_chunks():
    """Test that zero-chunk sources (source_type='') still display their source name."""
    # Simulate a zero-chunk summary (what embed_and_store returns when chunks is empty)
    summary = RunSummary(source_type="", total_seen=0, embedded=0, skipped_unchanged=0)

    line = _format_summary_line("rules", summary)

    # The line should contain "rules:" even though summary.source_type is empty
    assert line.startswith("  rules:")
    assert "embedded=0" in line
    assert "skipped_unchanged=0" in line
    assert "total_seen=0" in line
