# mtg-api Query Architecture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `mtg-api`'s dummy `POST /api/v1/query` with real retrieval: Aho-Corasick card-name detection plus hybrid (dense + sparse) Qdrant search, deduplicated into one unified result list. This is Part B of the hybrid query architecture — depends on Part A's plan (`docs/superpowers/plans/2026-08-25-mtg-embed-sparse-vectors.md`) already having run, since it queries the `"dense"`/`"sparse"` named-vector collection schema that plan creates.

**Architecture:** All new code lives directly inside `mtg-api` (per explicit decision — no new package, no import from `mtg_embed`/`mtg_worker`). `mtg-api` gets its own `Embedder`/`SparseEmbedder` seams (small, deliberate duplication of `mtg-embed`'s — same no-cross-package-import convention already used elsewhere in this repo), a `CardMatcher` built from the newest `cards_*.jsonl`, and a `hybrid_search` function doing manual weighted score fusion (not Qdrant's built-in RRF/DBSF, which don't accept literal tunable weights). Heavy objects (both models, the card automaton) are built once per process via `lru_cache`-wrapped FastAPI dependency providers — lazy, and fully overridable in tests via the same `dependency_overrides` pattern already used for `get_qdrant_client`/`get_celery_client`.

**Tech Stack:** `pyahocorasick` for card matching; `sentence-transformers` (`bge-base-en-v1.5`) for the dense query vector; `fastembed` (`Qdrant/bm25`) for the sparse query vector; `qdrant-client>=1.10`'s `query_points(using=...)` for named-vector search.

**Spec:** `docs/superpowers/specs/2026-08-25-hybrid-query-architecture-design.md` (Part B)

## Global Constraints

- Working branch: `feature/hybrid-query-architecture` (already checked out — same branch as Part A's plan; run this plan's tasks after Part A's are complete).
- Named vectors are a fixed contract with Part A: query the collection's `"dense"` and `"sparse"` vectors by those exact names.
- `mtg-api` never imports `mtg_embed` or `mtg_worker` — any type or helper it needs from those packages' shape gets its own small copy inside `mtg-api` instead.
- All new heavy-object construction (both models, the card automaton) is lazy — nothing loads until a real request needs it, and every one of these lives behind a `lru_cache`-wrapped provider function that tests can override via `app.dependency_overrides`.
- `QueryResult.source` values: `"rule"`, `"ruling"`, `"oracle"` (existing, from Qdrant payloads) plus the new `"card"` (exact name matches). `match_type` is `"card_name_match"` or `"vector_hit"`.
- No per-request override of the hybrid weights/limits/threshold — process-wide config only (`MTG_API_HYBRID_*` env vars).
- Every new Python file lives under `mtg-api/src/mtg_api/`; tests under `mtg-api/tests/`. Run every `pytest`/`pip install` command from `mtg-api/`.

---

### Task 1: mtg-api's own Embedder and SparseEmbedder seams

**Files:**
- Create: `mtg-api/src/mtg_api/embedder.py`
- Create: `mtg-api/src/mtg_api/sparse_embedder.py`
- Test: `mtg-api/tests/test_embedder.py`
- Test: `mtg-api/tests/test_sparse_embedder.py`

**Interfaces:**
- Produces: `mtg_api.embedder.Embedder(model, batch_size=32)` with `.vector_size` and `.encode(texts) -> list[list[float]]`; `mtg_api.embedder.load_sentence_transformer_embedder(model_name, batch_size=1) -> Embedder`. `mtg_api.sparse_embedder.SparseVector` (dataclass: `indices: list[int]`, `values: list[float]`); `mtg_api.sparse_embedder.SparseEmbedder(model)` with `.encode(texts) -> list[SparseVector]`; `mtg_api.sparse_embedder.load_bm25_sparse_embedder(model_name) -> SparseEmbedder`.

- [ ] **Step 1: Write the failing tests**

```python
# mtg-api/tests/test_embedder.py
from mtg_api.embedder import Embedder


class FakeModel:
    def __init__(self, dim: int = 4):
        self._dim = dim
        self.calls: list[tuple[int, int]] = []

    def encode(self, texts, batch_size, show_progress_bar=False):
        self.calls.append((len(texts), batch_size))
        return [[float(len(t))] * self._dim for t in texts]

    def get_sentence_embedding_dimension(self) -> int:
        return self._dim


def test_vector_size_comes_from_the_model():
    embedder = Embedder(FakeModel(dim=768), batch_size=1)
    assert embedder.vector_size == 768


def test_encode_returns_one_vector_per_text():
    embedder = Embedder(FakeModel(dim=4), batch_size=1)
    vectors = embedder.encode(["a", "bb"])
    assert len(vectors) == 2
    assert len(vectors[0]) == 4


def test_encode_empty_list_returns_empty_list_without_calling_the_model():
    model = FakeModel()
    embedder = Embedder(model, batch_size=1)
    assert embedder.encode([]) == []
    assert model.calls == []
```

```python
# mtg-api/tests/test_sparse_embedder.py
from mtg_api.sparse_embedder import SparseEmbedder, SparseVector


class _FakeSparseEmbedding:
    def __init__(self, indices, values):
        self.indices = indices
        self.values = values


class FakeSparseModel:
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
    assert vectors[1].indices == [0]


def test_encode_empty_list_returns_empty_list_without_calling_the_model():
    model = FakeSparseModel()
    embedder = SparseEmbedder(model)
    assert embedder.encode([]) == []
    assert model.calls == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd mtg-api && python -m pytest tests/test_embedder.py tests/test_sparse_embedder.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'mtg_api.embedder'` and `'mtg_api.sparse_embedder'`)

- [ ] **Step 3: Write the implementation**

```python
# mtg-api/src/mtg_api/embedder.py
from __future__ import annotations

from typing import Protocol, Sequence


class EncoderModel(Protocol):
    def encode(
        self, texts: Sequence[str], batch_size: int, show_progress_bar: bool
    ) -> list[list[float]]: ...

    def get_sentence_embedding_dimension(self) -> int: ...


class Embedder:
    def __init__(self, model: EncoderModel, batch_size: int = 32):
        self._model = model
        self._batch_size = batch_size

    @property
    def vector_size(self) -> int:
        return self._model.get_sentence_embedding_dimension()

    def encode(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return self._model.encode(texts, batch_size=self._batch_size, show_progress_bar=False)


def load_sentence_transformer_embedder(model_name: str, batch_size: int = 1) -> Embedder:
    """Real-model factory. Imports sentence_transformers lazily so importing
    this module never requires that heavy dependency unless this factory
    is actually called."""
    from sentence_transformers import SentenceTransformer

    return Embedder(SentenceTransformer(model_name), batch_size=batch_size)
```

```python
# mtg-api/src/mtg_api/sparse_embedder.py
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

Run: `cd mtg-api && python -m pytest tests/test_embedder.py tests/test_sparse_embedder.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add mtg-api/src/mtg_api/embedder.py mtg-api/src/mtg_api/sparse_embedder.py mtg-api/tests/test_embedder.py mtg-api/tests/test_sparse_embedder.py
git commit -m "feat(mtg-api): add Embedder and SparseEmbedder seams for query-time encoding"
```

---

### Task 2: CardMatcher (Aho-Corasick card-name detection)

**Files:**
- Create: `mtg-api/src/mtg_api/card_matcher.py`
- Test: `mtg-api/tests/test_card_matcher.py`

**Interfaces:**
- Produces: `mtg_api.card_matcher.CardMatcher(cards: list[dict])` with `.find_matches(query: str) -> list[dict]` (each returned dict is the original card row, deduplicated by name); `mtg_api.card_matcher.load_card_matcher(cards_path: Path) -> CardMatcher`.

- [ ] **Step 1: Write the failing tests**

```python
# mtg-api/tests/test_card_matcher.py
from mtg_api.card_matcher import CardMatcher

CARDS = [
    {"oracle_id": "oid-1", "name": "Bolt", "oracle_text": "Bolt text."},
    {"oracle_id": "oid-2", "name": "Lightning Bolt", "oracle_text": "Deals 3 damage."},
    {"oracle_id": "oid-3", "name": "Counterspell", "oracle_text": "Counter target spell."},
]


def test_finds_exact_single_word_match():
    matcher = CardMatcher(CARDS)
    matches = matcher.find_matches("does Counterspell stop everything?")
    names = {c["name"] for c in matches}
    assert names == {"Counterspell"}


def test_finds_multi_word_match():
    matcher = CardMatcher(CARDS)
    matches = matcher.find_matches("how good is Lightning Bolt")
    names = {c["name"] for c in matches}
    assert "Lightning Bolt" in names


def test_case_insensitive():
    matcher = CardMatcher(CARDS)
    matches = matcher.find_matches("COUNTERSPELL rules?")
    names = {c["name"] for c in matches}
    assert "Counterspell" in names


def test_word_boundary_prevents_substring_false_positive():
    matcher = CardMatcher(CARDS)
    matches = matcher.find_matches("what does Voltaic Boltcaster do")
    names = {c["name"] for c in matches}
    assert "Bolt" not in names


def test_empty_query_returns_no_matches():
    matcher = CardMatcher(CARDS)
    assert matcher.find_matches("") == []


def test_no_matches_returns_empty_list():
    matcher = CardMatcher(CARDS)
    assert matcher.find_matches("just a generic rules question") == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd mtg-api && python -m pytest tests/test_card_matcher.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'mtg_api.card_matcher'`)

- [ ] **Step 3: Write the implementation**

```python
# mtg-api/src/mtg_api/card_matcher.py
from __future__ import annotations

import json
from pathlib import Path

import ahocorasick


class CardMatcher:
    def __init__(self, cards: list[dict]):
        self._cards_by_key: dict[str, dict] = {}
        self._automaton = ahocorasick.Automaton()
        for card in cards:
            key = card["name"].lower()
            self._cards_by_key[key] = card
            self._automaton.add_word(key, key)
        self._automaton.make_automaton()

    def find_matches(self, query: str) -> list[dict]:
        query_lower = query.lower()
        matched_keys: set[str] = set()
        for end_index, key in self._automaton.iter(query_lower):
            start_index = end_index - len(key) + 1
            before_ok = start_index == 0 or not query_lower[start_index - 1].isalnum()
            after_index = end_index + 1
            after_ok = after_index == len(query_lower) or not query_lower[after_index].isalnum()
            if before_ok and after_ok:
                matched_keys.add(key)
        return [self._cards_by_key[key] for key in matched_keys]


def load_card_matcher(cards_path: Path) -> CardMatcher:
    cards = []
    with cards_path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                cards.append(json.loads(line))
    return CardMatcher(cards)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd mtg-api && pip install pyahocorasick>=2.0 && python -m pytest tests/test_card_matcher.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add mtg-api/src/mtg_api/card_matcher.py mtg-api/tests/test_card_matcher.py
git commit -m "feat(mtg-api): add Aho-Corasick CardMatcher for exact card-name detection"
```

---

### Task 3: Hybrid search (score fusion)

**Files:**
- Create: `mtg-api/src/mtg_api/retrieval.py`
- Test: `mtg-api/tests/test_retrieval.py`

**Interfaces:**
- Consumes: `mtg_api.sparse_embedder.SparseVector` (Task 1).
- Produces: `mtg_api.retrieval._normalize(scores: dict[str, float]) -> dict[str, float]`; `mtg_api.retrieval.hybrid_search(client, collection_name, dense_vector, sparse_vector, per_branch_limit, dense_weight, sparse_weight, score_threshold, top_k) -> list[tuple[str, float, dict]]`.

- [ ] **Step 1: Write the failing tests**

```python
# mtg-api/tests/test_retrieval.py
from mtg_api.retrieval import _normalize, hybrid_search
from mtg_api.sparse_embedder import SparseVector


class _FakeHit:
    def __init__(self, id, score, payload):
        self.id = id
        self.score = score
        self.payload = payload


class _FakeQueryResult:
    def __init__(self, points):
        self.points = points


class _FakeClient:
    def __init__(self, dense_points, sparse_points):
        self._dense_points = dense_points
        self._sparse_points = sparse_points
        self.calls: list[tuple[str, int]] = []

    def query_points(self, collection_name, using, query, limit, with_payload):
        self.calls.append((using, limit))
        points = self._dense_points if using == "dense" else self._sparse_points
        return _FakeQueryResult(points[:limit])


def test_normalize_empty_returns_empty():
    assert _normalize({}) == {}


def test_normalize_single_distinct_value_maps_to_one():
    assert _normalize({"a": 5.0}) == {"a": 1.0}
    assert _normalize({"a": 3.0, "b": 3.0}) == {"a": 1.0, "b": 1.0}


def test_normalize_spread_maps_to_zero_one_range():
    result = _normalize({"a": 0.0, "b": 5.0, "c": 10.0})
    assert result == {"a": 0.0, "b": 0.5, "c": 1.0}


def test_hybrid_search_combines_dense_and_sparse_with_weights():
    dense_points = [_FakeHit("p1", 1.0, {"text": "dense hit"}), _FakeHit("p2", 0.0, {"text": "other"})]
    sparse_points = [_FakeHit("p2", 1.0, {"text": "other"}), _FakeHit("p1", 0.0, {"text": "dense hit"})]
    client = _FakeClient(dense_points, sparse_points)

    results = hybrid_search(
        client,
        "test_collection",
        [0.1] * 4,
        SparseVector(indices=[0], values=[1.0]),
        per_branch_limit=10,
        dense_weight=0.5,
        sparse_weight=0.5,
        score_threshold=0.0,
        top_k=10,
    )

    result_ids = [r[0] for r in results]
    assert set(result_ids) == {"p1", "p2"}
    for _point_id, score, _payload in results:
        assert score == 0.5


def test_hybrid_search_respects_score_threshold():
    dense_points = [_FakeHit("p1", 1.0, {"text": "a"}), _FakeHit("p2", 0.0, {"text": "b"})]
    client = _FakeClient(dense_points, [])

    results = hybrid_search(
        client,
        "test_collection",
        [0.1] * 4,
        SparseVector(indices=[0], values=[1.0]),
        per_branch_limit=10,
        dense_weight=1.0,
        sparse_weight=0.0,
        score_threshold=0.5,
        top_k=10,
    )

    result_ids = [r[0] for r in results]
    assert result_ids == ["p1"]


def test_hybrid_search_truncates_to_top_k():
    dense_points = [_FakeHit(f"p{i}", float(i), {"text": str(i)}) for i in range(5)]
    client = _FakeClient(dense_points, [])

    results = hybrid_search(
        client,
        "test_collection",
        [0.1] * 4,
        SparseVector(indices=[0], values=[1.0]),
        per_branch_limit=10,
        dense_weight=1.0,
        sparse_weight=0.0,
        score_threshold=0.0,
        top_k=2,
    )

    assert len(results) == 2
    assert results[0][1] >= results[1][1]


def test_hybrid_search_uses_per_branch_limit():
    dense_points = [_FakeHit(f"p{i}", float(i), {"text": str(i)}) for i in range(5)]
    client = _FakeClient(dense_points, [])

    hybrid_search(
        client,
        "test_collection",
        [0.1] * 4,
        SparseVector(indices=[0], values=[1.0]),
        per_branch_limit=2,
        dense_weight=1.0,
        sparse_weight=0.0,
        score_threshold=0.0,
        top_k=10,
    )

    assert client.calls == [("dense", 2), ("sparse", 2)]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd mtg-api && python -m pytest tests/test_retrieval.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'mtg_api.retrieval'`)

- [ ] **Step 3: Write the implementation**

```python
# mtg-api/src/mtg_api/retrieval.py
from __future__ import annotations

from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

from mtg_api.sparse_embedder import SparseVector


def _normalize(scores: dict[str, float]) -> dict[str, float]:
    if not scores:
        return {}
    values = list(scores.values())
    lo, hi = min(values), max(values)
    if hi == lo:
        return {k: 1.0 for k in scores}
    return {k: (v - lo) / (hi - lo) for k, v in scores.items()}


def hybrid_search(
    client: QdrantClient,
    collection_name: str,
    dense_vector: list[float],
    sparse_vector: SparseVector,
    per_branch_limit: int,
    dense_weight: float,
    sparse_weight: float,
    score_threshold: float,
    top_k: int,
) -> list[tuple[str, float, dict]]:
    dense_hits = client.query_points(
        collection_name=collection_name,
        using="dense",
        query=dense_vector,
        limit=per_branch_limit,
        with_payload=True,
    ).points
    sparse_hits = client.query_points(
        collection_name=collection_name,
        using="sparse",
        query=qmodels.SparseVector(indices=sparse_vector.indices, values=sparse_vector.values),
        limit=per_branch_limit,
        with_payload=True,
    ).points

    dense_scores = {str(h.id): h.score for h in dense_hits}
    sparse_scores = {str(h.id): h.score for h in sparse_hits}
    payloads: dict[str, dict] = {}
    for h in dense_hits:
        payloads[str(h.id)] = h.payload
    for h in sparse_hits:
        payloads.setdefault(str(h.id), h.payload)

    dense_norm = _normalize(dense_scores)
    sparse_norm = _normalize(sparse_scores)

    combined = []
    for point_id in set(dense_scores) | set(sparse_scores):
        score = dense_weight * dense_norm.get(point_id, 0.0) + sparse_weight * sparse_norm.get(
            point_id, 0.0
        )
        if score >= score_threshold:
            combined.append((point_id, score, payloads[point_id]))

    combined.sort(key=lambda item: item[1], reverse=True)
    return combined[:top_k]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd mtg-api && python -m pytest tests/test_retrieval.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add mtg-api/src/mtg_api/retrieval.py mtg-api/tests/test_retrieval.py
git commit -m "feat(mtg-api): add weighted dense+sparse hybrid search fusion"
```

---

### Task 4: Config additions

**Files:**
- Modify: `mtg-api/src/mtg_api/config.py`
- Modify: `mtg-api/tests/test_config.py`

**Interfaces:**
- Produces: `mtg_api.config.Settings` gains `collection_name: str = "mtg_rules"`, `parsed_dir: Path = Path("../mtg-worker/mtg-ingestion/data/parsed")`, `dense_model_name: str = "BAAI/bge-base-en-v1.5"`, `sparse_model_name: str = "Qdrant/bm25"`, `hybrid_dense_weight: float = 0.5`, `hybrid_sparse_weight: float = 0.5`, `hybrid_top_k: int = 10`, `hybrid_per_branch_limit: int = 50`, `hybrid_score_threshold: float = 0.0`.

- [ ] **Step 1: Write the failing tests**

Add to `mtg-api/tests/test_config.py` (append, don't replace existing tests):

```python
def test_hybrid_defaults():
    s = Settings(_env_file=None)
    assert s.collection_name == "mtg_rules"
    assert s.dense_model_name == "BAAI/bge-base-en-v1.5"
    assert s.sparse_model_name == "Qdrant/bm25"
    assert s.hybrid_dense_weight == 0.5
    assert s.hybrid_sparse_weight == 0.5
    assert s.hybrid_top_k == 10
    assert s.hybrid_per_branch_limit == 50
    assert s.hybrid_score_threshold == 0.0


def test_hybrid_env_override(monkeypatch):
    monkeypatch.setenv("MTG_API_HYBRID_DENSE_WEIGHT", "0.7")
    s = Settings(_env_file=None)
    assert s.hybrid_dense_weight == 0.7
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd mtg-api && python -m pytest tests/test_config.py -v`
Expected: FAIL (`AttributeError: 'Settings' object has no attribute 'collection_name'`)

- [ ] **Step 3: Write the implementation**

In `mtg-api/src/mtg_api/config.py`, add `from pathlib import Path` to the imports at the top, then add these fields to `Settings`, after `result_backend`:

```python
    collection_name: str = "mtg_rules"
    parsed_dir: Path = Path("../mtg-worker/mtg-ingestion/data/parsed")
    dense_model_name: str = "BAAI/bge-base-en-v1.5"
    sparse_model_name: str = "Qdrant/bm25"
    hybrid_dense_weight: float = 0.5
    hybrid_sparse_weight: float = 0.5
    hybrid_top_k: int = 10
    hybrid_per_branch_limit: int = 50
    hybrid_score_threshold: float = 0.0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd mtg-api && python -m pytest tests/test_config.py -v`
Expected: PASS (all tests, including the 2 new ones)

- [ ] **Step 5: Commit**

```bash
git add mtg-api/src/mtg_api/config.py mtg-api/tests/test_config.py
git commit -m "feat(mtg-api): add collection/model/hybrid-search config"
```

---

### Task 5: Wire the real POST /api/v1/query endpoint

**Files:**
- Modify: `mtg-api/src/mtg_api/models.py`
- Modify: `mtg-api/src/mtg_api/main.py`
- Modify: `mtg-api/tests/test_query.py`
- Modify: `mtg-api/tests/test_models.py`

**Interfaces:**
- Consumes: `CardMatcher`/`load_card_matcher` (Task 2), `Embedder`/`load_sentence_transformer_embedder` (Task 1), `SparseEmbedder`/`load_bm25_sparse_embedder` (Task 1), `hybrid_search` (Task 3), `settings.*` hybrid/model/collection fields (Task 4).
- Produces: `mtg_api.models.QueryResult` gains `match_type: str` (required, no default — deliberately, so every construction site is explicit about which kind of match it represents) and `oracle_id: str | None = None`; `mtg_api.main.get_card_matcher() -> CardMatcher`, `get_dense_embedder() -> Embedder`, `get_sparse_embedder() -> SparseEmbedder` (all `lru_cache`-wrapped, overridable via `app.dependency_overrides`); `POST /api/v1/query`'s dummy body is replaced with real card-match + hybrid-search logic.

**Note:** `test_models.py` (from an earlier plan) constructs `QueryResult(source="rule", title="702.19", text="Trample text", score=0.9)` with no `match_type` — that construction breaks once `match_type` becomes required. Step 1 below updates that call site in the same commit, not as an afterthought.

- [ ] **Step 1: Write the failing tests**

In `mtg-api/tests/test_models.py`, change the `test_query_response_holds_results_list` test from:

```python
def test_query_response_holds_results_list():
    result = QueryResult(source="rule", title="702.19", text="Trample text", score=0.9)
    resp = QueryResponse(query="trample", results=[result])
    assert resp.results[0].source == "rule"
    assert resp.results[0].score == 0.9
```

to:

```python
def test_query_response_holds_results_list():
    result = QueryResult(
        source="rule", title="702.19", text="Trample text", score=0.9, match_type="vector_hit"
    )
    resp = QueryResponse(query="trample", results=[result])
    assert resp.results[0].source == "rule"
    assert resp.results[0].score == 0.9
```

Replace the entire contents of `mtg-api/tests/test_query.py`:

```python
# mtg-api/tests/test_query.py
from fastapi.testclient import TestClient

from mtg_api.card_matcher import CardMatcher
from mtg_api.embedder import Embedder
from mtg_api.main import app, get_card_matcher, get_dense_embedder, get_qdrant_client, get_sparse_embedder
from mtg_api.sparse_embedder import SparseEmbedder


class _FakeDenseModel:
    def encode(self, texts, batch_size, show_progress_bar=False):
        return [[0.1, 0.2, 0.3, 0.4] for _ in texts]

    def get_sentence_embedding_dimension(self):
        return 4


class _FakeSparseEmbedding:
    indices = [0]
    values = [1.0]


class _FakeSparseModel:
    def embed(self, texts):
        return [_FakeSparseEmbedding() for _ in texts]


class _FakeHit:
    def __init__(self, id, score, payload):
        self.id = id
        self.score = score
        self.payload = payload


class _FakeQueryResult:
    def __init__(self, points):
        self.points = points


class _FakeQdrantClient:
    def __init__(self, dense_points=None, sparse_points=None):
        self._dense_points = dense_points or []
        self._sparse_points = sparse_points or []

    def query_points(self, collection_name, using, query, limit, with_payload):
        points = self._dense_points if using == "dense" else self._sparse_points
        return _FakeQueryResult(points[:limit])


def _override(cards=None, dense_points=None, sparse_points=None):
    app.dependency_overrides[get_card_matcher] = lambda: CardMatcher(cards or [])
    app.dependency_overrides[get_dense_embedder] = lambda: Embedder(_FakeDenseModel())
    app.dependency_overrides[get_sparse_embedder] = lambda: SparseEmbedder(_FakeSparseModel())
    app.dependency_overrides[get_qdrant_client] = lambda: _FakeQdrantClient(dense_points, sparse_points)


def test_query_returns_card_match_when_name_detected():
    cards = [{"oracle_id": "oid-1", "name": "Counterspell", "oracle_text": "Counter target spell."}]
    _override(cards=cards)
    try:
        resp = TestClient(app).post("/api/v1/query", json={"query": "how does Counterspell work"})
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    card_hits = [r for r in body["results"] if r["match_type"] == "card_name_match"]
    assert len(card_hits) == 1
    assert card_hits[0]["title"] == "Counterspell"
    assert card_hits[0]["oracle_id"] == "oid-1"
    assert card_hits[0]["score"] == 1.0


def test_query_returns_vector_hit_when_no_card_named():
    dense_points = [
        _FakeHit("p1", 1.0, {"source_type": "rule", "rule_id": "702.19", "text": "Trample text"})
    ]
    _override(dense_points=dense_points)
    try:
        resp = TestClient(app).post("/api/v1/query", json={"query": "how does trample work"})
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    vector_hits = [r for r in body["results"] if r["match_type"] == "vector_hit"]
    assert len(vector_hits) == 1
    assert vector_hits[0]["title"] == "702.19"
    assert vector_hits[0]["source"] == "rule"


def test_query_dedupes_vector_hit_matching_a_card_match():
    cards = [{"oracle_id": "oid-1", "name": "Counterspell", "oracle_text": "Counter target spell."}]
    dense_points = [
        _FakeHit(
            "p1",
            1.0,
            {
                "source_type": "oracle",
                "card_name": "Counterspell",
                "oracle_id": "oid-1",
                "text": "Counter target spell.",
            },
        )
    ]
    _override(cards=cards, dense_points=dense_points)
    try:
        resp = TestClient(app).post("/api/v1/query", json={"query": "Counterspell rulings"})
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["results"]) == 1
    assert body["results"][0]["match_type"] == "card_name_match"


