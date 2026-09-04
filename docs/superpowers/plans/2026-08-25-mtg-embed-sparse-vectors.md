# mtg-embed Sparse Vectors Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade `mtg-embed` to compute and store a BM25 sparse vector alongside the existing dense vector for every chunk, so a later stage (`mtg-api`) can do real hybrid dense+sparse Qdrant search. This is Part A of the hybrid query architecture — Part B (the `mtg-api` query logic) depends on this plan's collection schema existing.

**Architecture:** A new `SparseEmbedder` seam mirrors the existing `Embedder`'s design exactly (`Protocol`-typed model, thin wrapper, lazily-imported real factory). `QdrantStore` and `embed_and_store` both grow a second vector parameter. The collection moves from one unnamed dense vector to two named vectors (`"dense"`, `"sparse"`) — an incompatible schema change requiring the existing collection to be deleted and rebuilt from scratch, once, at the end of this plan.

**Tech Stack:** `fastembed`'s BM25 implementation (`Qdrant/bm25`) for sparse vectors; existing `sentence-transformers`/`qdrant-client` stack unchanged otherwise.

**Spec:** `docs/superpowers/specs/2026-08-25-hybrid-query-architecture-design.md` (Part A)

## Global Constraints

- Working branch: `feature/hybrid-query-architecture` (already checked out).
- The two named vectors are a fixed contract, not configurable: `"dense"` and `"sparse"` exactly — `mtg-api`'s Part B plan queries against these same two names.
- Sparse model: `fastembed`'s `SparseTextEmbedding(model_name="Qdrant/bm25")`.
- `mtg-embed`'s CLI-level behavior is unchanged from the outside — `mtg-embed run --source ... --limit ...` stays the only entry point; internally it now computes and upserts both vectors per chunk in one call instead of just dense.
- Every new/modified file stays under `mtg-worker/mtg-embed/src/mtg_embed/` or `mtg-worker/mtg-embed/tests/`.
- All work in this plan happens against `mtg-worker/mtg-embed/` — run every `pytest`/`pip install` command from that directory.

---

### Task 1: SparseEmbedder seam

**Files:**
- Create: `mtg-worker/mtg-embed/src/mtg_embed/sparse_embedder.py`
- Test: `mtg-worker/mtg-embed/tests/test_sparse_embedder.py`

**Interfaces:**
- Produces: `mtg_embed.sparse_embedder.SparseVector` (dataclass: `indices: list[int]`, `values: list[float]`); `mtg_embed.sparse_embedder.SparseEmbedder(model)` with `.encode(texts: list[str]) -> list[SparseVector]`; `mtg_embed.sparse_embedder.load_bm25_sparse_embedder(model_name: str) -> SparseEmbedder`.

- [ ] **Step 1: Write the failing tests**

```python
# mtg-worker/mtg-embed/tests/test_sparse_embedder.py
from mtg_embed.sparse_embedder import SparseEmbedder, SparseVector


class _FakeSparseEmbedding:
    def __init__(self, indices, values):
        self.indices = indices
        self.values = values


class FakeSparseModel:
    """Stands in for fastembed.SparseTextEmbedding's interface."""

    def __init__(self):
        self.calls: list[list[str]] = []

    def embed(self, texts):
        self.calls.append(list(texts))
        return [
            _FakeSparseEmbedding(indices=list(range(len(t))), values=[1.0] * len(t))
            for t in texts
        ]


def test_encode_returns_one_sparse_vector_per_text():
    embedder = SparseEmbedder(FakeSparseModel())
    vectors = embedder.encode(["ab", "c"])
    assert len(vectors) == 2
    assert isinstance(vectors[0], SparseVector)
    assert vectors[0].indices == [0, 1]
    assert vectors[0].values == [1.0, 1.0]
    assert vectors[1].indices == [0]


def test_encode_empty_list_returns_empty_list_without_calling_the_model():
    model = FakeSparseModel()
    embedder = SparseEmbedder(model)
    assert embedder.encode([]) == []
    assert model.calls == []


def test_encode_passes_texts_through_to_the_model():
    model = FakeSparseModel()
    embedder = SparseEmbedder(model)
    embedder.encode(["x", "yy"])
    assert model.calls == [["x", "yy"]]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd mtg-worker/mtg-embed && PYTHONPATH=src python -m pytest tests/test_sparse_embedder.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'mtg_embed.sparse_embedder'`)

