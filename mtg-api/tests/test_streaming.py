import json
import threading

from mtg_api.streaming import AnswerJob, GenerationSlots, join_all, sse_event


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


def test_slots_cap_per_ip_and_release():
    slots = GenerationSlots()
    release = slots.acquire("a", total_limit=4, ip_limit=1)
    assert release is not None
    assert slots.acquire("a", total_limit=4, ip_limit=1) is None
    assert slots.acquire("b", total_limit=4, ip_limit=1) is not None
    release()
    release()  # idempotent: doesn't free a slot it doesn't hold
    again = slots.acquire("a", total_limit=4, ip_limit=1)
    assert again is not None
    assert slots.acquire("a", total_limit=4, ip_limit=1) is None


def test_slots_cap_the_total_even_without_an_ip_limit():
    slots = GenerationSlots()
    assert slots.acquire("admin", total_limit=2, ip_limit=None) is not None
    assert slots.acquire("admin", total_limit=2, ip_limit=None) is not None
    assert slots.acquire("admin", total_limit=2, ip_limit=None) is None
    assert slots.acquire("visitor", total_limit=2, ip_limit=1) is None