def test_query_rejects_missing_query_field():
    resp = TestClient(app).post("/api/v1/query", json={})
    assert resp.status_code == 422


def test_cors_header_present_for_configured_origin():
    _override()
    try:
        resp = TestClient(app).post(
            "/api/v1/query",
            json={"query": "trample"},
            headers={"Origin": "http://localhost:3000"},
        )
    finally:
        app.dependency_overrides.clear()
    assert resp.headers["access-control-allow-origin"] == "http://localhost:3000"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd mtg-api && python -m pytest tests/test_query.py tests/test_models.py -v`
Expected: FAIL (`ImportError: cannot import name 'get_card_matcher' from 'mtg_api.main'` for `test_query.py`; `test_models.py`'s updated test currently still passes on its own since `match_type` doesn't exist as a field yet and extra kwargs are rejected by pydantic — confirm it now fails with `ValidationError: Unexpected keyword argument` until Step 3 adds the field)

- [ ] **Step 3: Write the implementation**

In `mtg-api/src/mtg_api/models.py`, change the `QueryResult` class from:

```python
class QueryResult(BaseModel):
    source: str
    title: str
    text: str
    score: float
```

to:

```python
class QueryResult(BaseModel):
    source: str
    title: str
    text: str
    score: float
    match_type: str
    oracle_id: str | None = None
