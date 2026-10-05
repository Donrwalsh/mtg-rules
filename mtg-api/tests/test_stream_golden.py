"""Golden answer-stream transcripts, the contract the web client is tested
against (mtg-web/src/lib/answer-stream/golden.test.ts). A difference fails
here, on the side that changed. After a deliberate protocol change, rewrite
them with UPDATE_GOLDEN=1 pytest tests/test_stream_golden.py and update the
web client in the same PR."""

import os
import re
from pathlib import Path

import pytest
from conftest import ChunksAnswerer, CountingAnswerer, setup_trample
from fastapi.testclient import TestClient

from mtg_api.llm import StreamChunk
from mtg_api.main import app, get_answerer

GOLDEN = Path(__file__).resolve().parents[2] / "mtg-web/src/lib/answer-stream/golden"
CACHED_AT = re.compile(r'"cached_at": "[^"]*"')
FIXED_CACHED_AT = '"cached_at": "2026-01-01T00:00:00Z"'


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


def _post() -> str:
    # Transcripts must never spend: only the conftest fakes may answer.
    answerer = app.dependency_overrides[get_answerer]()
    assert isinstance(answerer, (ChunksAnswerer, CountingAnswerer)), answerer
    resp = TestClient(app).post("/api/v1/query/stream", json={"query": "trample"})
    assert resp.status_code == 200
    return resp.text


def _answered() -> str:
    chunks = [StreamChunk(text="Yes "), StreamChunk(text="[1].", finish_reason="STOP")]
    setup_trample(answerer=ChunksAnswerer(chunks))
    return _post()


def _cached() -> str:
    setup_trample()
    _post()
    return _post()


def _failed() -> str:
    setup_trample(answerer=ChunksAnswerer([], RuntimeError("Gemini 500")))
    return _post()


@pytest.mark.parametrize(
    ("name", "transcript"), [("answered", _answered), ("cached", _cached), ("failed", _failed)]
)
def test_golden_transcript(name, transcript):
    text = CACHED_AT.sub(FIXED_CACHED_AT, transcript())
    path = GOLDEN / f"{name}.sse"
    if os.environ.get("UPDATE_GOLDEN"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode())
    assert path.exists(), f"{path} is missing: run with UPDATE_GOLDEN=1"
    assert path.read_bytes().decode() == text, (
        f"{path.name} drifted from the server: rerun with UPDATE_GOLDEN=1 "
        "and update the web client to match"
    )
