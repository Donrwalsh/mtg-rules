from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

CATEGORIES = ("keyword", "cr-only", "card-ruling", "interaction", "negative", "messy")
SPLITS = ("dev", "test")
SOURCE_KINDS = ("rules", "rulings", "cards")

_REQUIRED_KEYS = ("id", "question", "tags", "split")
_OPTIONAL_KEYS = (
    "required_sources",
    "expected_cards",
    "forbidden_cards",
    "gold_answer",
    "verdict",
    "should_decline",
)


class CaseFileError(Exception):
    def __init__(self, errors: list[str]):
        super().__init__("\n".join(errors))
        self.errors = errors


@dataclass(frozen=True)
class Case:
    id: str
    question: str
    tags: tuple[str, ...]
    split: str
    rules: tuple[str, ...] = ()
    rulings: tuple[str, ...] = ()
    cards: tuple[str, ...] = ()
    expected_cards: tuple[str, ...] = ()
    forbidden_cards: tuple[str, ...] = ()
    gold_answer: str | None = None
    verdict: str | None = None
    should_decline: bool = False
    raw: dict = field(default_factory=dict, compare=False, repr=False)

    @property
    def category(self) -> str:
        return self.tags[0]

    @property
    def requirements(self) -> list[tuple[str, str]]:
        """(kind, value) pairs, e.g. ("rules", "702.19b"), in file order."""
        return (
            [("rules", r) for r in self.rules]
            + [("rulings", r) for r in self.rulings]
            + [("cards", c) for c in self.cards]
        )

    @property
    def has_required(self) -> bool:
        return bool(self.requirements)


def load_raw(path: Path) -> list:
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return data if data is not None else []


def normalize_entry(raw):
    """PyYAML follows YAML 1.1, so an unquoted `verdict: yes` loads as True.
    Map it back so eval.yaml can keep the natural spelling."""
    if isinstance(raw, dict) and isinstance(raw.get("verdict"), bool):
        return {**raw, "verdict": "yes" if raw["verdict"] else "no"}
    return raw


def _is_str_list(value) -> bool:
    return isinstance(value, list) and all(isinstance(v, str) and v for v in value)


def _case_errors(index: int, raw) -> list[str]:
    if not isinstance(raw, dict):
        return [f"entry #{index + 1}: not a mapping"]
    label = f"{raw.get('id')!r}" if isinstance(raw.get("id"), str) else f"entry #{index + 1}"
    errors = []
    for key in _REQUIRED_KEYS:
        if key not in raw:
            errors.append(f"{label}: missing {key}")
    for key in sorted(set(raw) - set(_REQUIRED_KEYS) - set(_OPTIONAL_KEYS)):
        errors.append(f"{label}: unknown key {key!r}")
    if "id" in raw and not (isinstance(raw["id"], str) and raw["id"]):
        errors.append(f"{label}: id must be a non-empty string")
    if "question" in raw and not (isinstance(raw["question"], str) and raw["question"].strip()):
        errors.append(f"{label}: question must be a non-empty string")
    if "tags" in raw:
        if not _is_str_list(raw["tags"]) or not raw["tags"]:
            errors.append(f"{label}: tags must be a non-empty list of strings")
        elif raw["tags"][0] not in CATEGORIES:
            errors.append(
                f"{label}: first tag {raw['tags'][0]!r} is not a category ({', '.join(CATEGORIES)})"
            )
    if "split" in raw and raw["split"] not in SPLITS:
        errors.append(f"{label}: split must be one of {', '.join(SPLITS)}")
    sources = raw.get("required_sources")
    if sources is not None:
        if not isinstance(sources, dict):
            errors.append(f"{label}: required_sources must be a mapping")
        else:
            for kind in sorted(set(sources) - set(SOURCE_KINDS)):
                errors.append(f"{label}: unknown required_sources key {kind!r}")
            for kind in SOURCE_KINDS:
                if kind in sources and not _is_str_list(sources[kind]):
                    errors.append(f"{label}: required_sources.{kind} must be a list of strings")
    for key in ("expected_cards", "forbidden_cards"):
        if key in raw and not _is_str_list(raw[key]):
            errors.append(f"{label}: {key} must be a list of strings")
    if "gold_answer" in raw and not isinstance(raw["gold_answer"], str):
        errors.append(f"{label}: gold_answer must be a string")
    if "verdict" in raw and raw["verdict"] not in ("yes", "no"):
        errors.append(f"{label}: verdict must be yes or no")
    if "should_decline" in raw and not isinstance(raw["should_decline"], bool):
        errors.append(f"{label}: should_decline must be true or false")
    return errors


def schema_errors(raw: list) -> list[str]:
    if not isinstance(raw, list):
        return ["eval file must be a YAML list of cases"]
    errors = []
    seen: set[str] = set()
    for index, entry in enumerate(normalize_entry(e) for e in raw):
        errors.extend(_case_errors(index, entry))
        case_id = entry.get("id") if isinstance(entry, dict) else None
        if isinstance(case_id, str):
            if case_id in seen:
                errors.append(f"{case_id!r}: duplicate id")
            seen.add(case_id)
    return errors


def to_case(raw: dict) -> Case:
    sources = raw.get("required_sources") or {}
    return Case(
        id=raw["id"],
        question=raw["question"],
        tags=tuple(raw["tags"]),
        split=raw["split"],
        rules=tuple(sources.get("rules", ())),
        rulings=tuple(sources.get("rulings", ())),
        cards=tuple(sources.get("cards", ())),
        expected_cards=tuple(raw.get("expected_cards", ())),
        forbidden_cards=tuple(raw.get("forbidden_cards", ())),
        gold_answer=raw.get("gold_answer"),
        verdict=raw.get("verdict"),
        should_decline=raw.get("should_decline", False),
        raw=raw,
    )


def load_cases(path: Path) -> list[Case]:
    try:
        raw = load_raw(path)
    except (OSError, yaml.YAMLError) as exc:
        raise CaseFileError([f"cannot read {path}: {exc}"]) from exc
    errors = schema_errors(raw)
    if errors:
        raise CaseFileError(errors)
    return [to_case(normalize_entry(entry)) for entry in raw]


def select(
    cases: list[Case],
    split: str = "dev",
    tags: list[str] | None = None,
    ids: list[str] | None = None,
) -> list[Case]:
    """Filter by split ("all" for both), any-of tags and exact ids, ANDed."""
    selected = []
    for case in cases:
        if split != "all" and case.split != split:
            continue
        if tags and not set(tags) & set(case.tags):
            continue
        if ids and case.id not in ids:
            continue
        selected.append(case)
    return selected
