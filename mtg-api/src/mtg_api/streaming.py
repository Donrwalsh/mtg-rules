"""Plumbing for streamed answers: SSE framing, a worker thread whose events
can be relayed to a client (or to no one), and caps on concurrent
generations. Knows nothing about queries."""

from __future__ import annotations

import json
import logging
import queue
import threading
from collections import Counter
from collections.abc import Callable, Iterator

from fastapi.encoders import jsonable_encoder

logger = logging.getLogger(__name__)

Emit = Callable[[object], None]

_END = object()
_live: set[threading.Thread] = set()
_live_lock = threading.Lock()


def sse_event(name: str, data: dict) -> str:
    # json.dumps escapes newlines, so the data is always one line.
    return f"event: {name}\ndata: {json.dumps(jsonable_encoder(data))}\n\n"


class AnswerJob:
    """Runs `work(emit)` on its own thread. Events go into a queue that
    `events()` relays until the work returns. The work never depends on
    anyone reading: a client that leaves only stops the relay."""

    def __init__(self, work: Callable[[Emit], None]):
        self._work = work
        self._queue: queue.Queue = queue.Queue()
        self._thread = threading.Thread(target=self._run, name="answer-job", daemon=True)

    def start(self) -> AnswerJob:
        with _live_lock:
            _live.add(self._thread)
        self._thread.start()
        return self

    def _run(self) -> None:
        try:
            self._work(self._queue.put)
        except Exception:
            logger.exception("Answer job failed")
        finally:
            self._queue.put(_END)
            with _live_lock:
                _live.discard(self._thread)

    def events(self) -> Iterator[object]:
        while (item := self._queue.get()) is not _END:
            yield item


def join_all(timeout: float = 5.0) -> None:
    """Wait for every running job. For tests."""
    with _live_lock:
        threads = list(_live)
    for thread in threads:
        thread.join(timeout)


class GenerationSlots:
    """How many answers are being written, in total and per IP bucket. In
    memory: the API runs as one process."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._total = 0
        self._by_bucket: Counter[str] = Counter()

    def acquire(
        self, bucket: str, *, total_limit: int, ip_limit: int | None
    ) -> Callable[[], None] | None:
        """A release function, or None when a cap is full. ip_limit=None
        leaves this request out of the per-IP count."""
        with self._lock:
            if self._total >= total_limit:
                return None
            if ip_limit is not None and self._by_bucket[bucket] >= ip_limit:
                return None
            self._total += 1
            if ip_limit is not None:
                self._by_bucket[bucket] += 1

        released = False

        def release() -> None:
            nonlocal released
            with self._lock:
                if released:
                    return
                released = True
                self._total -= 1
                if ip_limit is not None:
                    self._by_bucket[bucket] -= 1
                    if not self._by_bucket[bucket]:
                        del self._by_bucket[bucket]

        return release
