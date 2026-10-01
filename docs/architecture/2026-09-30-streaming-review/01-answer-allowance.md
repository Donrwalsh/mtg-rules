# 1 · Deepen the answer allowance

> Architecture review, 2026-09-30, branch `feature/streaming-answers`.
> Strength: **Strong** · Dependency category: **local-substitutable** (in-memory SQLite) + in-process (slots, lock)
> Companion docs: [02 query pipeline](02-query-pipeline.md) (natural home for this module), [06 answer runner](06-answer-runner.md) (owns the slots' lifetime).

## Summary

The permission to write one LLM answer is called the **answer allowance** in this doc. Deciding it, holding it and paying for it currently takes eight functions in three modules: `check_gate`, `reserve_usage`, `finalize_usage` and `record_usage` in `usage.py`, `GenerationSlots` in `streaming.py`, and `_gate`, `_reserve`, `_finalize` and `_record` in `main.py`. `_start_query` and `_run_answer` must call them in the right order, pass the right flags, and hand the slot's release function from the request thread to the worker thread.

This doc proposes one `allowance` module with a small interface:

- `admit` makes the gate decision.
- `served_from_cache` and `retrieval_only` cover the two ways to finish without generating.
- `start_answer` takes the slot and reserves spend.
- `settle` and `close` end the answer.

Each step checks its own preconditions, so the pipeline can't get the order wrong. Because the check and the reservation then happen in one place, the module can also make them one atomic step. That turns `daily_budget_usd` from a soft cap into an exact one within the process.

## Why do this

**Right away**

- **The branch is unmerged.** The reservation and slot logic arrived in the last five commits (`5325092`, `45ec94d`, `b64065a`, `b25652a`). Reshaping it before it reaches `main` costs one PR. Reshaping it later costs a migration of habits and tests.
- **The tests reach past the interface.** `test_query_stream.py` takes slots straight from the global `main.generation_slots` (L100, L132). `test_gating_flow.py::test_reservation_is_pending_while_the_answer_is_written` peeks at table rows from inside a fake answerer. These are signs the behaviour has no interface of its own to test through.
- **Fail-closed and fail-open sit side by side with nothing saying so.** The gate fails closed: no DB means retrieval-only (`_gate`, L233–239). The reservation fails open: no DB row means generate anyway and try to record at the end (`_reserve`, L577–582, then the `reservation is None` branch in `_finalize`, L492–495). That may be the right policy, but today it is only visible by reading two wrappers 340 lines apart.

**Long term**

- **Locality.** Every future change to quota policy lands in one module. That includes a new outcome, a new exemption, per-user quotas, a budget per model, or moving from one process to several workers.
- **Leverage.** Both endpoints, the eval path and any future entry point (a Discord bot, a batch re-answer job) get correct accounting by calling the same small interface.
- **Exact budget in-process.** It costs almost nothing once the check and the reservation live in the same module (see [The budget gap](#the-budget-gap)).

## Current state

### Who calls what, in order

```
query_stream / query
└─ _start_query                                   main.py:585
   ├─ tracked = request.generate and not settings.eval_mode
   ├─ if tracked and s.gating_enabled and not admin:
   │     gate = _gate(...) ──► check_gate         (fail closed → Gate("global_budget", 0))
   ├─ cache hit?
   │     cache_remaining = 0 if gate.degraded == "global_budget" else remaining
   │     _record(outcome="cached") ──► record_usage
   ├─ generating = request.generate and gate.degraded is None
   ├─ if generating and not settings.eval_mode:
   │     release = generation_slots.acquire(...)  (None → HTTP 429)
   ├─ try:
   │     _retrieve(...)                           (embeddings + 2 Qdrant searches)
   │     if tracked and gate.degraded: _record(outcome=_DEGRADED_OUTCOMES[...])
   │     if generating and tracked and remaining is not None: remaining -= 1
   │     reservation = _reserve(...) ──► reserve_usage  (fail open → None)
   │     AnswerJob(lambda emit: _run_answer(work, emit)).start()
   │  except BaseException:
   │     if release: release()                    (job never started)
   └─ ...
[worker thread]
_run_answer                                       main.py:504
   ├─ ... stream ...
   ├─ _finalize(work, outcome, generation)
   │     if not work.tracked: return
   │     if work.reservation is None: _record(...)      (reserve had failed)
   │     else: finalize_usage(...)
   └─ finally: if work.release: work.release()
```

### The facts a caller must hold today

This is the real **interface** of the allowance as it stands: everything `_start_query` and `_run_answer` must know to use it correctly.

1. Check the gate only when `tracked and s.gating_enabled and not admin`.
2. `tracked` reads the global `settings.eval_mode`, not the per-request `s`.
3. Take a slot only when `generating and not settings.eval_mode`. Admins pass `ip_limit=None`.
4. A cache hit never takes a slot, records `cached`, and must report `answers_remaining = 0` when the global budget is spent.
5. A degraded request records `degraded_ip` or `degraded_global` through a string map (`_DEGRADED_OUTCOMES`).
6. Decrement `answers_remaining` by one when about to generate, but only when tracked and gated.
7. Reserve after retrieval, because the worst-case cost needs `prompt_chars(query, context)`.
8. A reservation can be `None`, and then settlement has to take a different path.
9. The release function crosses threads. The worker releases it in `finally`, unless the job never started, in which case the request thread releases it.
10. Settlement is skipped entirely when not tracked.

Ten rules, two threads, three files. This is a **shallow** arrangement: the interface (the rules) is about as large as the implementation (roughly 60 lines of actual logic).

### The deletion test

Delete `_gate`, `_reserve`, `_record` and `_finalize`, and the code they wrapped (`try/except/log`) reappears inline in `_start_query` and `_run_answer`. Nothing gets more complex, because the wrappers are pass-throughs. Now delete the ten rules: there is nowhere for them to go except back into the pipeline. That shows the missing piece is a module that owns the rules. The wrappers themselves add nothing.

### The budget gap

`spec 2026-09-30-streaming-answers-design.md` L153–156 accepts this deliberately:

> Reserve … comes after retrieval because the worst-case estimate needs the context's length. The gap is one retrieval (well under a second), and the slot caps limit how many requests can sit in it.

Here is the concrete scenario. `daily_budget_usd = 1.00`, today's spend is `0.999`, and four visitors on four IPs ask at the same moment.

```
t0  A: check_gate → spend 0.999 < 1.00 → open     A: acquire slot (1/4)
t0  B: check_gate → spend 0.999 < 1.00 → open     B: acquire slot (2/4)
t0  C: …open                                      C: acquire slot (3/4)
t0  D: …open                                      D: acquire slot (4/4)
t1  A,B,C,D: retrieval
t2  A,B,C,D: reserve_usage(worst case)   → spend ≈ 0.999 + 4 × worst_case
```

The overshoot is bounded by `max_concurrent_generations × worst_case_cost`. Even with the reservation moved earlier, `check_gate` and `reserve_usage` are separate transactions, so the gap only gets smaller. It doesn't close. Closing it requires that **one** module hold the check and the insert under one lock. Today no module is in a position to do that. Once the allowance exists, it's a few lines.

The dollar amounts are small: one worst-case answer is cents. Whether to close the gap is still a real decision, but this doc's case doesn't rest on it. The case rests on the ten rules.

## Proposed design

### Module and interface

New module `mtg-api/src/mtg_api/allowance.py`. `GenerationSlots` moves here from `streaming.py`. That module's own docstring says it "knows nothing about queries", and the slots are about answers, not about SSE.

```python
# mtg_api/allowance.py  (sketch: names are proposals for the grilling session)

@dataclass(frozen=True)
class Caller:
    """Who is asking, as far as quotas care."""
    ip_bucket: str
    is_admin: bool


class GenerationsBusy(Exception):
    """Every generation slot this caller may use is taken. Maps to HTTP 429."""


class Allowances:
    """The answer allowance for one process: the per-IP and global gate,
    concurrent-generation slots, and the spend ledger (usage rows).

    One instance per app. Thread-safe. Never raises for a database
    failure: the gate fails closed, the ledger fails open (see Policy)."""

    def __init__(self, engine: Engine, slots: GenerationSlots, lock: threading.Lock,
                 *, clock: Callable[[], datetime] = lambda: datetime.now(UTC)): ...

    def admit(self, caller: Caller, s: Settings, *, tracked: bool, model: str) -> Admission:
        """Decide whether this request may have an LLM answer. Cheap; takes
        nothing. `tracked=False` (eval mode, or generate=False) admits
        without gating, slots or ledger rows."""


class Admission:
    degraded: Literal["ip_quota", "global_budget"] | None
    answers_remaining: int | None       # before this request uses one

    def served_from_cache(self) -> int | None:
        """Ledger: `cached`. Returns answers_remaining to show (0 when the
        global budget is spent: a cache hit doesn't promise new answers)."""

    def retrieval_only(self) -> int | None:
        """Ledger: degraded_ip / degraded_global if degraded, nothing for a
        plain generate=False. Returns answers_remaining to show."""

    def start_answer(self, prompt_chars: int) -> Spend | None:
        """Atomically: re-check the gate, take a slot, reserve worst-case
        spend. Raises GenerationsBusy. Returns None when the gate closed
        while this request was retrieving: the caller treats the request as
        retrieval-only (and calls retrieval_only())."""


class Spend:
    answers_remaining: int | None       # after this answer

    def settle(self, generation: Generation, *, failed: bool) -> None:
        """Replace the worst-case reservation with the real (or estimated)
        cost. Idempotent; the first call wins."""

    def close(self) -> None:
        """Release the slot. Idempotent. If settle() never ran, the
        reservation keeps its worst-case cost (logged): the safe failure."""
```

Each `Admission` allows exactly one exit: `served_from_cache`, `retrieval_only` or `start_answer`. Calling a second exit raises `RuntimeError`. That turns rules 4–9 above from conventions into checked state.

### How the pipeline reads afterwards

```python
caller = Caller(ip_bucket(host), is_admin(http_request))
tracked = request.generate and not s.eval_mode
admission = d.allowances.admit(caller, s, tracked=tracked, model=s.gemini_model)

if hit := cache_lookup(...):
    remaining = admission.served_from_cache()
    return cached_stream(hit, remaining)

results = retrieve(...)
context, sources = build_context(results)

spend = None
if request.generate and admission.degraded is None:
    spend = admission.start_answer(prompt_chars(request.query, context))   # may raise → 429
if spend is None:
    remaining = admission.retrieval_only()
    return head_only_stream(results, admission.degraded, remaining)

job = runner.start(lambda emit: write_answer(work, spend, emit))   # see doc 06
```

On the worker:

```python
def write_answer(work, spend: Spend, emit):
    try:
        acc = stream_from_answerer(...)
        spend.settle(estimate_generation(acc, ...), failed=failure is not None)
        ...
    finally:
        spend.close()
```

The pipeline no longer sees `tracked`, `reservation`, `release`, `record`, `_DEGRADED_OUTCOMES` or `remaining - 1`.

### Policy hidden behind the seam

| Concern | Today | Behind `Allowances` |
|---|---|---|
| Gate DB failure | `_gate` logs, returns `Gate("global_budget", 0)` | Same, inside `admit` and inside the re-check |
| Reserve DB failure | `_reserve` → `None`; `_finalize` falls back to `record_usage` | `Spend` remembers it has no row; `settle` inserts instead of updating |
| Ledger write failure | `_record` / `_finalize` log and swallow | Same, one helper |
| Eval mode | `tracked` flag + `not settings.eval_mode` check before slots | `tracked=False` → an `Admission` that never gates, never takes a slot, never writes |
| Admin | skip gate; `ip_limit=None` | Same, from `Caller.is_admin` |
| `answers_remaining` arithmetic | three sites | `Admission` / `Spend` properties |
| Worst-case cost | `worst_case_cost(prompt_chars(...), s)` at the call site | `start_answer(prompt_chars)` |
| Exactness | gap between check and reserve | re-check + slot + reserve under `lock` |

### Exact budget: what the lock covers

```python
def start_answer(self, prompt_chars):
    self._exit("start_answer")
    with self._lock:                               # one per process
        if self._gated:
            gate = self._check_gate()              # fresh read, inside the lock
            if gate.degraded:
                self._late_degraded = gate.degraded
                return None
        release = self._slots.acquire(...)         # raises GenerationsBusy
        row = self._reserve(worst_case_cost(prompt_chars, self._s))
    return Spend(...)
```

The lock is held for three small SELECTs and one INSERT, at most `max_concurrent_generations` times per answer cycle, which is trivial contention. It is exact **within one process**. The spec already assumes one uvicorn worker ("in memory: the API runs as one process"). If that ever changes, this lock becomes a Postgres advisory lock behind the same interface, and nothing outside the module notices. That's the locality payoff.

### Slot timing: one behaviour change to decide

Today the slot is taken **before** retrieval, so a request that would get a 429 doesn't pay for retrieval. Taking it inside `start_answer` (after retrieval) is what makes "re-check + slot + reserve" one atomic step.

- **Cost of moving it:** a request that ends in 429 now runs one retrieval first, about two embeddings and two Qdrant queries on the shared 2-vCPU VM. nginx already limits the burst per IP (`limit_req burst=5`, `limit_conn 2`), and a 429 is rare (per-IP cap 1, global cap 4).
- **Alternative:** keep a two-step `admission.claim_slot()` before retrieval and `claim.reserve(prompt_chars)` after it. That gives an early 429 but adds one more method and a state to the interface.

Recommendation: move it. Fewer states and an exact budget are worth one wasted retrieval on a rare path. **Open question for grilling.**

## Implementation plan

Each step leaves the suite green.

1. **Write the allowance tests first** (`tests/test_allowance.py`, in-memory SQLite from `conftest.memory_engine`, a fresh `GenerationSlots` and lock per test, a fixed `clock`). See [Test plan](#test-plan) for cases. They fail: the module doesn't exist.
2. **Create `allowance.py`.** Move `GenerationSlots` there from `streaming.py`, re-exported from `streaming` for one commit if that helps the diff. Implement `Caller`, `Allowances`, `Admission`, `Spend` and `GenerationsBusy` on top of the existing `usage.py` functions (`check_gate`, `reserve_usage`, `finalize_usage`, `record_usage`, `worst_case_cost`, `cost_usd`), which stay as they are as the ledger's storage functions. Move `_DEGRADED_OUTCOMES` here. Tests go green.
3. **Wire the dependency.** Add `get_allowances(engine = Depends(get_db_engine)) -> Allowances` in `main.py`, built over a module-level `GenerationSlots` and `threading.Lock`. Doc 06 moves them into the lifespan. Add `allowances` to `QueryDeps`. Tests that override `get_db_engine` keep working unchanged, because the allowance picks up the overridden engine.
4. **Switch `_start_query`.** Replace the gate block, the cache-hit `_record`, the degraded `_record`, the `remaining` decrement, the slot acquire and `_reserve` with the `admit` / `served_from_cache` / `retrieval_only` / `start_answer` calls. Map `GenerationsBusy` to the existing 429 detail string. The `except BaseException` block now calls `spend.close()` when a `Spend` exists.
5. **Switch `_run_answer`.** Replace `_finalize` with `spend.settle(...)` and `work.release()` with `spend.close()`. Remove `tracked`, `reservation`, `record` and `release` from `_AnswerWork`, and add `spend: Spend | None`.
6. **Delete** `_gate`, `_reserve`, `_finalize`, `_record` (once doc 02's cache-hit path no longer needs it) and the `generation_slots` global from `main.py`.
7. **Migrate tests** (see below). Run `uv run pytest`, `ruff check` and `ruff format --check`.
8. **Docs.** Add a short "Answer allowance" paragraph to the streaming spec's *Spend reservation* section, saying the gap is closed and the lock is per-process. Add the term to `GLOSSARY.md` (create it).
9. **Manual check.** `docker compose up`, set a tiny `daily_budget_usd`, fire four parallel `curl -N` requests from different `X-Forwarded-For` values, and confirm the admin usage page shows spend ≤ budget + one worst case.

Rough size: +180 lines (`allowance.py` ~130, tests ~150), −90 lines from `main.py`.

## Test plan

**New: `tests/test_allowance.py`** (the interface is the test surface):

| Case | Expectation |
|---|---|
| Open gate → start → settle(success) | One row, `generated`, real cost; `answers_remaining` decremented once |
| settle called twice | Still one row; second call ignored |
| close without settle | Slot freed; row stays `pending` at worst case; warning logged |
| Reserve insert fails (failing engine on insert only) | `start_answer` still returns `Spend`; `settle` inserts a `generated` row |
| Gate read fails | `admit` → `degraded == "global_budget"`, `answers_remaining == 0` |
| IP quota spent | `degraded == "ip_quota"`; `retrieval_only()` writes `degraded_ip` |
| Global budget spent, cache hit | `served_from_cache()` returns 0 and writes `cached` |
| Budget closes between admit and start_answer | `start_answer` → `None`; no slot held; no `pending` row |
| N threads race `start_answer` with room for k worst-case answers | Exactly k `Spend`s; spend ≤ budget |
| Per-IP slot taken | `GenerationsBusy`; admin `Caller` on same bucket succeeds |
| Global slots full, admin | `GenerationsBusy` |
| `tracked=False` | No rows, no slots; `served_from_cache`/`start_answer` work |
| Two exits on one `Admission` | `RuntimeError` |

**Replace (replace, don't layer):**

- `test_usage.py::test_pending_reservation_counts_against_quota_and_budget` and `::test_finalize_replaces_the_reservation_in_place` are covered through `Allowances` now. Delete them. Keep the `check_gate`, `estimate_*`, `worst_case_cost` and `usage_summary` tests: those stay public functions of the ledger.
- `test_streaming.py::test_slots_*` move with `GenerationSlots` into `test_allowance.py` (or stay as internal-seam tests of the slots class if it remains a separately tested part).
- `test_query_stream.py` L98–139 (cap tests) stop calling `main.generation_slots.acquire`. Either hold a slot through an overridden `get_allowances` whose slots are pre-filled, or drive the cap cases in `test_allowance.py` and keep a single HTTP test that `GenerationsBusy` → 429.
- `test_gating_flow.py::test_reservation_is_pending_while_the_answer_is_written` becomes an allowance test (`start_answer` → row is `pending`) with no fake answerer peeking at the DB.

**Keep:** the HTTP-level gating flow tests (`test_ip_quota_degrades_to_retrieval_only`, `test_global_budget_degrades_everyone`, the cache-hit tests). They describe end-to-end behaviour and should pass unchanged. That is the check that the refactor preserved behaviour.

## Risks

- **Changing when the slot is taken** changes a tested behaviour. `test_cache_hits_and_retrieval_only_need_no_slot` still holds. A new test pins down that a 429 happens after retrieval.
- **The lock on a slow DB.** If Postgres stalls, `start_answer` calls queue behind the lock. Mitigation: they would stall on the same DB anyway. Optionally use `lock.acquire(timeout=…)` and treat a timeout as `GenerationsBusy`.
- **Settings drift.** `admit` reads `s.eval_mode` while today's code reads `settings.eval_mode`. These are equal because `eval_mode` isn't overridable (`OVERRIDABLE_SETTINGS` in `config.py`), but the doc should say so, and doc 02 removes the mixed reads.

## Open questions (for grilling)

1. Take the slot after retrieval for exactness, or keep the early 429 with a two-step claim?
2. When a job never starts (`AnswerJob.start` raised), should the `pending` row be deleted, finalized as a zero-cost non-answer outcome, or left at worst case as today?
3. Should the ledger stay fail-open when reserving fails? Today a DB outage after the gate means answers go out unmetered until the outage ends.
4. Name check: "allowance" vs "answer quota" vs "spend ledger". The glossary entry should use whichever term the user already says.
5. **An abandoned answer still holds the visitor's only slot.** With `max_concurrent_generations_per_ip = 1` and detached generation (decision 7), asking again mid-stream almost always gets a 429. Doc 03 describes the bad screen this causes. One server-side fix belongs here: `admit` could let a new question from the same bucket take over a slot whose relay has been abandoned (the job keeps running, but stops counting toward the per-IP cap). That is only feasible once one module owns both the slots and the knowledge of who is still listening. It contradicts nothing in the spec, but it changes what the per-IP cap means.