- [ ] **Step 3: Write the implementation**

```python
# mtg-worker/mtg-embed/src/mtg_embed/sparse_embedder.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence


@dataclass
class SparseVector:
    indices: list[int]
    values: list[float]


class SparseEncoderModel(Protocol):
    def embed(self, texts: Sequence[str]) -> Sequence[object]: ...


class SparseEmbedder:
    """Thin wrapper around a fastembed-shaped sparse model.

    Takes the model as a constructor argument rather than loading it
    itself, so tests can inject a fake and never touch the real model.
    """

    def __init__(self, model: SparseEncoderModel):
        self._model = model

    def encode(self, texts: list[str]) -> list[SparseVector]:
        if not texts:
            return []
        return [
            SparseVector(indices=list(e.indices), values=list(e.values))
            for e in self._model.embed(texts)
        ]


def load_bm25_sparse_embedder(model_name: str) -> SparseEmbedder:
    """Real-model factory. Imports fastembed lazily so importing this
    module never requires that dependency unless this factory is called."""
    from fastembed import SparseTextEmbedding

    return SparseEmbedder(SparseTextEmbedding(model_name=model_name))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd mtg-worker/mtg-embed && pip install fastembed>=0.3 && PYTHONPATH=src python -m pytest tests/test_sparse_embedder.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add mtg-worker/mtg-embed/src/mtg_embed/sparse_embedder.py mtg-worker/mtg-embed/tests/test_sparse_embedder.py
git commit -m "feat(mtg-embed): add SparseEmbedder seam for BM25 sparse vectors"
```

---

### Task 2: QdrantStore hybrid (dense + sparse) vector support

**Files:**
- Modify: `mtg-worker/mtg-embed/src/mtg_embed/qdrant_store.py`
- Modify: `mtg-worker/mtg-embed/tests/test_qdrant_store.py`

**Interfaces:**
- Consumes: `mtg_embed.sparse_embedder.SparseVector` (Task 1).
- Produces: `QdrantStore.ensure_collection(dense_size: int) -> None` (renamed parameter, was `vector_size`); `QdrantStore.upsert(chunks, dense_vectors: list[list[float]], sparse_vectors: list[SparseVector]) -> None` (gained a third parameter). `existing_hashes` is unchanged.

- [ ] **Step 1: Write the failing tests**

Replace the entire contents of `mtg-worker/mtg-embed/tests/test_qdrant_store.py`:

