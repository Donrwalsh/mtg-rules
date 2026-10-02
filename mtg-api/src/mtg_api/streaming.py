"""Plumbing for streamed answers: SSE framing, and a worker thread whose
events can be relayed to a client (or to no one). Knows nothing about
queries."""

from __future__ import annotations

import json
import logging
import queue
import threading
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
