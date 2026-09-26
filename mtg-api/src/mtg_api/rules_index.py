from __future__ import annotations

import json
import re
from pathlib import Path

_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


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


def load_rules_index(rules_path: Path) -> RulesIndex:
    with rules_path.open("r", encoding="utf-8") as f:
        rules = [json.loads(line) for line in f if line.strip()]
    # Parsed files are named rules_<ingest date>.jsonl.
    match = _DATE_RE.search(rules_path.name)
    return RulesIndex(rules, match.group(0) if match else None)