```python
# mtg-worker/mtg-embed/tests/test_qdrant_store.py
from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

from mtg_embed.models import EmbeddableChunk
from mtg_embed.qdrant_store import QdrantStore
from mtg_embed.sparse_embedder import SparseVector


def _store() -> QdrantStore:
    # In-memory Qdrant: real client, real semantics, no network or docker.
    client = QdrantClient(location=":memory:")
    return QdrantStore(client, "test_collection")


def test_ensure_collection_is_idempotent():
    store = _store()
    store.ensure_collection(dense_size=4)
    store.ensure_collection(dense_size=4)  # must not raise on second call
    assert store.existing_hashes(["00000000-0000-0000-0000-000000000000"]) == {}


def test_upsert_then_existing_hashes_round_trips_content_hash():
    store = _store()
    store.ensure_collection(dense_size=4)

    chunk = EmbeddableChunk(
        point_id="6f6e0e2a-6f0a-4c1a-9f1a-6b0c9f6f6e2a",
        source_type="rule",
        text_to_embed="text",
        content_hash="hash-1",
        payload={"source_type": "rule", "content_hash": "hash-1", "text": "text"},
    )
    store.upsert(
        [chunk], [[0.1, 0.2, 0.3, 0.4]], [SparseVector(indices=[0, 2], values=[0.5, 0.5])]
    )

    assert store.existing_hashes([chunk.point_id]) == {chunk.point_id: "hash-1"}


def test_upsert_stores_both_dense_and_sparse_vectors():
    store = _store()
    store.ensure_collection(dense_size=4)

    chunk = EmbeddableChunk(
        point_id="6f6e0e2a-6f0a-4c1a-9f1a-6b0c9f6f6e2a",
        source_type="rule",
        text_to_embed="text",
        content_hash="hash-1",
        payload={"source_type": "rule", "content_hash": "hash-1", "text": "text"},
    )
    store.upsert(
        [chunk], [[0.1, 0.2, 0.3, 0.4]], [SparseVector(indices=[0, 2], values=[0.5, 0.75])]
    )

    points = store._client.retrieve(
        collection_name="test_collection", ids=[chunk.point_id], with_vectors=True
    )
    vectors = points[0].vector
    assert vectors["dense"] == [0.1, 0.2, 0.3, 0.4]
    assert list(vectors["sparse"].indices) == [0, 2]
    assert list(vectors["sparse"].values) == [0.5, 0.75]


def test_existing_hashes_empty_for_unknown_ids():
    store = _store()
    store.ensure_collection(dense_size=4)
    assert store.existing_hashes(["00000000-0000-0000-0000-000000000000"]) == {}


def test_existing_hashes_of_empty_list_makes_no_call():
    store = _store()
    store.ensure_collection(dense_size=4)
    assert store.existing_hashes([]) == {}


def test_existing_hashes_tolerates_missing_content_hash_key():
    """Test that points without content_hash key are safely skipped."""
    store = _store()
    store.ensure_collection(dense_size=4)

    chunk_with_hash = EmbeddableChunk(
        point_id="6f6e0e2a-6f0a-4c1a-9f1a-6b0c9f6f6e2a",
        source_type="rule",
        text_to_embed="text",
        content_hash="hash-1",
        payload={"source_type": "rule", "content_hash": "hash-1", "text": "text"},
    )
    store.upsert(
        [chunk_with_hash], [[0.1, 0.2, 0.3, 0.4]], [SparseVector(indices=[0], values=[1.0])]
    )

    # Directly insert a point via client with payload missing content_hash,
    # using the same named-vector shape ensure_collection now requires.
    point_without_hash = qmodels.PointStruct(
        id="8a8a8a8a-8a8a-8a8a-8a8a-8a8a8a8a8a8a",
        vector={
            "dense": [0.5, 0.5, 0.5, 0.5],
            "sparse": qmodels.SparseVector(indices=[0], values=[1.0]),
        },
        payload={"source_type": "rule", "text": "text"},  # no content_hash
    )
    store._client.upsert(collection_name="test_collection", points=[point_without_hash])

    result = store.existing_hashes(
        [chunk_with_hash.point_id, "8a8a8a8a-8a8a-8a8a-8a8a-8a8a8a8a8a8a"]
    )

    assert result == {chunk_with_hash.point_id: "hash-1"}
    assert "8a8a8a8a-8a8a-8a8a-8a8a-8a8a8a8a8a8a" not in result
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd mtg-worker/mtg-embed && PYTHONPATH=src python -m pytest tests/test_qdrant_store.py -v`
Expected: FAIL (`TypeError: ensure_collection() got an unexpected keyword argument 'dense_size'` and similar for `upsert`'s missing third argument)

- [ ] **Step 3: Write the implementation**

Replace the entire contents of `mtg-worker/mtg-embed/src/mtg_embed/qdrant_store.py`:

```python
# mtg-worker/mtg-embed/src/mtg_embed/qdrant_store.py
from __future__ import annotations

from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

from mtg_embed.models import EmbeddableChunk
from mtg_embed.sparse_embedder import SparseVector


class QdrantStore:
    def __init__(self, client: QdrantClient, collection_name: str):
        self._client = client
        self._collection_name = collection_name

    def ensure_collection(self, dense_size: int) -> None:
        existing = [c.name for c in self._client.get_collections().collections]
        if self._collection_name in existing:
            return
        self._client.create_collection(
            collection_name=self._collection_name,
            vectors_config={
                "dense": qmodels.VectorParams(size=dense_size, distance=qmodels.Distance.COSINE)
            },
            sparse_vectors_config={"sparse": qmodels.SparseVectorParams()},
        )

    def existing_hashes(self, point_ids: list[str]) -> dict[str, str]:
        if not point_ids:
            return {}
        points = self._client.retrieve(
            collection_name=self._collection_name,
            ids=point_ids,
            with_payload=["content_hash"],
        )
        return {
            str(p.id): p.payload["content_hash"]
            for p in points
            if p.payload and "content_hash" in p.payload
        }

    def upsert(
        self,
        chunks: list[EmbeddableChunk],
        dense_vectors: list[list[float]],
        sparse_vectors: list[SparseVector],
    ) -> None:
        if not chunks:
            return
        points = [
            qmodels.PointStruct(
                id=chunk.point_id,
                vector={
                    "dense": dense_vector,
                    "sparse": qmodels.SparseVector(
                        indices=sparse_vector.indices, values=sparse_vector.values
                    ),
                },
                payload=chunk.payload,
            )
            for chunk, dense_vector, sparse_vector in zip(chunks, dense_vectors, sparse_vectors)
        ]
        self._client.upsert(collection_name=self._collection_name, points=points)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd mtg-worker/mtg-embed && PYTHONPATH=src python -m pytest tests/test_qdrant_store.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add mtg-worker/mtg-embed/src/mtg_embed/qdrant_store.py mtg-worker/mtg-embed/tests/test_qdrant_store.py
git commit -m "feat(mtg-embed): store both dense and sparse vectors as named vectors"
```

---

### Task 3: Wire sparse embedding into embed_and_store

**Files:**
- Modify: `mtg-worker/mtg-embed/src/mtg_embed/pipeline.py`
- Modify: `mtg-worker/mtg-embed/tests/test_pipeline.py`

**Interfaces:**
- Consumes: `mtg_embed.sparse_embedder.SparseEmbedder` (Task 1); `QdrantStore.upsert`'s new 3-argument signature (Task 2).
- Produces: `embed_and_store(chunks, store, embedder, sparse_embedder, retrieve_batch_size=256) -> RunSummary` (gained a required `sparse_embedder` parameter, positioned after `embedder` and before `retrieve_batch_size`).

- [ ] **Step 1: Write the failing tests**

Replace the entire contents of `mtg-worker/mtg-embed/tests/test_pipeline.py`:

```python
# mtg-worker/mtg-embed/tests/test_pipeline.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd mtg-worker/mtg-embed && PYTHONPATH=src python -m pytest tests/test_pipeline.py -v`
Expected: FAIL (`TypeError: embed_and_store() missing 1 required positional argument: 'sparse_embedder'`)

- [ ] **Step 3: Write the implementation**

Replace the entire contents of `mtg-worker/mtg-embed/src/mtg_embed/pipeline.py`:

```python
# mtg-worker/mtg-embed/src/mtg_embed/pipeline.py
from __future__ import annotations

from dataclasses import dataclass

from mtg_embed.embedder import Embedder
from mtg_embed.models import EmbeddableChunk
from mtg_embed.qdrant_store import QdrantStore
from mtg_embed.sparse_embedder import SparseEmbedder


@dataclass
class RunSummary:
    source_type: str
    total_seen: int
    embedded: int
    skipped_unchanged: int


def _batched(items: list[EmbeddableChunk], size: int) -> list[list[EmbeddableChunk]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def embed_and_store(
    chunks: list[EmbeddableChunk],
    store: QdrantStore,
    embedder: Embedder,
    sparse_embedder: SparseEmbedder,
    retrieve_batch_size: int = 256,
) -> RunSummary:
    if not chunks:
        return RunSummary(source_type="", total_seen=0, embedded=0, skipped_unchanged=0)

    source_type = chunks[0].source_type
    embedded = 0
    skipped = 0

    for batch in _batched(chunks, retrieve_batch_size):
        existing = store.existing_hashes([c.point_id for c in batch])
        to_embed = [c for c in batch if existing.get(c.point_id) != c.content_hash]
        skipped += len(batch) - len(to_embed)

        if to_embed:
            texts = [c.text_to_embed for c in to_embed]
            dense_vectors = embedder.encode(texts)
            sparse_vectors = sparse_embedder.encode(texts)
            store.upsert(to_embed, dense_vectors, sparse_vectors)
            embedded += len(to_embed)

    return RunSummary(
        source_type=source_type,
        total_seen=len(chunks),
        embedded=embedded,
        skipped_unchanged=skipped,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd mtg-worker/mtg-embed && PYTHONPATH=src python -m pytest tests/test_pipeline.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add mtg-worker/mtg-embed/src/mtg_embed/pipeline.py mtg-worker/mtg-embed/tests/test_pipeline.py
git commit -m "feat(mtg-embed): compute and upsert sparse vectors alongside dense in embed_and_store"
```

---

### Task 4: Add oracle_id to cards and rulings payloads

**Files:**
- Modify: `mtg-worker/mtg-embed/src/mtg_embed/sources/cards.py`
- Modify: `mtg-worker/mtg-embed/src/mtg_embed/sources/rulings.py`
- Modify: `mtg-worker/mtg-embed/tests/test_sources_cards.py`
- Modify: `mtg-worker/mtg-embed/tests/test_sources_rulings.py`

**Interfaces:**
- Produces: both `load_card_chunks` and `load_ruling_chunks`'s `EmbeddableChunk.payload` dicts gain `"oracle_id"`. Signatures are otherwise unchanged.

- [ ] **Step 1: Write the failing test assertions**

In `mtg-worker/mtg-embed/tests/test_sources_cards.py`, add one line to the end of `test_card_chunk_has_no_prefix_and_joins_fields`:

```python
def test_card_chunk_has_no_prefix_and_joins_fields(tmp_path):
    path = _write_rows(tmp_path)
    chunks = {c.payload["card_name"]: c for c in load_card_chunks(path)}

    bolt = chunks["Lightning Bolt"]
    assert bolt.text_to_embed == (
        "Lightning Bolt\nInstant\n{R}\nLightning Bolt deals 3 damage to any target."
    )
    assert bolt.point_id == oracle_point_id("oid-1")
    assert bolt.source_type == "oracle"
    assert bolt.content_hash == "hcard1"
    assert bolt.payload["source_type"] == "oracle"
    assert bolt.payload["text"] == "Lightning Bolt deals 3 damage to any target."
    assert bolt.payload["oracle_id"] == "oid-1"
```

In `mtg-worker/mtg-embed/tests/test_sources_rulings.py`, add one line to the end of `test_ruling_prefix_includes_card_name_and_oracle_text_snippet`:

```python
def test_ruling_prefix_includes_card_name_and_oracle_text_snippet(tmp_path):
    rulings_path = _write(tmp_path, "rulings.jsonl", RULING_ROWS)
    cards_path = _write(tmp_path, "cards.jsonl", CARD_ROWS)

    chunks, _ = load_ruling_chunks(rulings_path, cards_path)
    first = chunks[0]

    assert first.text_to_embed == (
        "Lightning Bolt — Lightning Bolt deals 3 damage to any target.\nRuling: First ruling."
    )
    assert first.payload["card_name"] == "Lightning Bolt"
    assert first.payload["text"] == "First ruling."
    assert first.content_hash == "hr1"
    assert first.payload["oracle_id"] == "oid-1"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd mtg-worker/mtg-embed && PYTHONPATH=src python -m pytest tests/test_sources_cards.py tests/test_sources_rulings.py -v`
Expected: FAIL (`KeyError: 'oracle_id'` on the new assertion lines)

- [ ] **Step 3: Write the implementation**

In `mtg-worker/mtg-embed/src/mtg_embed/sources/cards.py`, change the `payload` dict inside `load_card_chunks` from:

```python
                payload={
                    "source_type": "oracle",
                    "content_hash": row["content_hash"],
                    "text": oracle_text,
                    "card_name": row["name"],
                },
```

to:

```python
                payload={
                    "source_type": "oracle",
                    "content_hash": row["content_hash"],
                    "text": oracle_text,
                    "card_name": row["name"],
                    "oracle_id": row["oracle_id"],
                },
```

In `mtg-worker/mtg-embed/src/mtg_embed/sources/rulings.py`, change the `payload` dict inside `load_ruling_chunks` from:

```python
                payload={
                    "source_type": "ruling",
                    "content_hash": row["content_hash"],
                    "text": row["comment"],
                    "card_name": card["name"],
                },
```

to:

```python
                payload={
                    "source_type": "ruling",
                    "content_hash": row["content_hash"],
                    "text": row["comment"],
                    "card_name": card["name"],
                    "oracle_id": oracle_id,
                },
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd mtg-worker/mtg-embed && PYTHONPATH=src python -m pytest tests/test_sources_cards.py tests/test_sources_rulings.py -v`
Expected: PASS (7 tests: 2 in cards, 5 in rulings)

- [ ] **Step 5: Commit**

```bash
git add mtg-worker/mtg-embed/src/mtg_embed/sources/cards.py mtg-worker/mtg-embed/src/mtg_embed/sources/rulings.py mtg-worker/mtg-embed/tests/test_sources_cards.py mtg-worker/mtg-embed/tests/test_sources_rulings.py
git commit -m "feat(mtg-embed): add oracle_id to card and ruling payloads for downstream dedup"
```

---

### Task 5: Config + CLI wiring

**Files:**
- Modify: `mtg-worker/mtg-embed/src/mtg_embed/config.py`
- Modify: `mtg-worker/mtg-embed/src/mtg_embed/cli.py`
- Modify: `mtg-worker/mtg-embed/tests/test_config.py`

**Interfaces:**
- Consumes: `mtg_embed.sparse_embedder.load_bm25_sparse_embedder` (Task 1); `QdrantStore.ensure_collection(dense_size)`/`.upsert(chunks, dense_vectors, sparse_vectors)` (Task 2); `embed_and_store(chunks, store, embedder, sparse_embedder, retrieve_batch_size)` (Task 3).
- Produces: `mtg_embed.config.Settings` gains `sparse_model_name: str = "Qdrant/bm25"`.

- [ ] **Step 1: Write the failing test**

Add to `mtg-worker/mtg-embed/tests/test_config.py` (append, don't replace the existing two tests):

```python
def test_settings_sparse_model_default():
    from mtg_embed.config import Settings

    s = Settings()
    assert s.sparse_model_name == "Qdrant/bm25"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd mtg-worker/mtg-embed && PYTHONPATH=src python -m pytest tests/test_config.py -v`
Expected: FAIL (`AttributeError: 'Settings' object has no attribute 'sparse_model_name'`)

- [ ] **Step 3: Write the implementation**

In `mtg-worker/mtg-embed/src/mtg_embed/config.py`, add one field to `Settings`, after `model_name`:

```python
    sparse_model_name: str = "Qdrant/bm25"
```

In `mtg-worker/mtg-embed/src/mtg_embed/cli.py`, change the imports inside `run()` from:

```python
    from mtg_embed.embedder import load_sentence_transformer_embedder
    from mtg_embed.qdrant_store import QdrantStore

    client = QdrantClient(host=settings.qdrant_host, port=settings.qdrant_port)
    store = QdrantStore(client, settings.collection_name)
    embedder = load_sentence_transformer_embedder(settings.model_name, settings.embed_batch_size)
    store.ensure_collection(embedder.vector_size)
```

to:

```python
    from mtg_embed.embedder import load_sentence_transformer_embedder
    from mtg_embed.qdrant_store import QdrantStore
    from mtg_embed.sparse_embedder import load_bm25_sparse_embedder

    client = QdrantClient(host=settings.qdrant_host, port=settings.qdrant_port)
    store = QdrantStore(client, settings.collection_name)
    embedder = load_sentence_transformer_embedder(settings.model_name, settings.embed_batch_size)
    sparse_embedder = load_bm25_sparse_embedder(settings.sparse_model_name)
    store.ensure_collection(embedder.vector_size)
```

Then update each of the three `embed_and_store(...)` call sites in the same function to pass `sparse_embedder` as the fourth positional argument, before `settings.retrieve_batch_size`. Change:

```python
        summaries.append(("rules", embed_and_store(chunks, store, embedder, settings.retrieve_batch_size)))
```

to:

```python
        summaries.append(
            ("rules", embed_and_store(chunks, store, embedder, sparse_embedder, settings.retrieve_batch_size))
        )
```

Change:

```python
        summaries.append(("cards", embed_and_store(chunks, store, embedder, settings.retrieve_batch_size)))
```

to:

```python
        summaries.append(
            ("cards", embed_and_store(chunks, store, embedder, sparse_embedder, settings.retrieve_batch_size))
        )
```

Change:

```python
        summaries.append(("rulings", embed_and_store(chunks, store, embedder, settings.retrieve_batch_size)))
```

to:

```python
        summaries.append(
            ("rulings", embed_and_store(chunks, store, embedder, sparse_embedder, settings.retrieve_batch_size))
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd mtg-worker/mtg-embed && pip install -e ".[dev]" && PYTHONPATH=src python -m pytest tests/ -v`
Expected: PASS (all tests — this also re-runs Tasks 1-4's tests as a full-suite regression check)

- [ ] **Step 5: Commit**

```bash
git add mtg-worker/mtg-embed/src/mtg_embed/config.py mtg-worker/mtg-embed/src/mtg_embed/cli.py mtg-worker/mtg-embed/tests/test_config.py
git commit -m "feat(mtg-embed): wire the sparse embedder into the run CLI command"
```

---

### Task 6: Dependency bump, collection migration, real verification

**Files:**
- Modify: `mtg-worker/mtg-embed/pyproject.toml`

**Interfaces:**
- Consumes: everything from Tasks 1-5.
- Produces: a working end-to-end hybrid-vector embed run against real Qdrant.

- [ ] **Step 1: Bump dependency floors**

In `mtg-worker/mtg-embed/pyproject.toml`, add `"fastembed>=0.3",` to the `dependencies` list, and change the existing `"qdrant-client>=1.9",` line to `"qdrant-client>=1.10",` (Part B's hybrid search needs `query_points`'s `using=` parameter, added in 1.10 — bumping the floor here now keeps both parts of this feature on the same minimum version).

- [ ] **Step 2: Reinstall and run the full test suite one more time**

Run:
```bash
cd mtg-worker/mtg-embed
pip install -e ".[dev]"
PYTHONPATH=src python -m pytest tests/ -v
```
Expected: PASS (all tests)

- [ ] **Step 3: Commit the dependency change**

```bash
git add mtg-worker/mtg-embed/pyproject.toml
git commit -m "chore(mtg-embed): add fastembed dependency, bump qdrant-client floor to 1.10"
```

- [ ] **Step 4: Delete the existing incompatible collection**

The current `mtg_rules` collection has a single unnamed dense vector from
before this plan — incompatible with the new named `"dense"`/`"sparse"`
schema, and Qdrant cannot migrate it in place. Bring up Qdrant and delete
it:

```bash
cd /path/to/mtg-rules  # repo root
docker compose up -d qdrant
curl -s -X DELETE localhost:6333/collections/mtg_rules
```
Expected: `{"result":true,"status":"ok","time":...}` (or a 404-shaped
"doesn't exist" response if it was already gone — either way, no
`mtg_rules` collection exists afterward). Confirm:
```bash
curl -s localhost:6333/collections/mtg_rules
```
Expected: a "not found" style response.

- [ ] **Step 5: Re-embed the full corpus for real**

```bash
docker compose run --rm worker --source all
```
This downloads the BM25 sparse model (small, no neural weights) and, if
not already cached in the image layer, `bge-base-en-v1.5` — can take a
few minutes on a cold cache. Watch for the printed `Embedding summary:`
block at the end confirming non-zero `embedded` counts across `rules`,
`cards`, and `rulings`.

- [ ] **Step 6: Confirm the collection has both named vectors**

```bash
curl -s localhost:6333/collections/mtg_rules | python -c "import sys,json; d=json.load(sys.stdin)['result']; print(d['config']['params']['vectors'].keys()); print(d['config']['params']['sparse_vectors'].keys()); print('points:', d['points_count'])"
```
Expected: `dict_keys(['dense'])`, `dict_keys(['sparse'])`, and a non-zero
points count.

- [ ] **Step 7: Tear down**

```bash
docker compose down
```
