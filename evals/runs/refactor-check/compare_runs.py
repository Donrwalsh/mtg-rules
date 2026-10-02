"""Usage: python compare_runs.py <before.json> <after.json>
Lines starting MUST are acceptance failures; "look" lines are worth reading."""
import json
import sys

a, b = (json.load(open(p, encoding="utf-8")) for p in sys.argv[1:3])
must = look = 0
for case_id, ca in a["cases"].items():
    cb = b["cases"].get(case_id)
    if cb is None:
        print(f"MUST {case_id}: missing from the second run")
        must += 1
        continue
    for key in ("results", "context_hash", "error"):
        if ca.get(key) != cb.get(key):
            print(f"MUST {case_id}: {key} differs")
            must += 1
    fa, fb = ca.get("full") or {}, cb.get("full") or {}
    for key in ("answer", "citations", "judge", "generation_error"):
        if fa.get(key) != fb.get(key):
            print(f"look {case_id}: {key} differs")
            look += 1
print(f"{must} must-match differences, {look} to look at")
