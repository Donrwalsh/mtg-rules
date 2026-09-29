from __future__ import annotations

import json
import re
from pathlib import Path

_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")

# The Comprehensive Rules' nine sections. Only the raw text names them, and
# they haven't changed in decades, so they live here rather than in ingestion.
CR_SECTIONS: dict[int, str] = {
    1: "Game Concepts",
    2: "Parts of a Card",
    3: "Card Types",
    4: "Zones",
    5: "Turn Structure",
    6: "Spells, Abilities, and Effects",
    7: "Additional Rules",
    8: "Multiplayer Rules",
    9: "Casual Variants",
}
_TOP_LEVEL_RE = re.compile(r"\d{3}")
_HEADING_MAX_CHARS = 60


def _is_title(text: str) -> bool:
    # "Deathtouch", "Combat Damage Step" -- not rule prose.
    return len(text) <= _HEADING_MAX_CHARS and not text.rstrip().endswith((".", ":", ")"))


class RulesIndex:
    """The Comprehensive Rules in memory, keyed by rule_id. Shared by the
    keyword matcher, answer rule-reference validation, and the rules
    endpoint, so the rules file is read once."""

    def __init__(self, rules: list[dict], ingested_at: str | None = None):
        self.rules = rules
        self.ingested_at = ingested_at
        self._by_id = {r["rule_id"]: r for r in rules}
        self._children: dict[str, list[dict]] = {}
        for rule in rules:
            if rule.get("parent_id"):
                self._children.setdefault(rule["parent_id"], []).append(rule)

    def get(self, rule_id: str) -> dict | None:
        return self._by_id.get(rule_id)

    def __contains__(self, rule_id: object) -> bool:
        return rule_id in self._by_id

    def children(self, rule_id: str) -> list[dict]:
        return list(self._children.get(rule_id, []))

    def ancestors(self, rule_id: str) -> list[dict]:
        """Top-level ancestor first; excludes the rule itself. Stops at the
        first parent_id that isn't in the index."""
        chain: list[dict] = []
        rule = self._by_id.get(rule_id)
        parent_id = rule.get("parent_id") if rule else None
        while parent_id in self._by_id:
            parent = self._by_id[parent_id]
            chain.append(parent)
            parent_id = parent.get("parent_id")
        chain.reverse()
        return chain

    def heading(self, rule_id: str) -> str | None:
        """The nearest title-like text at or above this rule: 702.2c ->
        "Deathtouch", 510.1c -> "Combat Damage Step"."""
        rule = self._by_id.get(rule_id)
        if rule is None:
            return None
        for candidate in [rule, *reversed(self.ancestors(rule_id))]:
            if _is_title(candidate["text"]):
                return candidate["text"]
        return None

    def table_of_contents(self) -> list[dict]:
        sections: dict[int, list[dict]] = {}
        for rule in self.rules:
            if _TOP_LEVEL_RE.fullmatch(rule["rule_id"]):
                sections.setdefault(int(rule["rule_id"][0]), []).append(
                    {"rule_id": rule["rule_id"], "text": rule["text"]}
                )
        return [
            {"number": n, "title": CR_SECTIONS.get(n, f"Section {n}"), "rules": sections[n]}
            for n in sorted(sections)
        ]


def load_rules_index(rules_path: Path) -> RulesIndex:
    with rules_path.open("r", encoding="utf-8") as f:
        rules = [json.loads(line) for line in f if line.strip()]
    # Parsed files are named rules_<ingest date>.jsonl.
    match = _DATE_RE.search(rules_path.name)
    return RulesIndex(rules, match.group(0) if match else None)
