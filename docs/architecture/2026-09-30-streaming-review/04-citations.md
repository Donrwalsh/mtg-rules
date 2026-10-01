# 4 · One owner for citation numbering and marker grammar

> Architecture review, 2026-09-30, branch `feature/streaming-answers`.
> Strength: **Worth exploring** · Dependency category: **in-process** (pure functions + the rules index and card matcher, both already in memory)
> Companion docs: [02 query pipeline](02-query-pipeline.md) (calls this from its generate strategy), [03 web answer stream](03-web-answer-stream.md) (calls the client half while streaming).

## Summary

A **citation** is the `[n]` in an answer that points at numbered source `n`. Four facts define it:

1. **Numbering:** source `n` is retrieval result `n − 1`, in context order.
2. **Grammar:** which bracket groups count as markers, which are malformed, and which are unfinished at the end of a draft.
3. **Validation:** dropping out-of-range numbers and malformed markers, normalizing `[1,3]` to `[1, 3]`, and finding rule references in the prose.
4. **Presentation:** a `Citation` built from a result, with card art or a rule heading attached.

These facts are spread over seven modules on two sides of the network, with ordering rules the callers have to remember:

| Fact | Server | Client |
|---|---|---|
| Numbering | `llm.build_context` (`enumerate(start=1)`) | `stream.liveResults` (`i + 1`), `fixtures` L387 (`i + 1`) |
| Marker grammar | `citations._MARKER_RE`, `_WELL_FORMED_RE` | `stream.MARKER`, `stream.OPEN_MARKER`, `segments.TOKEN_RE` |
| Rule-reference grammar | `citations._RULE_REF_RE` | `segments.TOKEN_RE` ("same lookarounds as the server") |
| Validation | `citations.cite_answer` (mutates `sources[n].cited`) | fixtures' fake validation (L378–390); page's broken-stream path (L229–236) |
| Presentation | `citations.citation_for` + `enrich.enrich_citations` (3 call sites) | none |

This doc proposes:

- **On the server**, a `NumberedSources` value returned by `build_context`. It owns numbering, the LLM context, live citations and final validation, and it returns new values instead of mutating results.
- **On the client**, one `markers` module that owns the grammar for both segmenting the finished answer and reading the live draft.
- **Across the seam**, one shared corpus of marker cases that both test suites run.

## Why do this

**Right away**

- **The streamed view and the final view disagree.** For `[1-3]` and `[1;2]`, the server's `_MARKER_RE` matches them, `_WELL_FORMED_RE` rejects them, and `cite_answer` deletes them. The client's `MARKER` doesn't match them, so while streaming they show as literal text `[1-3]` and then disappear when `done` arrives. Visitors see the text jump.
- **The two cut-off paths give different answers.** A server-side cut-off (`MAX_TOKENS`) runs `cite_answer`: out-of-range `[9]` is removed and `invalid_count` is set. A client-side cut-off (the stream broke) runs `liveCitations`: `[9]` stays in the text as plain characters and `citation_stats` keeps the head's zeros. The same partial answer looks different depending on which side noticed the cut-off.
- **The ordering rules are written only in comments.** "Must run before the results are dumped: it sets each cited result's `cited` flag" (`main.py:531`). "After build_context: display data must never reach the LLM" (`main.py:673`). Comments are the only thing enforcing them.
- **Enrichment is repeated.** Each source is enriched up to three times per request: as a result (L674), as a live source (L711) and as a final citation (L537). `citation_for` drops the result's `card`/`heading`, so each one is looked up again.

**Long term**

- **Locality.** Citation features land in one module per side: a new marker syntax, a new source type (e.g. tournament rules or the MTR), footnote numbering, or citing by rule id instead of number.
- **The grammar is shared but enforced on each side.** The shared corpus makes drift a test failure rather than a visual glitch someone has to notice.
- **Fewer aliasing hazards.** Today `sources` (a dict) and `all_results` (a list) hold the **same `QueryResult` objects**, and `cite_answer` mutates them on the worker thread. The spec's thread-safety note (L190–192) depends on nobody else touching them after phase 1. Returning new values makes that a structural guarantee instead of a convention.

## Current state

### Server: who calls what