```

In `mtg-api/src/mtg_api/main.py`, replace the entire file's contents:

```python
# mtg-api/src/mtg_api/main.py
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from celery import Celery
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from qdrant_client import QdrantClient

from mtg_api.card_matcher import CardMatcher, load_card_matcher
from mtg_api.celery_client import get_celery_client
from mtg_api.config import settings
from mtg_api.embedder import Embedder, load_sentence_transformer_embedder
from mtg_api.models import EmbedRequest, QueryRequest, QueryResponse, QueryResult
from mtg_api.qdrant_check import check_qdrant
from mtg_api.retrieval import hybrid_search
from mtg_api.sparse_embedder import SparseEmbedder, load_bm25_sparse_embedder

app = FastAPI(title="mtg-api")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


def get_qdrant_client() -> QdrantClient:
    return QdrantClient(host=settings.qdrant_host, port=settings.qdrant_port)


def _latest(directory: Path, pattern: str) -> Path:
    matches = sorted(directory.glob(pattern))
    if not matches:
        raise FileNotFoundError(f"No files matching {pattern!r} in {directory}")
    return matches[-1]


@lru_cache(maxsize=1)
def get_card_matcher() -> CardMatcher:
    cards_path = _latest(settings.parsed_dir, "cards_*.jsonl")
    return load_card_matcher(cards_path)


