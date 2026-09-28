from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path


def _key(parts: list) -> str:
    # sort_keys makes dict components order-independent.
    encoded = json.dumps(parts, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def answer_key(
    question: str,
    context_hash: str,
    generator: str,
    prompt_version: int | None,
    generation_overrides: dict,
) -> str:
    """Everything that determines the generated answer. Retrieval overrides
    are covered by context_hash; only generation overrides are listed."""
    return _key(["answer", question, context_hash, generator, prompt_version, generation_overrides])


def judge_key(
    question: str,
    gold_answer: str | None,
    answer: str,
    judge_model: str,
    judge_prompt_version: int,
    should_decline: bool = False,
) -> str:
    return _key(
        ["judge", question, gold_answer, answer, judge_model, judge_prompt_version, should_decline]
    )


class JsonCache:
    """One JSON file per entry: safe under concurrent writers, trivial to
    inspect, and `rm -r evals/.cache` resets everything."""

    def __init__(self, root: Path, kind: str):
        self._dir = root / kind

    def get(self, key: str) -> dict | None:
        path = self._dir / f"{key}.json"
        try:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        except (FileNotFoundError, json.JSONDecodeError):
            return None

    def put(self, key: str, value: dict) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._dir / f"{key}.json"
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(value, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
