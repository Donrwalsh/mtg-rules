# Query-pipeline refactor: before/after eval notes

- retrieval-before: 20261001T153639Z-348139a-retrieval.json (REGRESSIONS 0 vs baseline-retrieval.json;
  config diffs are newer data files + phi4 settings, expected)
- full-before: 20261001T153725Z-348139a-dirty-full.json
  (dirty = an uncommitted Task 9 test file in the tree; the backend container was built from 348139a)
  judge correct 91.4%, verdict 60.0%, invalid citations 8, context overruns 0,
  max prompt tokens 2680, mean generate 21.3 s
- full-control: 20261001T155305Z-5518331-full.json (same container/code as full-before)
  NOISE FLOOR: 0 must-match diffs; 15/38 answers differ in text (phi4 is NOT deterministic here
  at temperature 0 + seed 0, likely from partial CPU offload); 2 judge grade flips
  (hexproof-own-target, legend-rule: correct -> partial) = "REGRESSIONS (2)"; judge correct 85.7% vs 91.4%
- PRE-EXISTING NONDETERMINISM (not the refactor): hybrid_search iterates set(dense)|set(sparse) of
  string ids, so tied scores come back in hash-seed order, which changes on every API process start.
  Same refactored code, plain restart: 15 must-match diffs. Tie-insensitive check: 0 real differences
  (retrieval-after 20261001T160956Z-5c18103: 6 tie-only; after restart 20261001T161046Z: 9 tie-only).
- keys-after == keys-before (blocking, results, done)
- full-after: 20261001T161112Z-5c18103-full.json vs baseline-full-phi4.json
  REGRESSIONS (1): humility-opalescence correct->partial (control had 2 with no code change)
  judge correct 88.6% (before 91.4%, control 85.7%); verdict 60.0% = ; decline 100% = ; errors 0;
  context overruns 0; max prompt tokens 2680 =
  retrieval: 0 real differences, 9 tie-only cases; the 19 must-match lines are those 9 cases
  (results + context_hash) plus path-optional-search, whose context differs only by two
  equal-score Settle the Wreckage rulings (same tie cause, invisible in the identifiers)
- nginx stream: results/thinking at 0.4 s, 348 deltas from 17.1 s, done at 56.2 s
VERDICT: no difference attributable to the refactor beyond the measured noise.