```
_start_query
├─ all_results = _retrieve(...)
├─ context, sources = build_context(all_results)      llm.py:58   sources[n] is all_results[n-1] (same object)
├─ enrich_results(all_results, ...)                   must be after build_context
├─ live_sources = [citation_for(n, r) for n, r in sources.items()]   drops r.card / r.heading
├─ enrich_citations(live_sources, ...)                looks the cards up again
└─ (worker) _run_answer
     ├─ cited = cite_answer(answer, work.sources, rules_index)   mutates sources[n].cited = True
     ├─ citations = cited.citations                   built by citation_for again: no card/heading
     ├─ enrich_citations(citations, ...)              looks them up a third time
     └─ QueryResponse(results=work.results, ...)      must come after cite_answer (aliasing)
```

```python
# citations.py:114
def cite_answer(answer, sources, rules_index) -> CitedAnswer:
    parsed = parse_citations(answer, set(sources))
    for number in parsed.cited_numbers:
        sources[number].cited = True          # ← mutates the caller's results
    ...
```

### Client: the grammar, three times

```ts
// stream.ts:7–8
const OPEN_MARKER = /\[[\d,\s]*$/;
const MARKER = /\[(\s*\d+(?:\s*,\s*\d+)*\s*)\]/g;

// segments.ts:14
const TOKEN_RE =
  /\[(\s*\d+(?:\s*,\s*\d+)*\s*)\]|(?<![\d.$€£])(\d{3}\.\d+[a-z]?)(?![a-z\d]|\.\d)/g;
```

```python
# citations.py:16–21
_MARKER_RE = re.compile(r"([ \t]*)(\[[\d\s,;^\-–]*\d[\d\s,;^\-–]*\])")
_WELL_FORMED_RE = re.compile(r"\[\s*\d+(?:\s*,\s*\d+)*\s*\]")
_RULE_REF_RE = re.compile(r"(?<![\d.$€£])\d{3}\.\d+[a-z]?(?![a-z\d]|\.\d)")
```

The well-formed marker pattern is written three times, and the rule-reference pattern twice. The "malformed but marker-shaped" class (`_MARKER_RE` minus `_WELL_FORMED_RE`) exists only on the server.

### Client: validation, faked twice

```ts
// fixtures/index.ts:378 — the cut-off fixture imitates the server's validation
const answer = visibleDraft(text).trimEnd();
const citations = liveCitations(answer, base.citations);
...
rule_references: base.rule_references.filter((id) => answer.includes(id)),   // ≠ server's regex
results: base.results.map((r, i) => ({ ...r, cited: cited.has(i + 1) })),
citation_stats: { cited_count: citations.length, invalid_count: 0, uncited_answer: false },
```

```ts
// +page.svelte:229 — the broken-stream path imitates it again, differently
const answer = visibleDraft(draft).trimEnd() || null;
const citations = answer ? liveCitations(answer, sources) : [];
// no rule_references update, no citation_stats update, out-of-range markers left in
```

### Tests

- `test_citations.py` (17 tests): `parse_citations`, `find_rule_references`, `build_citations` and `cite_answer`, each as a separate piece. Good cases, at the wrong level for the ordering rules.
- `test_enrich.py` (3 tests).
- `segments.test.ts` (4) and `stream.test.ts` (4).
- **Nothing checks that client and server agree on any input.**

## Proposed design

### Server: `NumberedSources`

```python
# mtg_api/citations.py  (sketch)

@dataclass(frozen=True)
class CitedAnswer:
    answer: str
    citations: list[Citation]          # enriched, by number
    rule_references: list[str]
    stats: CitationStats
    results: list[QueryResult]         # copies, `cited` set; inputs untouched


class NumberedSources:
    """Retrieval results numbered 1..N in context order: the single source
    of truth for what [n] means, for the LLM, the live stream, and the
    validated answer."""

    def __init__(self, results: list[QueryResult]):
        self._results = list(results)
        # Built here, before anyone can enrich: display data never reaches the LLM.
        self.context: str = "\n\n".join(
            f"[{n}] {source_label(r)}: {r.text}" for n, r in self._numbered())

    def live(self) -> list[Citation]:
        """A citation for every source, for markers while the answer streams.
        Copies card/heading from the (already enriched) results."""

    def cite(self, answer: str, rules_index: RulesIndex) -> CitedAnswer:
        """Validate an answer against these sources. Pure: returns new
        results with `cited` set; never mutates."""
```

The pipeline after this change (with doc 02):

```python
results = retrieve(query, s, deps)
numbered = NumberedSources(results)                 # context frozen
enrich_results(results, deps.matcher, deps.rules_index)
head = StreamHead(results=results, sources=numbered.live(), ...)
...
# worker
cited = numbered.cite(answer, deps.rules_index) if answer is not None else None
response = StreamDone(results=cited.results if cited else results, answer=..., ...)
```

