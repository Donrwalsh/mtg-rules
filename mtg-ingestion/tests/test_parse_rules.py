from mtg_ingestion.parse.rules import _parent_id, parse_rules_text

SAMPLE = """\
Introduction
Some preamble text that should be ignored entirely.

100. General
100.1. These Magic rules apply to any Magic game with two or more
players, including two-player games and multiplayer games.
100.1a In a two-player game, another term for that player's opponent
is "the opponent."
100.2. To play, each player needs their own deck of traditional Magic
cards, small items to represent any tokens and counters, and some
way to clearly track life totals.

702. Keyword Abilities
702.19. Flying
702.19a Flying is a static ability.
702.19b A creature with flying can't be blocked except by creatures
with flying and/or reach.

Glossary
Ability
See rule 113, "Abilities."
"""


def test_parses_top_level_rule() -> None:
    chunks = {c.rule_id: c for c in parse_rules_text(SAMPLE)}
    assert "100" in chunks
    assert chunks["100"].text == "General"
    assert chunks["100"].parent_id is None


def test_folds_wrapped_lines_into_one_chunk() -> None:
    chunks = {c.rule_id: c for c in parse_rules_text(SAMPLE)}
    text = chunks["100.1"].text
    assert "two or more players" in text
    assert "multiplayer games." in text
    # confirm the wrap didn't create a stray second chunk
    assert text.count("These Magic rules apply") == 1


def test_parent_ids_across_all_three_levels() -> None:
    chunks = {c.rule_id: c for c in parse_rules_text(SAMPLE)}
    assert chunks["100"].parent_id is None
    assert chunks["100.1"].parent_id == "100"
    assert chunks["100.1a"].parent_id == "100.1"
    assert chunks["702.19b"].parent_id == "702.19"


def test_stops_before_glossary() -> None:
    chunks = {c.rule_id: c for c in parse_rules_text(SAMPLE)}
    assert "113" not in chunks
    assert all(not c.text.startswith("See rule 113") for c in chunks.values())


def test_content_hash_changes_when_text_changes() -> None:
    chunks = {c.rule_id: c for c in parse_rules_text(SAMPLE)}
    original_hash = chunks["702.19a"].content_hash

    mutated = SAMPLE.replace(
        "702.19a Flying is a static ability.",
        "702.19a Flying is a static ability that changed.",
    )
    mutated_chunks = {c.rule_id: c for c in parse_rules_text(mutated)}
    assert mutated_chunks["702.19a"].content_hash != original_hash


TOC_SAMPLE = """\
Contents

1. Game Concepts
100. General
101. The Magic Golden Rules

2. Parts of a Card
200. General

Glossary

Credits

1. Game Concepts

100. General

100.1. These Magic rules apply to any Magic game with two or more
players, including two-player games and multiplayer games.
100.1a In a two-player game, another term for that player's opponent
is "the opponent."

2. Parts of a Card

200. General

200.1. A card's characteristics are name, mana cost, color, type,
supertype, subtype, rules text, power, toughness, and loyalty.

Glossary

Ability
See rule 113, "Abilities."
"""


def test_skips_table_of_contents_and_parses_leaf_rules() -> None:
    chunks = {c.rule_id: c for c in parse_rules_text(TOC_SAMPLE)}

    # The TOC lists only top-level rules; the real body's leaf-level
    # subrules must still show up once the TOC is skipped.
    assert "100.1" in chunks
    assert "100.1a" in chunks
    assert chunks["100.1a"].text == (
        'In a two-player game, another term for that player\'s opponent '
        'is "the opponent."'
    )


def test_toc_entries_do_not_produce_duplicate_or_corrupted_chunks() -> None:
    all_chunks = parse_rules_text(TOC_SAMPLE)
    rule_ids = [c.rule_id for c in all_chunks]

    # Only one "100" chunk should exist (from the real body, not the TOC).
    assert rule_ids.count("100") == 1

    chunks = {c.rule_id: c for c in all_chunks}
    # A top-level rule's text must not have a following section heading
    # ("2. Parts of a Card") glommed onto it as a stray continuation line.
    assert chunks["200"].text == "General"
    assert "Parts of a Card" not in chunks["100"].text


def test_parent_id_helper_directly() -> None:
    assert _parent_id("100") is None
    assert _parent_id("100.1") == "100"
    assert _parent_id("100.1a") == "100.1"
    assert _parent_id("704.5k") == "704.5"
