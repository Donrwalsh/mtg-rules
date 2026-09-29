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
    assert "payload_updated=0" in line
    assert "total_seen=0" in line


def test_format_summary_line_reports_pruned_points():
    summary = RunSummary(
        source_type="oracle", total_seen=10, embedded=1, skipped_unchanged=9, pruned=216
    )
    assert "pruned=216" in _format_summary_line("cards", summary)


def test_format_summary_line_says_when_prune_was_skipped():
    # --limit runs see only part of each source, so they must not prune.
    summary = RunSummary(source_type="rule", total_seen=5, embedded=5, skipped_unchanged=0)
    assert "pruned=skipped" in _format_summary_line("rules", summary, pruned_skipped=True)