The ordering rules become structural:

- **"Context before enrichment."** `context` is computed in the constructor. Enriching afterwards can't change it. Better still, `source_label` reads only retrieval fields, and a test pins that `context` is unchanged after `enrich_results`.
- **"Cite before dumping results."** `cite` returns the results to dump, so there is nothing to order.
- **"Enrich every citation."** `citation_for(n, result)` copies `result.card` and `result.heading`. `enrich_citations` stays only for the cache-hit legacy-row repair (`main.py:639–641`), which already has its own comment.

`build_context(results) -> (str, dict)` is replaced by `NumberedSources`. `prompt_chars(query, numbered.context)` and `GeminiAnswerer.stream(query, numbered.context)` keep taking a plain `str`, so the answerer never learns about citations.

### Option: the server sends the numbers

Each result in the head could carry `number: int`, set by `NumberedSources`. That would remove every `i + 1` on the client: `liveResults`, the fixtures and EvidencePanel's pairing. Cost: one more field on `QueryResult`, plus the cache-hit repair for rows stored without it. **Open question 1.**

### Client: `markers.ts`

```ts
// src/lib/citations/markers.ts  (sketch)

/** A well-formed marker: [1], [1, 3], [1,3]. Mirrors the server's _WELL_FORMED_RE. */
export const MARKER = /\[(\s*\d+(?:\s*,\s*\d+)*\s*)\]/g;
/** Marker-shaped but malformed ([1-3], [1;2], [^1]): the server drops these. */
const MARKERISH = /[ \t]*\[[\d\s,;^\-–]*\d[\d\s,;^\-–]*\]/g;
/** An unfinished marker at the end of a draft. */
const OPEN = /\[[\d\s,;^\-–]*$/;
/** A rule number in prose. Mirrors the server's _RULE_REF_RE. */
export const RULE_REF = /(?<![\d.$€£])(\d{3}\.\d+[a-z]?)(?![a-z\d]|\.\d)/g;

/** The draft as it may be shown while streaming: an unfinished marker at
 * the end held back, malformed markers hidden, as `done` will. */
export function visibleDraft(draft: string): string;

/** Distinct cited numbers that are in `valid`, in first-appearance order. */
export function citedNumbers(text: string, valid: Set<number>): number[];

/** Client-side best effort of the server's cite step, for a stream that
 * broke before `done`: drops invalid and malformed markers, finds rule
 * references, fills citation_stats. */
export function citeDraft(draft: string, sources: Citation[], ruleIds?: Set<string>): DraftCitation;
```

`segments.ts` imports `MARKER` and `RULE_REF` instead of defining `TOKEN_RE`. `liveCitations` becomes `citedNumbers` plus a lookup. The fixtures and the page's broken-stream path both call `citeDraft`, so they agree with each other and, through the corpus, with the server.

`citeDraft` can't know which rule references exist without the rules index. It only keeps references that are among the head's sources (`rule_id`s it has). That is a subset of what the server returns, and it's documented as such.

### The shared corpus

```json
// contracts/citation-markers.json  (repo root; read by both test suites)
[
  { "name": "single",          "text": "Yes [1].",       "valid": [1, 2], "clean": "Yes [1].",       "cited": [1] },
  { "name": "list normalized", "text": "A [1,2].",       "valid": [1, 2], "clean": "A [1, 2].",      "cited": [1, 2] },
  { "name": "out of range",    "text": "A [9].",         "valid": [1],    "clean": "A.",             "cited": [], "invalid": 1 },
  { "name": "range malformed", "text": "A [1-3].",       "valid": [1, 2, 3], "clean": "A.",          "cited": [], "invalid": 1 },
  { "name": "semicolon",       "text": "A [1;2] b",      "valid": [1, 2], "clean": "A b",            "cited": [], "invalid": 1 },
  { "name": "not a marker",    "text": "Pay [X] mana.",  "valid": [1],    "clean": "Pay [X] mana.",  "cited": [] },
  { "name": "open at end",     "draft": "Yes [1",        "visible": "Yes " },
  { "name": "price not rule",  "text": "$100.50 and 702.19b", "rules": ["702.19b"], "rule_refs": ["702.19b"] }
]
```

