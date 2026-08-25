from __future__ import annotations

from pathlib import Path
from typing import Iterable, TypeVar

from pydantic import BaseModel

ModelT = TypeVar("ModelT", bound=BaseModel)


def write_jsonl(records: Iterable[ModelT], dest: Path) -> int:
    """Write one JSON object per line. Returns the number of records written.

    JSONL over a single JSON array so a future diff/embed stage can stream
    this file line by line instead of loading the whole thing into memory.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with dest.open("w", encoding="utf-8") as f:
        for record in records:
            f.write(record.model_dump_json())
            f.write("\n")
            count += 1
    return count


def read_jsonl(path: Path, model: type[ModelT]) -> list[ModelT]:
    with path.open("r", encoding="utf-8") as f:
        return [model.model_validate_json(line) for line in f if line.strip()]
