from __future__ import annotations

import re
from pathlib import Path

from mtg_ingestion.models import RuleChunk

# Matches "100. General", "100.1. These Magic rules apply..." and
# "100.1a In a two-player game...". Rule numbers are always three digits;
# an optional ".<digits>" subrule and an optional trailing letter follow.
_RULE_LINE = re.compile(r"^(?P<rule_id>\d{3}(?:\.\d+[a-z]?)?)\.?\s+(?P<text>\S.*)$")

# The glossary follows the numbered rules and isn't structured the same way.
# Parsing it is a deliberate follow-up, not part of this MVP.
_GLOSSARY_HEADING = "Glossary"

# Top-level section headers like "1. Game Concepts" or "2. Parts of a Card"
# (1-2 digit number, no subrule suffix -- that's what distinguishes them from
# _RULE_LINE's always-3-digit rule numbers).
_SECTION_HEADING = re.compile(r"^(?P<num>\d{1,2})\.\s+\S")


def _parent_id(rule_id: str) -> str | None:
    """"100" -> None, "100.1" -> "100", "100.1a" -> "100.1"."""
    if "." not in rule_id:
        return None
    base, _, tail = rule_id.partition(".")
    if tail and tail[-1].isalpha():
        return f"{base}.{tail[:-1]}"
    return base


def parse_rules_text(raw_text: str) -> list[RuleChunk]:
    """Parse Comprehensive Rules text into one RuleChunk per numbered rule.

    A rule's text can wrap across multiple lines in the source file; those
    continuation lines get folded into the chunk that owns them.

    The document opens with a Contents block that lists every top-level
    section ("1. Game Concepts", "100. General", ...) before the real body
    repeats those same headers -- and it even contains a "Glossary" line of
    its own. That block is skipped by watching for the first section
    heading's number to recur: the Contents block always lists each section
    number once, and the body immediately re-lists them in the same order,
    so the second occurrence of the first section number marks where the
    real body begins.
    """
    chunks: list[RuleChunk] = []
    current_id: str | None = None
    current_lines: list[str] = []
    in_toc = False
    first_section_num: str | None = None

    def flush() -> None:
        if current_id is None:
            return
        text = " ".join(current_lines).strip()
        if text:
            chunks.append(RuleChunk(rule_id=current_id, text=text, parent_id=_parent_id(current_id)))

    for raw_line in raw_text.splitlines():
        line = raw_line.strip()

        section_match = _SECTION_HEADING.match(line)
        if section_match:
            # A section header (e.g. "2. Parts of a Card") is never part of
            # a rule's own text -- skip it rather than folding it into the
            # current chunk as a continuation line. The document's very
            # first section heading opens the Contents block; that block
            # relists every section once before the real body immediately
            # re-lists them in the same order, so the second occurrence of
            # that same number marks where the real body begins.
            num = section_match.group("num")
            if first_section_num is None:
                first_section_num = num
                in_toc = True
            elif in_toc and num == first_section_num:
                in_toc = False
            continue

        if in_toc:
            continue

        if line == _GLOSSARY_HEADING:
            break

        match = _RULE_LINE.match(line)
        if match:
            flush()
            current_id = match.group("rule_id")
            current_lines = [match.group("text")]
        elif line and current_id is not None:
            current_lines.append(line)
        # Blank lines are ignored; a rule's chunk is only closed out when
        # the next numbered rule line (or the glossary) is reached.

    flush()
    return chunks


def parse_rules_file(raw_path: Path) -> list[RuleChunk]:
    raw_text = raw_path.read_text(encoding="utf-8")
    return parse_rules_text(raw_text)