@lru_cache(maxsize=1)
def get_dense_embedder() -> Embedder:
    return load_sentence_transformer_embedder(settings.dense_model_name, batch_size=1)


@lru_cache(maxsize=1)
def get_sparse_embedder() -> SparseEmbedder:
    return load_bm25_sparse_embedder(settings.sparse_model_name)


@app.get("/health")
def health(client: QdrantClient = Depends(get_qdrant_client)) -> dict:
    qdrant_status = "ok" if check_qdrant(client) else "unreachable"
    return {"status": "ok", "qdrant": qdrant_status}


@app.post("/api/v1/query", response_model=QueryResponse)
def query(
    request: QueryRequest,
    matcher: CardMatcher = Depends(get_card_matcher),
    dense_embedder: Embedder = Depends(get_dense_embedder),
    sparse_embedder: SparseEmbedder = Depends(get_sparse_embedder),
    client: QdrantClient = Depends(get_qdrant_client),
) -> QueryResponse:
    card_results = [
        QueryResult(
            source="card",
            title=card["name"],
            text=card.get("oracle_text", ""),
            score=1.0,
            match_type="card_name_match",
            oracle_id=card.get("oracle_id"),
        )
        for card in matcher.find_matches(request.query)
    ]
    matched_oracle_ids = {r.oracle_id for r in card_results if r.oracle_id}

    dense_vector = dense_embedder.encode([request.query])[0]
    sparse_vector = sparse_embedder.encode([request.query])[0]
    hits = hybrid_search(
        client,
        settings.collection_name,
        dense_vector,
        sparse_vector,
        settings.hybrid_per_branch_limit,
        settings.hybrid_dense_weight,
        settings.hybrid_sparse_weight,
        settings.hybrid_score_threshold,
        settings.hybrid_top_k,
    )

    vector_results = []
    for point_id, score, payload in hits:
        oracle_id = payload.get("oracle_id")
        if oracle_id and oracle_id in matched_oracle_ids:
            continue
        vector_results.append(
            QueryResult(
                source=payload.get("source_type", "unknown"),
                title=payload.get("card_name") or payload.get("rule_id", ""),
                text=payload.get("text", ""),
                score=score,
                match_type="vector_hit",
                oracle_id=oracle_id,
            )
        )

    return QueryResponse(query=request.query, results=card_results + vector_results)


