# Keyword Rule Matcher Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a query names a keyword ability, always include that keyword's 702.N heading and subrules in `/api/v1/query` results and the LLM context.

**Architecture:** New in-memory `KeywordMatcher` (Aho-Corasick over keyword surface forms, built from the parsed rules JSONL), cached with `lru_cache` and warmed in `lifespan`, exactly like `CardMatcher`. `main.query` turns matches into `keyword_rule_match` results placed after card and card-ruling results, and drops vector hits with a duplicate `rule_id`.

**Tech Stack:** FastAPI, pyahocorasick, pytest.

**Spec:** [docs/superpowers/specs/2026-09-26-keyword-rule-matcher-design.md](../specs/2026-09-26-keyword-rule-matcher-design.md)

## Global Constraints

- No Qdrant calls for keyword lookup; rules come from the latest `rules_*.jsonl` in `settings.parsed_dir`.
- `match_type="keyword_rule_match"`, `score=1.0`, `source="rule"`, `title=rule_id`.
- Result order: card → card ruling → keyword rule → vector hit.
- Common-word keywords (reach, flash, ward, fear, ...) are matched anyway; no heuristic.

---

## Task 1: `KeywordMatcher`

**Files:**
- Create: `mtg-api/src/mtg_api/keyword_matcher.py`
- Test: `mtg-api/tests/test_keyword_matcher.py`

**Interfaces:**
- Produces: `KeywordMatcher(rules: list[dict])`, `.find_matches(query: str) -> list[dict]` returning `{"keyword", "rule_id", "rules"}` entries; `load_keyword_matcher(rules_path: Path) -> KeywordMatcher`.

- [ ] **Step 1:** Write failing tests with a small in-file `RULES` fixture (702.1 prose, Hexproof + subrules, First Strike, Double Strike, Trample, Flash, Flashback, a fake overlapping `Strike`, `For Mirrodin!`, `Daybound and Nightbound`, `∞ (Infinity)`, plus a non-702 rule).
- [ ] **Step 2:** Run `pytest tests/test_keyword_matcher.py` — expect import failure.
- [ ] **Step 3:** Implement: filter `702.N` headings (skip `702.1`), group subrules by `parent_id` in file order, register surface forms (base forms take precedence over inflections), Aho-Corasick + word-boundary check, greedy longest-span overlap resolution, dedupe by heading.
- [ ] **Step 4:** Tests pass. Commit.

## Task 2: Wire into `/api/v1/query`

**Files:**
- Modify: `mtg-api/src/mtg_api/main.py`
- Test: `mtg-api/tests/test_query.py`, `mtg-api/tests/test_lifespan.py`

**Interfaces:**
- Consumes: `KeywordMatcher`, `load_keyword_matcher` from Task 1.
- Produces: `get_keyword_matcher()` dependency (override point for tests).

- [ ] **Step 1:** Extend `_override` in `test_query.py` with a `rules=` argument overriding `get_keyword_matcher`; add tests for keyword results, ordering (card → card ruling → keyword → vector), and `rule_id` dedupe. Extend the lifespan test to expect `keyword_matcher`.
- [ ] **Step 2:** Run — expect failures.
- [ ] **Step 3:** Add `get_keyword_matcher` (`lru_cache`, latest `rules_*.jsonl`), warm it in `lifespan`, build `keyword_results`, drop duplicate-`rule_id` vector hits, concatenate in order.
- [ ] **Step 4:** Full `pytest` passes. Commit.

## Task 3: Manual verification

- [ ] Run the stack locally, POST "Can I target my own creature that has hexproof with Lightning Bolt?", confirm 702.11 and 702.11a–h appear as `keyword_rule_match` and the answer says yes.
- [ ] Update `README.md` if it documents the retrieval pipeline.