- `test_citations.py` adds one `pytest.mark.parametrize` over the corpus, run through `NumberedSources.cite`.
- `markers.test.ts` adds one `it.each` over the same file, run through `citeDraft` and `visibleDraft`.
- A case with `"server_only": true` documents a deliberate difference (e.g. rule references the client can't check).

## Implementation plan

1. **Add the corpus** at `contracts/citation-markers.json`, seeded from the existing `test_citations.py`, `segments.test.ts` and `stream.test.ts` cases, plus the drift cases above. Add the two parametrized runners. Expect the client side to fail on `[1-3]` and `[1;2]` (the visible-draft flicker). Mark those `xfail`/`it.fails` for now.
2. **Server: make `citation_for` copy `card`/`heading`.** Remove the `enrich_citations` calls at `main.py:537` and `:711`. Keep `:641` (cache repair). `test_enrich.py::test_citations_are_enriched_the_same_way` moves to a `NumberedSources.live()` test.
3. **Server: introduce `NumberedSources`** in `citations.py`, with `context`, `live()` and `cite()`. `cite` returns copied results (`r.model_copy(update={"cited": ...})`). Port `build_context`'s callers (`main.py:672`, the eval context hash at `:679`, which hashes `numbered.context`, unchanged). Delete `build_context` and `build_citations` from the public surface.
4. **Server: delete the mutation.** `cite_answer` becomes `NumberedSources.cite`. Remove the "must run before the results are dumped" comment, because the constraint is gone. Check that `QueryResult.cited` is never set anywhere else (`grep "\.cited ="`).
5. **Client: create `citations/markers.ts`.** Point `segments.ts` at its patterns. Move `visibleDraft`/`liveCitations`/`liveResults` from `stream.ts` (or into doc 03's reducer, which calls them). Add `citeDraft`. Corpus cases go green and the `xfail`s are removed.
6. **Client: use `citeDraft`** in the page's broken-stream path (or doc 03's `cutOff`) and in the cut-off fixture. Delete the fixture's hand validation.
7. **(If chosen) server-sent numbers.** Add `number` to `QueryResult`, set by `NumberedSources`, and backfill on cache-hit repair. Remove the client's `i + 1` uses.
8. **Verify.** `uv run pytest`, `npm test`, `npx playwright test`, and the eval harness. `context_hash` must be unchanged for the same retrieval, so a before/after eval run should produce identical hashes.

Rough size: server ±0 lines (moved, plus ~25 for `NumberedSources`, minus the mutation and enrich calls). Client +60 (`markers.ts`, `citeDraft`), −40 (`stream.ts`, the fixture's fake validation). Corpus ~40 cases.

## Test plan

| Level | Test | Replaces |
|---|---|---|
| Corpus (both sides) | parametrized over `contracts/citation-markers.json` | the overlapping single-case tests in `test_citations.py`, `segments.test.ts` and `stream.test.ts` (delete the duplicates; keep the log-assertion tests) |
| `NumberedSources` | numbering is 1..N in order; `context` unchanged after `enrich_results`; `live()` carries card/heading with no second lookup (spy on `card_matcher.by_oracle_id`); `cite()` leaves its inputs unmodified | `test_llm.py::test_build_context_*`, `test_citations.py::test_cite_answer_*`, `test_enrich.py::test_citations_are_enriched_the_same_way` |
| `citeDraft` | broken-stream draft gives clean text, the right citations, stats and `answer_complete: false` handling | page logic with no tests |
| Pipeline (doc 02) | a cut-off answer has the same citation shape whether it came from `MAX_TOKENS` (server) or a broken stream (client) | none |

## Risks

- **Cached answers.** Stored rows hold citations from before this change. They stay valid because the shape doesn't change. Only the optional `number` field needs the cache-hit repair.
- **Eval comparability.** `context` must be byte-for-byte what `build_context` produced, since `context_hash` and the eval answer caches depend on it. The corpus plus a before/after eval run guard this.
- **Client-side rule references are a subset of the server's.** This only affects the rare broken-stream path, and `done` replaces it whenever it arrives.

## Open questions (for grilling)

1. Should the server send `number` on each result (no client numbering at all), or should `i + 1` stay client knowledge, isolated in `markers.ts`?
2. Where should the corpus live? `contracts/` at the repo root (both packages read upward), or `mtg-api/tests/fixtures/` with the web test importing across packages? The Docker build of `mtg-web` doesn't need it either way, because tests don't run in the image.
3. Should the client hide malformed markers while streaming (no flicker, but it hides text the model really wrote), or show them until `done` (as today)?
