from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import typer

from mtg_ingestion.config import settings
from mtg_ingestion.fetch.rules import fetch_rules_text
from mtg_ingestion.fetch.scryfall import fetch_oracle_cards, fetch_rulings
from mtg_ingestion.parse.cards import parse_cards_file
from mtg_ingestion.parse.rules import parse_rules_file
from mtg_ingestion.parse.rulings import parse_rulings_file
from mtg_ingestion.storage import write_jsonl

app = typer.Typer(help="Fetch and parse the MTG Comprehensive Rules, oracle card text, and rulings.")


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _latest(directory: Path, pattern: str) -> Path:
    matches = sorted(directory.glob(pattern))
    if not matches:
        raise FileNotFoundError(f"No files matching {pattern!r} in {directory}")
    return matches[-1]


def _find_scryfall_raw(data_type: str) -> Path:
    """Locate a fetched Scryfall bulk file regardless of which wire format
    it was saved in (current .jsonl.gz vs legacy .json)."""
    for candidate in (settings.raw_dir / f"{data_type}.jsonl.gz", settings.raw_dir / f"{data_type}.json"):
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        f"No {data_type}.jsonl.gz or {data_type}.json found in {settings.raw_dir} -- run the matching "
        "fetch command first."
    )


@app.command("fetch-rules")
def fetch_rules_cmd() -> None:
    """Download the current Comprehensive Rules text."""
    dest = fetch_rules_text()
    typer.echo(f"Saved rules text -> {dest}")


@app.command("fetch-cards")
def fetch_cards_cmd() -> None:
    """Download Scryfall's oracle_cards bulk data."""
    dest = fetch_oracle_cards()
    typer.echo(f"Saved oracle cards -> {dest}")


@app.command("fetch-rulings")
def fetch_rulings_cmd() -> None:
    """Download Scryfall's rulings bulk data."""
    dest = fetch_rulings()
    typer.echo(f"Saved rulings -> {dest}")


@app.command("parse-rules")
def parse_rules_cmd(
    raw_path: Path | None = typer.Option(
        None, help="Rules .txt to parse; defaults to the newest file in data/raw."
    ),
) -> None:
    """Parse a downloaded Comprehensive Rules file into JSONL."""
    resolved = raw_path or _latest(settings.raw_dir, "rules_*.txt")
    chunks = parse_rules_file(resolved)
    dest = settings.parsed_dir / f"rules_{_today()}.jsonl"
    count = write_jsonl(chunks, dest)
    typer.echo(f"Parsed {count} rule chunks -> {dest}")


@app.command("parse-cards")
def parse_cards_cmd(
    raw_path: Path | None = typer.Option(
        None, help="oracle_cards file to parse; defaults to whatever's newest in data/raw."
    ),
) -> None:
    """Parse downloaded Scryfall oracle card data into JSONL."""
    resolved = raw_path or _find_scryfall_raw("oracle_cards")
    cards = parse_cards_file(resolved)
    dest = settings.parsed_dir / f"cards_{_today()}.jsonl"
    count = write_jsonl(cards, dest)
    typer.echo(f"Parsed {count} cards -> {dest}")


@app.command("parse-rulings")
def parse_rulings_cmd(
    raw_path: Path | None = typer.Option(
        None, help="rulings file to parse; defaults to whatever's newest in data/raw."
    ),
) -> None:
    """Parse downloaded Scryfall rulings data into JSONL."""
    resolved = raw_path or _find_scryfall_raw("rulings")
    rulings = parse_rulings_file(resolved)
    dest = settings.parsed_dir / f"rulings_{_today()}.jsonl"
    count = write_jsonl(rulings, dest)
    typer.echo(f"Parsed {count} rulings -> {dest}")


@app.command("run-all")
def run_all() -> None:
    """Fetch and parse all three sources end to end -- today's MVP entrypoint.

    This is also the exact command a scheduler calls later for automated
    syncs; nothing here changes when ingestion stops being "one-time."
    """
    typer.echo("Fetching Comprehensive Rules...")
    rules_raw = fetch_rules_text()
    typer.echo("Fetching Scryfall oracle cards...")
    cards_raw = fetch_oracle_cards()
    typer.echo("Fetching Scryfall rulings...")
    rulings_raw = fetch_rulings()

    typer.echo("Parsing rules...")
    rule_chunks = parse_rules_file(rules_raw)
    write_jsonl(rule_chunks, settings.parsed_dir / f"rules_{_today()}.jsonl")

    typer.echo("Parsing cards...")
    cards = parse_cards_file(cards_raw)
    write_jsonl(cards, settings.parsed_dir / f"cards_{_today()}.jsonl")

    typer.echo("Parsing rulings...")
    rulings = parse_rulings_file(rulings_raw)
    write_jsonl(rulings, settings.parsed_dir / f"rulings_{_today()}.jsonl")

    typer.echo(f"Done: {len(rule_chunks)} rule chunks, {len(cards)} cards, {len(rulings)} rulings.")


if __name__ == "__main__":
    app()
