from __future__ import annotations

from pathlib import Path

import yaml

from mtg_evals.paths import EXPERIMENTS_DIR


class ExperimentError(Exception):
    pass


def available(directory: Path = EXPERIMENTS_DIR) -> list[str]:
    return sorted(p.stem for p in directory.glob("*.yaml"))


def load_experiment(name: str, directory: Path = EXPERIMENTS_DIR) -> dict:
    path = directory / f"{name}.yaml"
    if not path.exists():
        known = ", ".join(available(directory)) or "none"
        raise ExperimentError(f"no experiment {name!r} in {directory} (available: {known})")
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    overrides = data.get("overrides") or {}
    if not isinstance(overrides, dict):
        raise ExperimentError(f"{path}: overrides must be a mapping")
    return {"name": name, "description": data.get("description", ""), "overrides": overrides}
