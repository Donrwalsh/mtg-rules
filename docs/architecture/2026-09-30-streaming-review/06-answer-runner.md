# 6 · An app-scoped answer runner

> Architecture review, 2026-09-30, branch `feature/streaming-answers`.
> Strength: **Speculative** (refined below: the shutdown finding raises it towards *Worth exploring*) · Dependency category: **in-process**
> Companion docs: [01 answer allowance](01-answer-allowance.md) (owns the slots; this owns the threads), [02 query pipeline](02-query-pipeline.md) (starts jobs through this), [03 web answer stream](03-web-answer-stream.md) (the ask-again 429 this could help fix).

## Summary

`streaming.py` runs each answer on a daemon thread (`AnswerJob`). It keeps every live thread in a **module-level** set (`_live`) whose only reader is `join_all()`, whose docstring says "For tests". Nothing in production knows how many answers are running, waits for them at shutdown, or knows whether anyone is still reading a given answer.

This doc proposes an `AnswerRunner`: one instance per app, provided through the same `lru_cache` dependency pattern as the other singletons. It starts jobs, tracks them, knows which ones still have a listener, and **drains** them on shutdown. It does not own the generation slots; doc 01's allowance does. The first review card bundled the two together; this write-up separates them.

The review rated this card *Speculative*. Reading the deployment config turned up a concrete cost, so the case is stronger than the card suggested. It's still smaller than items 1–3.

## The finding: every deploy can strand answers

- Production runs `sh -c "alembic upgrade head && uvicorn mtg_api.main:app ... --workers 1 ..."` (`docker-compose.prod.yml:97–100`; the Dockerfile `CMD` uses the same shell form). That makes **`sh` PID 1 and uvicorn its child**. A shell running as PID 1 doesn't pass SIGTERM on to its child, and `&&` stops the shell from `exec`ing the last command. So uvicorn most likely **never sees SIGTERM**: Docker waits `stop_grace_period` (unset, so **10 seconds**) and then SIGKILLs everything. No graceful shutdown happens at all, not even for connected streams. *Verify:* `time docker compose -f docker-compose.prod.yml stop backend` should take about 10 s, and the logs should have no "Shutting down" line from uvicorn.
- An answer can run for up to `gemini_timeout_seconds = 60` (`config.py:37`).
- On SIGTERM, uvicorn stops accepting connections and waits for open responses. A **detached** job, whose visitor closed the tab or asked again, has no open response, so uvicorn doesn't wait for it. The lifespan's shutdown phase is empty (`main.py:198`, nothing after `yield`). When the interpreter exits, daemon threads are killed mid-answer.

What a killed job leaves behind:

| Effect | Why |
|---|---|
| A `pending` usage row at **worst-case cost**, for good | `reserve_usage` docstring: "A row never finalized keeps that cost." |
| The visitor loses one of today's answers and gets none back | `pending` is in `ANSWER_OUTCOMES`, so it counts against the IP quota |
| The global budget is overstated for the rest of the UTC day | `spend_since` sums `pending` at worst case |
| No history row, no cache entry | both happen after generation, on the killed thread |
| A visitor still connected sees a broken stream | after 10 s, SIGKILL closes the socket |

The spec chose "a stranded `pending` keeps its worst-case cost" as the safe failure for a *crash*. A routine redeploy shouldn't count as a crash. On a small hobby deployment that redeploys often (Coolify, every push), this hits real visitors.

## Why do this

**Right away**

- **Deploys stop costing visitors answers.** Drain running answers for up to ~65 s at shutdown, and give the container long enough to do it.
- **Tests stop sharing global state.** `join_all()` waits on *every* job from *every* test. A per-test runner isolates them and makes `test_query_stream.py::test_closing_the_stream_early_still_finishes_the_answer` deterministic about which jobs it waits for.

**Long term**

- **Knowing who's listening opens up options.** Once the runner tracks whether a job's relay is still attached, doc 01's open question 5 (let a new question from the same visitor take over the slot of an abandoned generation) and doc 03's ask-again 429 can be solved on the server.
- **Operations visibility.** Add `running`/`detached` counts to `/health` or the admin usage page, which helps when tuning `max_concurrent_generations` on the 2-vCPU box.
- **A place for future lifecycle policy:** cancelling a detached job early to save tokens, which would mean revisiting decision 7, or bounding the event queue.

## Current state

