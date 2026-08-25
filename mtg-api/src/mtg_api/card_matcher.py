from __future__ import annotations

import json
from pathlib import Path

import ahocorasick


class CardMatcher:
    def __init__(self, cards: list[dict]):
        self._cards_by_key: dict[str, dict] = {}
        self._automaton = ahocorasick.Automaton()
        for card in cards:
            key = card["name"].lower()
            self._cards_by_key[key] = card
            self._automaton.add_word(key, key)
        self._automaton.make_automaton()

    def find_matches(self, query: str) -> list[dict]:
        query_lower = query.lower()
        matched_keys: set[str] = set()
        for end_index, key in self._automaton.iter(query_lower):
            start_index = end_index - len(key) + 1
            before_ok = start_index == 0 or not query_lower[start_index - 1].isalnum()
            after_index = end_index + 1
            after_ok = after_index == len(query_lower) or not query_lower[after_index].isalnum()
            if before_ok and after_ok:
                matched_keys.add(key)
        return [self._cards_by_key[key] for key in matched_keys]


def load_card_matcher(cards_path: Path) -> CardMatcher:
    cards = []
    with cards_path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                cards.append(json.loads(line))
    return CardMatcher(cards)
