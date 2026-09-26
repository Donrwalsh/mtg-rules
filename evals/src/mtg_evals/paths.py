from __future__ import annotations

import os
from pathlib import Path

# src/mtg_evals/paths.py -> evals/ -> repo root. Resolved from this file, not
# the cwd, so every command works from anywhere (including `make` at the root).
EVALS_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = EVALS_DIR.parent

RUNS_DIR = EVALS_DIR / "runs"
CACHE_DIR = EVALS_DIR / ".cache"
EXPERIMENTS_DIR = EVALS_DIR / "experiments"

MODES = ("retrieval", "full")


def baseline_path(mode: str) -> Path:
    return EVALS_DIR / f"baseline-{mode}.json"


def default_eval_file() -> Path:
    return Path(os.environ.get("EVAL_FILE") or REPO_ROOT / "eval.yaml")


def default_api_url() -> str:
    return os.environ.get("EVAL_API_URL") or "http://localhost:8000"


def default_parsed_dir() -> Path:
    return Path(
        os.environ.get("EVAL_PARSED_DIR")
        or REPO_ROOT / "mtg-worker" / "mtg-ingestion" / "data" / "parsed"
    )