```python
# streaming.py
_END = object()
_live: set[threading.Thread] = set()        # module global
_live_lock = threading.Lock()

class AnswerJob:
    def __init__(self, work):
        self._queue = queue.Queue()          # unbounded
        self._thread = threading.Thread(target=self._run, name="answer-job", daemon=True)
    def start(self):
        with _live_lock: _live.add(self._thread)
        self._thread.start(); return self
    def _run(self):
        try: self._work(lambda name, data: self._queue.put((name, data)))
        except Exception: logger.exception("Answer job failed")
        finally:
            self._queue.put(_END)
            with _live_lock: _live.discard(self._thread)
    def events(self):
        while (item := self._queue.get()) is not _END: yield item

def join_all(timeout=5.0):                   # "For tests."
    ...
```

```python
# main.py:184
@asynccontextmanager
async def lifespan(app):
    ...warm-up...
    yield                                    # no shutdown work
```

```python
# main.py:753
def _sse(started):
    yield sse_event("results", started.head)
    for name, data in started.rest:          # when the client leaves, Starlette closes this
        yield sse_event(name, data)          # generator; nobody records that it happened
```

### A smaller ordering issue

`_run_answer` emits `done` (L571) and only then releases the slot in `finally` (L573). The client can receive `done` before the slot is free. Over a network the window is microseconds, so it doesn't matter in practice. In the test suite, though, `TestClient` posts back-to-back from one bucket with a per-IP cap of 1 (e.g. `test_gating_flow.py::test_new_data_version_misses_the_cache` makes two generating posts in a row). That relies on the GIL letting the worker reach `release()` before the next request reaches `acquire()`. It's a latent flake. The runner (or doc 01's `Spend.close()`) should free the slot **before** emitting `done`.

## Proposed design

### Interface

```python
# mtg_api/streaming.py  (sketch; stays query-agnostic)

class AnswerJob:
    def events(self) -> Iterator[Event]:
        """Relay the job's events. Closing this iterator (the client left)
        marks the job detached; the work carries on."""
    @property
    def detached(self) -> bool: ...


class AnswerRunner:
    """Every answer job in this process: starts them, knows which still have
    a listener, and lets them finish at shutdown."""

    def start(self, work: Callable[[Emit], None], *, owner: str | None = None) -> AnswerJob:
        """Run `work` on its own thread. `owner` (an IP bucket) lets callers
        ask about a visitor's abandoned jobs."""

    def abandoned_by(self, owner: str) -> list[AnswerJob]:
        """Jobs still running for `owner` whose relay has been closed."""

    def drain(self, timeout: float) -> int:
        """Wait up to `timeout` for running jobs to finish. Returns how many
        are still running (and logs them). Called once, at shutdown."""

    @property
    def running(self) -> int: ...
```

Providing it, following the existing singleton pattern:

```python
@lru_cache(maxsize=1)
def get_answer_runner() -> AnswerRunner:
    return AnswerRunner()

@asynccontextmanager
async def lifespan(app):
    ...warm-up...
    yield
    left = await anyio.to_thread.run_sync(get_answer_runner().drain, settings.answer_drain_seconds)
    if left:
        logger.warning("%d answers still running at shutdown; their reservations stay pending", left)
```

Tests override `get_answer_runner` with a fresh runner and call `runner.drain(5)` where they call `join_all()` today. Most tests use `TestClient(app)` without `with`, so the lifespan, which loads the models, doesn't run, which is why the runner has to come from a dependency rather than being created in the lifespan.

### Detached tracking

```python
def sse_frames(stream: AnswerStream, job: AnswerJob):      # doc 02's adapter
    try:
        yield ...
    finally:
        job.detach()                                       # GeneratorExit when the client leaves
```

Or `AnswerJob.events()` does it itself in a `try/finally` around its loop. That version is better, because it's then true for every relay, including the blocking endpoint's `collect`.

### Deployment changes (part of the same item)

```yaml
# docker-compose.prod.yml, backend service
stop_grace_period: 75s
command: >-
  sh -c "alembic upgrade head &&
  exec uvicorn mtg_api.main:app --host 0.0.0.0 --port 8000 --workers 1
  --proxy-headers --forwarded-allow-ips='*' --timeout-graceful-shutdown 70"
```

The `exec` matters most: it replaces the shell, so uvicorn becomes PID 1 and receives SIGTERM. Without it, none of the rest has any effect. Make the same change to the Dockerfile `CMD` (`... && exec uvicorn ...`) for the dev compose. That one-word fix is worth making even if the rest of this item is never done, because it also gives connected streams a graceful close.

```python
# config.py
answer_drain_seconds: float = 65.0     # > gemini_timeout_seconds; < uvicorn's graceful timeout
```

The budget: uvicorn closes connections at 70 s, the lifespan drain gets 65 s, and Docker SIGKILLs at 75 s. During a rolling deploy Coolify starts the new container first, so a slow drain doesn't mean downtime. **Verify that Coolify respects `stop_grace_period`** (open question 1).

## Implementation plan

1. **Runner, tests first.** In `test_streaming.py`, add: start and drain; drain times out and reports how many are left; `running` counts; a job is marked detached after its relay closes; `abandoned_by(owner)`. Implement `AnswerRunner` in `streaming.py`. Delete `_live`, `_live_lock` and `join_all`.
2. **Dependency.** Add `get_answer_runner` in `main.py` and put it on `QueryDeps` (or doc 02's pipeline deps). Start jobs through `deps.runner.start(..., owner=bucket)`.
3. **Detach on relay close.** Add `try/finally` in `AnswerJob.events()`.
4. **Release before done.** In `_run_answer` (or doc 01's `Spend`), free the slot right after settling usage, before history, cache and `done`. Add a test: after reading `done`, the slot is already free (`GenerationSlots` count is 0, no thread join needed).
5. **Lifespan drain.** Add `answer_drain_seconds` to `Settings`. Call drain in the lifespan teardown. Test: a `TestClient` used as a context manager (`with TestClient(app)`) with the warm-up getters overridden, and a job still running at exit, gets finalized before exit. This needs `lifespan`'s warm-up calls to go through overridable getters. They already are `lru_cache` functions; patch them in the test.
6. **Deploy config.** Add `exec` before `uvicorn` in the prod compose command and the Dockerfile `CMD`. Then add `stop_grace_period` and `--timeout-graceful-shutdown` to `docker-compose.prod.yml`. Note it in the deploy README / `deploy/`. The `exec` change can ship first, on its own, as a one-line fix.
7. **(Optional, with doc 01/03) Take over abandoned slots.** Pass `deps.runner.abandoned_by(bucket)` to `admit`. A per-IP slot held only by abandoned jobs doesn't block a new question. This is a product decision; see doc 03 open question 1.
8. **Verify.** `uv run pytest`. Then, on the real stack: start a long answer (`?mock` doesn't apply, so use a real question with `generation_thinking_level=high`), close the tab, run `docker compose -f docker-compose.prod.yml restart backend`, and confirm the usage row ends as `generated` rather than `pending` and a history row exists.

Rough size: `streaming.py` +50/−20, `main.py` +15, config +2, compose +3, tests +80/−10.

## Test plan

| Test | Level |
|---|---|
| drain waits for running jobs; returns 0 | runner |
| drain past timeout returns N and logs | runner |
| closing `events()` marks the job detached; the work still finishes | runner |
| `abandoned_by` lists only detached, running jobs for that owner | runner |
| slot is free when `done` is read | pipeline (doc 02) |
| lifespan teardown finalizes a running answer | app (one test, lifespan with overridden warm-up) |
| per-test runner: no cross-test waits | migrate `join_all()` call sites (2) |

## Risks

- **Shutdown takes longer.** Up to 75 s per deploy in the worst case. In practice answers take 3–15 s, so the drain is usually short.
- **Drain from async code.** The lifespan is async, and `drain` blocks on `thread.join`. Run it through `anyio.to_thread.run_sync` (shown above) so it doesn't block the event loop while uvicorn closes connections.
- **Coolify/Traefik behaviour** on container stop isn't verified here. If Coolify kills the container immediately, step 6 does nothing. The drain is still correct, just useless.
- **The PID 1 claim is inferred from the command's shape, not observed.** Run the `time docker compose stop` check before relying on it. Use the `exec` form either way; it's never wrong.
- **Scope creep.** "Take over abandoned slots" changes what the per-IP cap means. Keep it a separate, explicit decision.

## Open questions (for grilling)

1. Does Coolify (v4) honour `stop_grace_period` from the compose file, and does it start the new container before stopping the old one?
2. Should a detached job be **cancelled** instead of finished (saving tokens and the visitor's quota) when nobody is reading? That reverses decision 7 ("the server reads Gemini to the end and records, caches and saves history"). Decision 7 had good reasons, the cache and history among them, so only reopen it if quota complaints show up.
3. Should the runner's counts be exposed (`/health`, or the admin usage page)?
4. Is the shutdown cost real enough to promote this item from *Speculative* to *Worth exploring*? The answer depends on how often production redeploys.
