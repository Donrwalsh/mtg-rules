# Answer allowance

Status: design agreed, awaiting implementation plan
Date: 2026-10-02
Branch: `refactor/answer-allowance` (cut from `main` at `0c9f185`, after doc 02 merged)

## Purpose

Architecture review
[doc 01](../../architecture/2026-09-30-streaming-review/01-answer-allowance.md)
proposes one module that owns the permission to write an LLM answer: the
gate, the generation slots and the spend ledger. This spec doesn't repeat
that design. It records the decisions taken on doc 01's open questions, the
ones that followed from them, and what changed since doc 01 was written.

Unlike doc 02, this PR **changes behaviour** in two places: the daily budget
becomes exact within the process, and the slot is taken after retrieval.
Everything else is a reshaping.

## Since doc 01 was written

Doc 02 moved the pipeline into `query_pipeline.py`. Every piece doc 01
replaces now lives there: `_gate`, `_record`, `_reserve`, `_acquire_slot`,
the global `generation_slots`, `_DEGRADED_OUTCOMES`, and
`_AnswerWriter._finalize` / `.release`. `start()` reads the per-request
`s.eval_mode` everywhere, so doc 01's "settings drift" risk no longer
applies. The web client no longer lets a visitor ask again mid-stream
(`e144b79`), so the 429 that doc 01's open question 5 describes is already
gone from the UI.

## Decisions

| # | Question | Decision |
|---|---|---|
| 1 | Exact budget; when the slot is taken | **Exact.** `start_answer` re-checks the gate, takes the slot and reserves the worst case under one process-wide lock, **after retrieval**. A request that ends in 429 has paid for one retrieval. |
| 2 | Job never starts after reserving | **Settle it as `error` at zero cost.** It still counts as one answer toward the per-IP quota, as every `error` does, and as the stuck `pending` row does today. |
| 3 | Reservation insert fails | **Fail open, as today**, stated in the module docstring. The gate read just succeeded, so the window is narrow. A real DB outage already fails closed at the gate. |
| 4 | Name | **Answer allowance.** Classes `Allowances`, `Admission`, `Spend`, `GenerationsBusy`. "Ledger" stays the word for the usage rows in `usage.py`. |
| 5 | Abandoned-slot takeover | **Out of scope.** The client fix covers the visible bug. Takeover changes what the per-IP cap means. A follow-up for doc 06. |
| 6 | `Caller`, slots, lock | `admit` takes plain `ip_bucket` and `is_admin`: the allowance knows nothing about refresh rights, and `Caller` stays in `query_pipeline.py`. `GenerationSlots` moves from `streaming.py` to `allowance.py`. The slots and the lock are module-level singletons there, wrapped in an `Allowances` built per request in `main.py` and passed in `QueryDeps.allowances`. Doc 06 moves them into the lifespan. |
| 7 | Who decides `tracked` | **The allowance.** `admit(..., generate=request.generate)` works out tracked = `generate and not s.eval_mode` itself. The pipeline no longer sees `tracked`. |
| 8 | Process | This spec and a plan. **No eval run:** eval mode bypasses the allowance, so answers can't change. Verification is the unchanged HTTP gating-flow tests, plus doc 01's manual four-`curl` budget check. |
| 9 | Lock on a slow DB | `lock.acquire(timeout=5)`, a module constant, not a setting. A timeout raises `GenerationsBusy` (429). |
| 10 | Expressing decision 2 | A third method, **`Spend.cancel()`**: settles at zero cost as `error` and releases the slot. `settle`, `cancel` and `close` are idempotent, and the first ending wins. |
| 11 | One exit per `Admission` | **Enforced.** A second exit raises `RuntimeError`. One sequence is legal: `start_answer` returning `None` (the budget closed during retrieval) leaves the admission late-degraded, and then `retrieval_only()` is the only allowed call. |
| 12 | Slot-cap tests | All cap cases (per-IP, global, admin bypass) go into `test_allowance.py`, together with the `GenerationSlots` tests from `test_streaming.py`. One HTTP test that `GenerationsBusy` gives 429 with today's message. |
| 13 | Glossary | `GLOSSARY.md` at the repo root with only the allowance's terms: answer allowance, admission, spend, generation slot. Full glossary: [#24](https://github.com/Donrwalsh/mtg-rules/issues/24). |

## Interface

Doc 01's sketch, updated by the decisions above:

