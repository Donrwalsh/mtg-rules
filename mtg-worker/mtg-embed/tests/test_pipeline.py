from qdrant_client import QdrantClient

from mtg_embed.embedder import Embedder
from mtg_embed.models import EmbeddableChunk
from mtg_embed.pipeline import embed_and_store
from mtg_embed.qdrant_store import QdrantStore
from mtg_embed.sparse_embedder import SparseEmbedder


class FakeModel:
    def __init__(self, dim: int = 4):
        self._dim = dim
        self.encode_calls = 0

    def encode(self, texts, batch_size, show_progress_bar=False):
        self.encode_calls += 1
        return [[float(len(t))] * self._dim for t in texts]

    def get_sentence_embedding_dimension(self) -> int:
        return self._dim


class _FakeSparseEmbedding:
    def __init__(self, indices, values):
        self.indices = indices
        self.values = values


class FakeSparseModel:
    def __init__(self):
        self.embed_calls = 0

    def embed(self, texts):
        self.embed_calls += 1
        return [_FakeSparseEmbedding(indices=[0], values=[1.0]) for _ in texts]


def _chunk(point_id: str, content_hash: str) -> EmbeddableChunk:
    return EmbeddableChunk(
        point_id=point_id,
        source_type="rule",
        text_to_embed=f"text for {point_id}",
        content_hash=content_hash,
        payload={"source_type": "rule", "content_hash": content_hash, "text": "x"},
    )


def _fresh_store() -> QdrantStore:
    store = QdrantStore(QdrantClient(location=":memory:"), "pipeline_test")
    store.ensure_collection(dense_size=4)
    return store


def test_first_run_embeds_every_chunk():
    store = _fresh_store()
    embedder = Embedder(FakeModel(), batch_size=32)
    sparse_embedder = SparseEmbedder(FakeSparseModel())
    chunks = [
        _chunk("11111111-1111-1111-1111-111111111111", "h1"),
        _chunk("22222222-2222-2222-2222-222222222222", "h2"),
    ]

    summary = embed_and_store(chunks, store, embedder, sparse_embedder)

    assert summary.embedded == 2
    assert summary.skipped_unchanged == 0
    assert summary.total_seen == 2
    assert summary.source_type == "rule"


def test_second_run_with_unchanged_hashes_skips_everything():
    store = _fresh_store()
    embedder = Embedder(FakeModel(), batch_size=32)
    sparse_embedder = SparseEmbedder(FakeSparseModel())
    chunks = [
        _chunk("11111111-1111-1111-1111-111111111111", "h1"),
        _chunk("22222222-2222-2222-2222-222222222222", "h2"),
    ]

    embed_and_store(chunks, store, embedder, sparse_embedder)
    summary = embed_and_store(chunks, store, embedder, sparse_embedder)

    assert summary.embedded == 0
    assert summary.skipped_unchanged == 2


def test_changed_content_hash_gets_re_embedded():
    store = _fresh_store()
    embedder = Embedder(FakeModel(), batch_size=32)
    sparse_embedder = SparseEmbedder(FakeSparseModel())
    point_id = "11111111-1111-1111-1111-111111111111"

    embed_and_store([_chunk(point_id, "h1")], store, embedder, sparse_embedder)
    summary = embed_and_store([_chunk(point_id, "h1-changed")], store, embedder, sparse_embedder)

    assert summary.embedded == 1
    assert summary.skipped_unchanged == 0


def test_empty_chunk_list_returns_zeroed_summary():
    store = _fresh_store()
    embedder = Embedder(FakeModel(), batch_size=32)
    sparse_embedder = SparseEmbedder(FakeSparseModel())

    summary = embed_and_store([], store, embedder, sparse_embedder)

    assert summary.total_seen == 0
    assert summary.embedded == 0
    assert summary.skipped_unchanged == 0


def test_unchanged_chunk_is_never_sent_to_embedder():
    """Proves the idempotency requirement at the model level, not just via
    summary counts: an unchanged chunk's text must never reach the fake
    model's encode() at all on the second run."""
    store = _fresh_store()
    model = FakeModel()
    embedder = Embedder(model, batch_size=32)
    sparse_embedder = SparseEmbedder(FakeSparseModel())
    chunks = [
        _chunk("11111111-1111-1111-1111-111111111111", "h1"),
        _chunk("22222222-2222-2222-2222-222222222222", "h2"),
    ]

    embed_and_store(chunks, store, embedder, sparse_embedder)
    assert model.encode_calls == 1

    embed_and_store(chunks, store, embedder, sparse_embedder)
    assert model.encode_calls == 1


def test_only_changed_chunk_text_is_sent_to_embedder():
    """When one chunk in a batch changes and one doesn't, only the changed
    chunk's text should be passed to encode() -- proven by inspecting the
    actual texts the fake model received, not just the resulting counts."""
    store = _fresh_store()
    seen_texts: list[list[str]] = []

    class RecordingModel(FakeModel):
        def encode(self, texts, batch_size, show_progress_bar=False):
            seen_texts.append(list(texts))
            return super().encode(texts, batch_size, show_progress_bar)

    embedder = Embedder(RecordingModel(), batch_size=32)
    sparse_embedder = SparseEmbedder(FakeSparseModel())
    unchanged_id = "11111111-1111-1111-1111-111111111111"
    changed_id = "22222222-2222-2222-2222-222222222222"

    embed_and_store(
        [_chunk(unchanged_id, "h1"), _chunk(changed_id, "h2")], store, embedder, sparse_embedder
    )
    seen_texts.clear()

    summary = embed_and_store(
        [_chunk(unchanged_id, "h1"), _chunk(changed_id, "h2-changed")],
        store,
        embedder,
        sparse_embedder,
    )

    assert summary.embedded == 1
    assert summary.skipped_unchanged == 1
    assert len(seen_texts) == 1
    assert seen_texts[0] == [f"text for {changed_id}"]


def test_retrieve_batch_size_controls_batching():
    """With retrieve_batch_size=1, each chunk is looked up and embedded in
    its own batch, so encode() should be called once per changed chunk
    rather than once for the whole list."""
    store = _fresh_store()
    model = FakeModel()
    embedder = Embedder(model, batch_size=32)
    sparse_embedder = SparseEmbedder(FakeSparseModel())
    chunks = [
        _chunk("11111111-1111-1111-1111-111111111111", "h1"),
        _chunk("22222222-2222-2222-2222-222222222222", "h2"),
        _chunk("33333333-3333-3333-3333-333333333333", "h3"),
    ]

    summary = embed_and_store(chunks, store, embedder, sparse_embedder, retrieve_batch_size=1)

    assert summary.embedded == 3
    assert summary.skipped_unchanged == 0
    assert model.encode_calls == 3


def test_sparse_embedder_is_called_with_the_same_texts_as_the_dense_embedder():
    store = _fresh_store()
    sparse_model = FakeSparseModel()
    embedder = Embedder(FakeModel(), batch_size=32)
    sparse_embedder = SparseEmbedder(sparse_model)
    chunks = [_chunk("11111111-1111-1111-1111-111111111111", "h1")]

    embed_and_store(chunks, store, embedder, sparse_embedder)

    assert sparse_model.embed_calls == 1
