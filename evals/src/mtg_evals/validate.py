from __future__ import annotations

import json
from pathlib import Path

import yaml

from mtg_evals.cases import load_raw, normalize_entry, schema_errors, to_case


def latest(directory: Path, pattern: str) -> Path:
    # Same rule the API uses: dated filenames sort chronologically.
    matches = sorted(directory.glob(pattern))
    if not matches:
        raise FileNotFoundError(f"No files matching {pattern!r} in {directory}")
    return matches[-1]


def _read_jsonl(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def validate(eval_path: Path, parsed_dir: Path) -> list[str]:
    """Every problem with eval.yaml, checked against the latest parsed data.
    An empty list means the file is clean."""
    try:
        raw = load_raw(eval_path)
    except (OSError, yaml.YAMLError) as exc:
        return [f"cannot read {eval_path}: {exc}"]
    problems = schema_errors(raw)

    rule_ids = [r["rule_id"] for r in _read_jsonl(latest(parsed_dir, "rules_*.jsonl"))]
    cards = _read_jsonl(latest(parsed_dir, "cards_*.jsonl"))
    oracle_ids_by_name: dict[str, set[str]] = {}
    for card in cards:
        oracle_ids_by_name.setdefault(card["name"], set()).add(card["oracle_id"])
    ruled_oracle_ids = {r["oracle_id"] for r in _read_jsonl(latest(parsed_dir, "rulings_*.jsonl"))}

    for entry in raw if isinstance(raw, list) else []:
        # Data checks only make sense on entries that are structurally sound.
        if not isinstance(entry, dict) or schema_errors([entry]):
            continue
        case = to_case(normalize_entry(entry))
        for rule in case.rules:
            if not any(rule_id.startswith(rule) for rule_id in rule_ids):
                problems.append(f"{case.id!r}: rule {rule!r} matches no rule ID")
        named = [
            ("required_sources.cards", case.cards),
            ("required_sources.rulings", case.rulings),
            ("expected_cards", case.expected_cards),
            ("forbidden_cards", case.forbidden_cards),
        ]
        for field, names in named:
            for name in names:
                if name not in oracle_ids_by_name:
                    problems.append(f"{case.id!r}: {field} card {name!r} does not exist")
        for name in case.rulings:
            oracle_ids = oracle_ids_by_name.get(name)
            if oracle_ids and not oracle_ids & ruled_oracle_ids:
                problems.append(f"{case.id!r}: {name!r} has no rulings")
    return problems
