from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Collection
from pathlib import Path

import ahocorasick

_LOWERCASE_WORD_RE = re.compile(r"\b[a-z][a-z'-]*\b")
_SENTENCE_END = ".!?"


def ordinary_words(rules: list[dict], min_count: int = 3) -> set[str]:
    # Words the rules use in lowercase are ordinary vocabulary ("library",
    # "sacrifice"); card names in the rules' examples are capitalized.
    counts = Counter(w for rule in rules for w in _LOWERCASE_WORD_RE.findall(rule["text"]))
    return {word for word, count in counts.items() if count >= min_count}


def _starts_sentence(query: str, index: int) -> bool:
    before = query[:index].rstrip(" \t\n\"'(“‘")
    return not before or before[-1] in _SENTENCE_END


class CardMatcher:
    def __init__(self, cards: list[dict], ordinary_words: Collection[str] = frozenset()):
        # A card named after an ordinary word ("Vigilance", "Library") only
        # matches when capitalized mid-sentence, i.e. deliberately named.
        self._ordinary_words = {w.lower() for w in ordinary_words}
        self._cards_by_key: dict[str, dict] = {}
        self._automaton = ahocorasick.Automaton()
        for card in cards:
            key = card["name"].lower()
            self._cards_by_key[key] = card
            self._automaton.add_word(key, key)
        self._automaton.make_automaton()

    def find_matches(self, query: str) -> list[dict]:
        if not self._cards_by_key:
            return []
        query_lower = query.lower()
        spans: list[tuple[int, int, str]] = []
        for end_index, key in self._automaton.iter(query_lower):
            start_index = end_index - len(key) + 1
            before_ok = start_index == 0 or not query_lower[start_index - 1].isalnum()
            after_index = end_index + 1
            after_ok = after_index == len(query_lower) or not query_lower[after_index].isalnum()
            if not (before_ok and after_ok):
                continue
            if key in self._ordinary_words and (
                not query[start_index].isupper() or _starts_sentence(query, start_index)
            ):
                continue
            spans.append((start_index, end_index, key))
        # A name inside a longer matched name ("Lightning" in "Lightning Bolt")
        # is part of that card, not a mention of its own.
        matched_keys = {
            key
            for start, end, key in spans
            if not any(s <= start and end <= e and (s, e) != (start, end) for s, e, _ in spans)
        }
        return [self._cards_by_key[key] for key in matched_keys]


def load_card_matcher(
    cards_path: Path, ordinary_words: Collection[str] = frozenset()
) -> CardMatcher:
    cards = []
    with cards_path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                cards.append(json.loads(line))
    return CardMatcher(cards, ordinary_words)