```python
# mtg_api/allowance.py

LOCK_TIMEOUT_S = 5.0

class GenerationsBusy(Exception): ...          # → Refused(429, <today's message>)

class Allowances:
    def __init__(self, engine, slots: GenerationSlots, lock: threading.Lock,
                 *, clock=lambda: datetime.now(UTC)): ...

    def admit(self, s: Settings, *, ip_bucket: str, is_admin: bool,
              generate: bool, model: str) -> Admission: ...

class Admission:
    degraded: Literal["ip_quota", "global_budget"] | None
    answers_remaining: int | None              # before this request uses one

    def served_from_cache(self) -> int | None   # ledger: `cached`; 0 when the global budget is spent
    def retrieval_only(self) -> int | None      # ledger: degraded_ip / degraded_global, or nothing
    def start_answer(self, prompt_chars: int) -> Spend | None   # may raise GenerationsBusy

class Spend:
    answers_remaining: int | None              # after this answer

    def settle(self, generation: Generation, *, failed: bool) -> None
    def cancel(self) -> None                   # never started: `error`, zero cost, slot freed
    def close(self) -> None                    # free the slot; unsettled keeps worst case (logged)
```

What a caller can rely on:

- **Untracked** (`generate=False` or eval mode): no gate, no slot, no ledger
  rows. `start_answer` returns a `Spend` whose methods do nothing.
- **Gated** means tracked, `s.gating_enabled` and not admin. Only gated
  admissions check the gate, at `admit` and again inside `start_answer`.
  Admins take a slot outside the per-IP count (`ip_limit=None`).
- **`answers_remaining`** is `None` unless gated. `Spend.answers_remaining`
  is one less than the admission's, never below 0.
- **Gate read fails:** treated as `global_budget` with 0 remaining
  (fail closed), in `admit` and in the re-check.
- **Reservation fails:** `start_answer` still returns a `Spend`, which
  remembers it has no row, so `settle` / `cancel` insert instead of update
  (fail open).
- **Ledger write fails:** logged and swallowed, in one helper.

`usage.py` keeps `check_gate`, `reserve_usage`, `finalize_usage`,
`record_usage`, `worst_case_cost` and `cost_usd` as the ledger's storage
functions. The allowance calls them.

## Pipeline afterwards

In `start()`:

- `admit` replaces the gate block.
- The cache hit uses `served_from_cache()` for its `answers_remaining`.
- After retrieval, `start_answer(prompt_chars(...))` runs when the request
  wants an answer and isn't degraded. `GenerationsBusy` becomes
  `Refused(429, ...)`.
- `None` from `start_answer`, or no generation at all, goes through
  `retrieval_only()`.
- The `except BaseException` block calls `spend.cancel()`.

`_AnswerWriter` takes `spend: Spend` in place of `tracked`, `reservation`,
`record` and `release`. It calls `spend.settle(...)` where `_finalize` was,
and `spend.close()` in `finally`. `record["now"]`, which the answer cache
uses, becomes a plain `now` field.

Deleted from `query_pipeline.py`: `_gate`, `_record`, `_reserve`,
`_acquire_slot`, `_DEGRADED_OUTCOMES` (moves) and `generation_slots`
(moves).

## Tests

- **New `tests/test_allowance.py`.** Doc 01's test plan table, plus:
  - `cancel` gives an `error` row at zero cost and frees the slot.
  - A lock timeout raises `GenerationsBusy`.
  - `retrieval_only` after `start_answer → None` is allowed, and any other
    second exit raises.
  - The moved `GenerationSlots` tests.
- **Replaced, not layered:**
  - `test_usage.py`'s pending and finalize tests.
  - The direct `generation_slots` use in `test_query_stream.py`.
  - The DB-peeking fake answerer in
    `test_gating_flow.py::test_reservation_is_pending_while_the_answer_is_written`.
- **Kept unchanged, as the behaviour check:** the HTTP gating-flow tests
  (IP quota, global budget, cache hits) and
  `test_cache_hits_and_retrieval_only_need_no_slot`.
- **New HTTP test:** a 429 now comes after retrieval.

## Docs

- In the streaming spec's *Spend reservation* section, add a paragraph: the
  check-to-reserve gap is closed by the answer allowance, and the lock is
  per-process. Mark the L153–156 note as superseded.
- Create `GLOSSARY.md` (decision 13).
- Mark doc 01 as done in the review README.

## Out of scope

- Slot takeover (decision 5).
- Moving the slots and the lock into the lifespan (doc 06).
- A cross-process lock: it becomes a Postgres advisory lock behind the same
  interface if the API ever runs more than one worker.
- The full glossary (#24).