@app.post("/api/v1/ingest")
def trigger_ingest(client: Celery = Depends(get_celery_client)) -> dict:
    result = client.send_task("mtg_worker.ingest")
    return {"task_id": result.id}


@app.post("/api/v1/embed")
def trigger_embed(request: EmbedRequest, client: Celery = Depends(get_celery_client)) -> dict:
    if request.limit == "all":
        limit = None
    else:
        try:
            limit = int(request.limit)
        except ValueError:
            raise HTTPException(status_code=400, detail='limit must be "all" or a positive integer')
        if limit <= 0:
            raise HTTPException(status_code=400, detail='limit must be "all" or a positive integer')
    result = client.send_task("mtg_worker.embed", kwargs={"limit": limit})
    return {"task_id": result.id}


@app.get("/api/v1/tasks/{task_id}")
def get_task_status(task_id: str, client: Celery = Depends(get_celery_client)) -> dict:
    result = client.AsyncResult(task_id)
    return {
        "task_id": task_id,
        "status": result.status,
        "result": result.result if result.ready() else None,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd mtg-api && python -m pytest tests/ -v`
Expected: PASS (all tests — this also re-runs every earlier test file as a full-suite regression check)

- [ ] **Step 5: Commit**

```bash
git add mtg-api/src/mtg_api/models.py mtg-api/src/mtg_api/main.py mtg-api/tests/test_query.py mtg-api/tests/test_models.py
git commit -m "feat(mtg-api): replace dummy query endpoint with card-match + hybrid search"
```

---

### Task 6: Dependencies, compose wiring, real end-to-end verification

**Files:**
- Modify: `mtg-api/pyproject.toml`
- Modify: `docker-compose.yml` (repo root)

**Interfaces:**
- Consumes: everything from Tasks 1-5, plus Part A's re-embedded `mtg_rules` collection (already has both named vectors and real data by the time this task runs).
- Produces: a working end-to-end hybrid query against real data via `docker compose`.

- [ ] **Step 1: Bump dependencies**

In `mtg-api/pyproject.toml`, add these three lines to the `dependencies` list: `"pyahocorasick>=2.0",`, `"sentence-transformers>=3.0",`, `"fastembed>=0.3",`. Change the existing `"qdrant-client>=1.9",` line to `"qdrant-client>=1.10",`.

- [ ] **Step 2: Reinstall and run the full test suite**

Run:
```bash
cd mtg-api
pip install -e ".[dev]"
python -m pytest tests/ -v
```
Expected: PASS (all tests)

- [ ] **Step 3: Commit the dependency change**

```bash
git add mtg-api/pyproject.toml
git commit -m "chore(mtg-api): add pyahocorasick, sentence-transformers, fastembed; bump qdrant-client floor"
```

- [ ] **Step 4: Give the backend container read access to the card data**

In the root `docker-compose.yml`, add a volume mount and an env var to the `backend` service. Change:

```yaml
  backend:
    build: ./mtg-api
    environment:
      MTG_API_QDRANT_HOST: qdrant
      MTG_API_QDRANT_PORT: "6333"
      MTG_API_CORS_ORIGINS: '["http://localhost:3000"]'
      MTG_API_BROKER_URL: redis://redis:6379/0
      MTG_API_RESULT_BACKEND: redis://redis:6379/0
    ports:
      - "8000:8000"
    depends_on:
      - qdrant
      - redis
```

to:

```yaml
  backend:
    build: ./mtg-api
    environment:
      MTG_API_QDRANT_HOST: qdrant
      MTG_API_QDRANT_PORT: "6333"
      MTG_API_CORS_ORIGINS: '["http://localhost:3000"]'
      MTG_API_BROKER_URL: redis://redis:6379/0
      MTG_API_RESULT_BACKEND: redis://redis:6379/0
      MTG_API_PARSED_DIR: /app/data/parsed
    volumes:
      - ./mtg-worker/mtg-ingestion/data/parsed:/app/data/parsed:ro
    ports:
      - "8000:8000"
    depends_on:
      - qdrant
      - redis
```

- [ ] **Step 5: Validate the compose file parses**

Run: `docker compose config --quiet`
Expected: no output, exit code 0.

- [ ] **Step 6: Bring up the stack and run a real query**

```bash
docker compose up --build -d qdrant redis worker backend
sleep 5
curl -s -X POST localhost:8000/api/v1/query -H "Content-Type: application/json" -d '{"query": "how does Lightning Bolt interact with trample"}' | python -m json.tool
```
This is real end to end: real card automaton built from the live
`cards_*.jsonl`, real dense + sparse model downloads (cold-cache, can
take a few minutes the first time), real hybrid search against the
`mtg_rules` collection Part A's plan already populated. Expected: a JSON
response whose `results` include at least one `match_type ==
"card_name_match"` entry titled `"Lightning Bolt"`, plus one or more
`match_type == "vector_hit"` entries relating to trample.

- [ ] **Step 7: Tear down**

```bash
docker compose down
```
