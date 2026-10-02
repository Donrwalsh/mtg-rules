from __future__ import annotations

import os
import re
from pathlib import Path

# src/mtg_evals/paths.py -> evals/ -> repo root. Resolved from this file, not
# the cwd, so every command works from anywhere (including `make` at the root).
EVALS_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = EVALS_DIR.parent

RUNS_DIR = EVALS_DIR / "runs"
CACHE_DIR = EVALS_DIR / ".cache"
EXPERIMENTS_DIR = EVALS_DIR / "experiments"

MODES = ("retrieval", "full")


def _baseline_slug(generator: str | None) -> str | None:
    """None for Gemini, which keeps the original baseline files. Otherwise
    the model's name: "ollama:phi4:latest" -> "phi4"."""
    if not generator or generator.startswith("gemini:"):
        return None
    model = generator.partition(":")[2]
    name, _, tag = model.partition(":")
    slug = name if tag in ("", "latest") else f"{name}-{tag}"
    return re.sub(r"[^a-z0-9.]+", "-", slug.lower()).strip("-")


def baseline_path(mode: str, generator: str | None = None) -> Path:
    """Full-mode baselines are per generator, so a run is never compared
    against answers another model wrote. Retrieval doesn't depend on one."""
    slug = _baseline_slug(generator) if mode == "full" else None
    return EVALS_DIR / (f"baseline-{mode}-{slug}.json" if slug else f"baseline-{mode}.json")


def default_eval_file() -> Path:
    return Path(os.environ.get("EVAL_FILE") or REPO_ROOT / "eval.yaml")


def default_api_url() -> str:
    return os.environ.get("EVAL_API_URL") or "http://localhost:8000"


def default_parsed_dir() -> Path:
    return Path(
        os.environ.get("EVAL_PARSED_DIR")
        or REPO_ROOT / "mtg-worker" / "mtg-ingestion" / "data" / "parsed"
    )
