from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool

from mtg_api.history import metadata as history_metadata
from mtg_api.llm import Generation

# Side-effect imports to register tables on metadata
_answer_cache = __import__("mtg_api.answer_cache", fromlist=[""])
_usage = __import__("mtg_api.usage", fromlist=[""])


def memory_engine() -> Engine:
    """A fresh in-memory SQLite engine with the query_history schema created.

    Uses StaticPool + check_same_thread=False because FastAPI runs sync path
    operations in a worker thread pool -- the default SQLite :memory: pooling
    ties a connection to the thread that created it, which would hand a
    request a different, schema-less database than the one a test set up on
    the main thread.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    history_metadata.create_all(engine)
    return engine


def admin_client(monkeypatch, password: str = "pw"):
    """A TestClient logged in as the admin, sending the X-Admin-Request
    marker on every request."""
    from fastapi.testclient import TestClient
    from pydantic import SecretStr

    from mtg_api.config import settings
    from mtg_api.main import app

    monkeypatch.setattr(settings, "admin_password", SecretStr(password))
    client = TestClient(app, headers={"X-Admin-Request": "1"})
    assert client.post("/api/v1/auth/login", json={"password": password}).status_code == 200
    return client


class StreamsFromGenerate:
    """For fake answerers that define generate(): serves it as a one-chunk
    stream, the way the pipeline reads answers. Raises on first read, as a
    real failed stream does."""

    def stream(self, query, context):
        from mtg_api.llm import StreamChunk

        g = self.generate(query, context)
        yield StreamChunk(
            text=g.text,
            input_tokens=g.input_tokens,
            output_tokens=g.output_tokens,
            thinking_tokens=g.thinking_tokens,
            finish_reason=g.finish_reason,
        )


class FakeDenseModel:
    def encode(self, texts, batch_size, show_progress_bar=False):
        return [[0.1, 0.2, 0.3, 0.4] for _ in texts]

    def get_sentence_embedding_dimension(self):
        return 4


class FakeSparseEmbedding:
    indices = (0,)
    values = (1.0,)


class FakeSparseModel:
    def embed(self, texts):
        return [FakeSparseEmbedding() for _ in texts]


class FakeHit:
    def __init__(self, id, score, payload):
        self.id = id
        self.score = score
        self.payload = payload


class FakeQueryResult:
    def __init__(self, points):
        self.points = points


class FakeQdrantClient:
    def __init__(self, dense_points=None, sparse_points=None, scroll_points=None):
        self._dense_points = dense_points or []
        self._sparse_points = sparse_points or []
        self._scroll_points = scroll_points or []

    def query_points(self, collection_name, using, query, limit, with_payload, query_filter=None):
        points = self._dense_points if using == "dense" else self._sparse_points
        if query_filter is not None:
            (condition,) = query_filter.must
            points = [p for p in points if p.payload.get(condition.key) == condition.match.value]
        return FakeQueryResult(points[:limit])

    def scroll(self, collection_name, scroll_filter, limit, with_payload):
        return self._scroll_points[:limit], None


class FakeAnswerer(StreamsFromGenerate):
    def __init__(self, answer="A generated answer.", raises=None):
        self._answer = answer
        self._raises = raises

    def generate(self, query, context):
        if self._raises:
            raise self._raises
        return Generation(
            text=self._answer,
            input_tokens=1000,
            output_tokens=100,
            thinking_tokens=200,
            finish_reason="STOP",
        )


class CountingAnswerer(StreamsFromGenerate):
    def __init__(self, raises=None, finish_reason="STOP"):
        self.calls = 0
        self._raises = raises
        self._finish_reason = finish_reason

    def generate(self, query, context):
        self.calls += 1
        if self._raises:
            raise self._raises
        return Generation(
            text="Yes [1].",
            input_tokens=1_000_000,
            output_tokens=0,
            thinking_tokens=0,
            finish_reason=self._finish_reason,
        )


class ChunksAnswerer:
    """Streams the given chunks, then raises `then_raise` if set."""

    def __init__(self, chunks, then_raise=None):
        self._chunks = chunks
        self._then_raise = then_raise
        self.calls = 0

    def stream(self, query, context):
        self.calls += 1
        yield from self._chunks
        if self._then_raise:
            raise self._then_raise


class FailingEngine:
    def begin(self):
        raise RuntimeError("db unreachable")

    def connect(self):
        raise RuntimeError("db unreachable")


def trample_hits():
    return [FakeHit("p1", 1.0, {"source_type": "rule", "rule_id": "702.19b", "text": "T."})]


def override(
    cards=None,
    dense_points=None,
    sparse_points=None,
    scroll_points=None,
    answerer=None,
    engine=None,
    rules=None,
):
    """Point the app's dependencies at fakes. Tests clear
    app.dependency_overrides afterwards."""
    from mtg_api.card_matcher import CardMatcher
    from mtg_api.embedder import Embedder
    from mtg_api.keyword_matcher import KeywordMatcher
    from mtg_api.main import (
        app,
        get_answerer,
        get_card_matcher,
        get_db_engine,
        get_dense_embedder,
        get_keyword_matcher,
        get_qdrant_client,
        get_rules_index,
        get_sparse_embedder,
    )
    from mtg_api.rules_index import RulesIndex
    from mtg_api.sparse_embedder import SparseEmbedder

    app.dependency_overrides[get_card_matcher] = lambda: CardMatcher(cards or [])
    app.dependency_overrides[get_keyword_matcher] = lambda: KeywordMatcher(rules or [])
    app.dependency_overrides[get_rules_index] = lambda: RulesIndex(rules or [])
    app.dependency_overrides[get_dense_embedder] = lambda: Embedder(FakeDenseModel())
    app.dependency_overrides[get_sparse_embedder] = lambda: SparseEmbedder(FakeSparseModel())
    app.dependency_overrides[get_qdrant_client] = lambda: FakeQdrantClient(
        dense_points, sparse_points, scroll_points
    )
    app.dependency_overrides[get_answerer] = lambda: answerer or FakeAnswerer()
    app.dependency_overrides[get_db_engine] = lambda: engine or memory_engine()


def setup_trample(answerer=None, engine=None, data_version="v1"):
    """The app answering from one trample rule hit."""
    from mtg_api.main import app, get_data_version

    engine = engine or memory_engine()
    answerer = answerer or CountingAnswerer()
    override(dense_points=trample_hits(), answerer=answerer, engine=engine)
    app.dependency_overrides[get_data_version] = lambda: data_version
    return engine, answerer


def make_deps(*, hits=None, answerer=None, engine=None, cards=None, rules=None, data_version="v1"):
    """QueryDeps built directly, with no FastAPI involved: one trample rule
    hit unless `hits` says otherwise."""
    from mtg_api.card_matcher import CardMatcher
    from mtg_api.embedder import Embedder
    from mtg_api.keyword_matcher import KeywordMatcher
    from mtg_api.main import QueryDeps
    from mtg_api.rules_index import RulesIndex
    from mtg_api.sparse_embedder import SparseEmbedder

    return QueryDeps(
        matcher=CardMatcher(cards or []),
        keyword_matcher=KeywordMatcher(rules or []),
        dense_embedder=Embedder(FakeDenseModel()),
        sparse_embedder=SparseEmbedder(FakeSparseModel()),
        client=FakeQdrantClient(trample_hits() if hits is None else hits),
        answerer=answerer or CountingAnswerer(),
        engine=engine or memory_engine(),
        rules_index=RulesIndex(rules or []),
        data_version=data_version,
    )
