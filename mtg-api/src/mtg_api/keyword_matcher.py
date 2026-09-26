from __future__ import annotations

import re

import ahocorasick

# 702.N headings name one keyword ability each; 702.1 is the section's
# introductory prose, not a keyword.
_HEADING_RE = re.compile(r"702\.\d+")
_SECTION_INTRO_ID = "702.1"
_PARENTHETICAL_RE = re.compile(r"^(.*?)\s*\((.+)\)$")


def _base_forms(name: str) -> list[str]:
    name = name.lower().rstrip("!").strip()
    forms = [name]
    paren = _PARENTHETICAL_RE.match(name)
    if paren:
        forms = [paren.group(1), paren.group(2)]
    if " and " in name:
        forms += name.split(" and ")
    return [f.strip() for f in forms if f.strip()]


def _inflections(form: str) -> list[str]:
    # Cheap, regular-only inflections of the last word: "trample" ->
    # "tramples"/"trampled"/"trampling". No consonant doubling or irregulars.
    if not form[-1].isalpha():
        return []
    variants = [form + "s", form + "es", form + "ed", form + "ing"]
    if form.endswith("e"):
        variants += [form + "d", form[:-1] + "ing"]
    return variants


class KeywordMatcher:
    def __init__(self, rules: list[dict]):
        headings = [
            r
            for r in rules
            if _HEADING_RE.fullmatch(r["rule_id"]) and r["rule_id"] != _SECTION_INTRO_ID
        ]
        heading_ids = {h["rule_id"] for h in headings}
        subrules: dict[str, list[dict]] = {rule_id: [] for rule_id in heading_ids}
        for rule in rules:
            if rule.get("parent_id") in heading_ids:
                subrules[rule["parent_id"]].append(rule)

        self._keywords: dict[str, dict] = {
            h["rule_id"]: {
                "keyword": h["text"],
                "rule_id": h["rule_id"],
                "rules": [h] + subrules[h["rule_id"]],
            }
            for h in headings
        }

        # Base forms are registered before any inflection so that an
        # inflected form never shadows another keyword's actual name.
        heading_by_form: dict[str, str] = {}
        base_forms = [(h["rule_id"], f) for h in headings for f in _base_forms(h["text"])]
        for rule_id, form in base_forms:
            heading_by_form.setdefault(form, rule_id)
        for rule_id, form in base_forms:
            for variant in _inflections(form):
                heading_by_form.setdefault(variant, rule_id)

        self._automaton = ahocorasick.Automaton()
        for form, rule_id in heading_by_form.items():
            self._automaton.add_word(form, (form, rule_id))
        if heading_by_form:
            self._automaton.make_automaton()

    def find_matches(self, query: str) -> list[dict]:
        if not self._keywords:
            return []
        query_lower = query.lower()
        spans: list[tuple[int, int, str]] = []
        for end_index, (form, rule_id) in self._automaton.iter(query_lower):
            start_index = end_index - len(form) + 1
            before_ok = start_index == 0 or not query_lower[start_index - 1].isalnum()
            after_index = end_index + 1
            after_ok = after_index == len(query_lower) or not query_lower[after_index].isalnum()
            if before_ok and after_ok:
                spans.append((start_index, after_index, rule_id))

        # Longest span wins on overlap ("double strike" over "strike").
        accepted: list[tuple[int, int, str]] = []
        for start, end, rule_id in sorted(spans, key=lambda s: (s[0] - s[1], s[0])):
            if all(end <= a_start or start >= a_end for a_start, a_end, _ in accepted):
                accepted.append((start, end, rule_id))

        matched_ids: list[str] = []
        for _start, _end, rule_id in sorted(accepted):
            if rule_id not in matched_ids:
                matched_ids.append(rule_id)
        return [self._keywords[rule_id] for rule_id in matched_ids]

