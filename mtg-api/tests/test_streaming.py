import json
import threading

from mtg_api.streaming import AnswerJob, join_all, sse_event


def test_sse_event_frames_name_and_json():
    frame = sse_event("delta", {"text": "a\nb", "at": None})
    assert frame.startswith("event: delta\ndata: ")
    assert frame.endswith("\n\n")
    # The JSON stays on one line, so a newline in the text can't split the frame.
    assert json.loads(frame.split("data: ", 1)[1]) == {"text": "a\nb", "at": None}
    assert frame.count("\n") == 3


def test_job_relays_events_in_order_then_ends():
    def work(emit):
        emit(("thinking", {}))
        emit(("done", {"answer": "x"}))

    assert list(AnswerJob(work).start().events()) == [
        ("thinking", {}),
        ("done", {"answer": "x"}),
    ]


def test_job_finishes_without_anyone_reading():
    finished = threading.Event()

    def work(emit):
        emit(("delta", {"text": "a"}))
        finished.set()

    AnswerJob(work).start()
    join_all()
    assert finished.is_set()


def test_job_that_raises_still_ends_its_events():
    def work(emit):
        emit(("thinking", {}))
        raise RuntimeError("bug")

    assert list(AnswerJob(work).start().events()) == [("thinking", {})]
