# Architecture review: streaming answers (2026-09-30)

A review of the hottest area of the codebase on `feature/streaming-answers`: the query pipeline and the streamed answer, on both the API and the web side. [`architecture-review.html`](architecture-review.html) is the original visual review: open it in a browser, since it loads Tailwind and Mermaid from CDNs. Each candidate it lists has a detailed write-up here, part spec and part implementation plan.

The vocabulary is the `codebase-design` skill's: **module**, **interface**, **implementation**, **depth**, **seam**, **adapter**, **leverage**, **locality**.

| # | Write-up | Strength | Touches |
|---|---|---|---|
| 1 | [Deepen the answer allowance](01-answer-allowance.md) | Strong | `main.py`, `usage.py`, `streaming.py` |
| 2 | [Pull the query pipeline out of `main.py`](02-query-pipeline.md) | Strong | `main.py`, `retrieval.py`, `models.py` |
| 3 | [Deepen the answer stream on the web side](03-web-answer-stream.md) | Strong | `+page.svelte`, `api.ts`, `stream.ts`, fixtures |
| 4 | [One owner for citation numbering and marker grammar](04-citations.md) | Worth exploring | `citations.py`, `llm.py`, `enrich.py`, `stream.ts`, `segments.ts` |
| 5 | [Make the answerer seam real](05-answerer-seam.md) | Worth exploring | `llm.py`, `usage.py`, test fakes |
| 6 | [An app-scoped answer runner](06-answer-runner.md) | Speculative → see doc | `streaming.py`, lifespan, prod compose |

## Found while writing these up

The HTML review doesn't mention these; each one is covered in full in its doc.

- **Doc 3: a user-visible bug.** Asking again while an answer streams almost always gets a 429 in production, because the per-IP generation cap is 1 and the abandoned answer keeps its slot. The page then shows "Couldn't write an answer this time" for the *first* question, whose answer is actually being written successfully. Fixtures don't model slots, so Playwright doesn't catch it.
- **Doc 6: graceful shutdown probably never happens.** The prod command is `sh -c "alembic … && uvicorn …"`, so `sh` is PID 1 and doesn't pass SIGTERM on to uvicorn. Every deploy SIGKILLs in-flight answers after 10 s and leaves their reservations `pending` at worst-case cost against the visitors' daily quota. Adding `exec` before `uvicorn` is a one-word fix worth doing on its own.
- **Doc 1: the budget gap is a known, accepted trade-off** (spec L153–156), not a bug. Doc 1's case rests on the ten ordering rules callers must keep, and closing the gap is a cheap side benefit.
- **Doc 4: the client has a third copy of the marker grammar** (`segments.ts`), and the streamed view and final view disagree on malformed markers like `[1-3]`.

## Suggested order

1. The `exec` fix from doc 6, on its own: one line, no design needed.
2. Doc 2, then doc 1 inside it. They share `_start_query`, and doc 2 gives doc 1 a home and a test surface that doesn't go through HTTP.
3. Doc 3, which is independent of the backend work and fixes the 429 bug. Its open question 1 is a product decision to settle first.
4. Docs 4 and 5, in either order. Doc 5's shared test fakes make docs 1 and 2 cheaper to test, so pulling it forward is reasonable.
5. The rest of doc 6, once doc 1 has settled where the slots live.

Every doc ends with **open questions**: decisions to settle (in a grilling session) before writing code.
