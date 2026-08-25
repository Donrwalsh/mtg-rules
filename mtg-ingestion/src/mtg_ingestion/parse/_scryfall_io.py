from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any, Iterator


def iter_scryfall_records(raw_path: Path) -> Iterator[dict[str, Any]]:
    """Yield raw record dicts from a downloaded Scryfall bulk-data file.

    Handles both wire formats Scryfall has served:
      - *.jsonl.gz -- gzip-compressed newline-delimited JSON (current
        format as of July 20, 2026)
      - *.json     -- a single JSON array (legacy format; kept here only
        so an older raw file can still be reprocessed without re-fetching)
    """
    if raw_path.name.endswith(".jsonl.gz"):
        with gzip.open(raw_path, mode="rt", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    yield json.loads(line)
    elif raw_path.suffix == ".jsonl":
        with raw_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    yield json.loads(line)
    else:
        with raw_path.open("r", encoding="utf-8") as f:
            yield from json.load(f)
