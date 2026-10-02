"""Usage: python compare_retrieval_ties.py <a.json> <b.json>
Retrieval equality that ignores order only among results with exactly equal scores
(hybrid_search breaks score ties in set order, which changes with each process)."""
import json
import sys
from collections import Counter

a, b = (json.load(open(p, encoding="utf-8"))["cases"] for p in sys.argv[1:3])
bad = ties = 0
for cid, ca in a.items():
    ra, rb = ca["results"], b[cid]["results"]
    key = lambda r: tuple((k, r[k]) for k in sorted(r) if k != "rank")  # noqa: E731
    same_scores = [r["score"] for r in ra] == [r["score"] for r in rb]
    same_items = Counter(map(key, ra)) == Counter(map(key, rb))
    if ra == rb:
        continue
    if same_scores and same_items:
        ties += 1
    else:
        bad += 1
        print(f"MUST {cid}: results differ beyond tied-score order")
print(f"{bad} real differences, {ties} cases differ only in tied-score order")
