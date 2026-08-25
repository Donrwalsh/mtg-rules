# Hybrid query architecture: card-name matching + dense/sparse Qdrant search

Status: approved, ready for implementation planning
Date: 2026-08-25

## Purpose

This is the retrieval stage the earlier `mtg-embed` spec explicitly deferred
("no retrieval/query-time logic"). A user query now produces one unified
list of sources: exact card names detected via Aho-Corasick, plus hybrid
(dense + sparse) Qdrant vector search over the full corpus, deduplicated
against each other. This replaces `mtg-api`'s existing dummy
`POST /api/v1/query` response. No answer synthesis, no citation formatting
— just the retrieval layer a later stage builds on.

## Two coupled parts

`mtg-api` (part B) queries Qdrant with both a dense and a sparse vector.
The collection today only stores dense vectors (confirmed in
`qdrant_store.py`: a single unnamed `VectorParams`), so part A upgrades
`mtg-embed` to compute and store sparse vectors too, before part B can do
real hybrid search. **Implementation should be planned as two sequential
plans** — part A first (touches already-shipped `mtg-embed` code, ends
with a fresh embed run), part B second (net-new `mtg-api` code, depends
on part A's collection schema existing).

## Part A: mtg-embed sparse vectors

**Sparse model:** `fastembed`'s BM25 implementation (`Qdrant/bm25`) — a
lightweight, non-neural sparse embedder (no model download beyond a small
IDF table), genuinely lexical/complementary to the dense semantic vector.
Chosen over a neural sparse model (e.g. SPLADE) to avoid a second heavy
download on top of `bge-base-en-v1.5`.

**Collection schema change:** Qdrant requires named vectors once a
collection has more than one. The collection moves from a single unnamed
dense vector to two named vectors, fixed as an exact contract shared with
`mtg-api` (not configurable — only the weights/limits/thresholds around
them are):
- `"dense"`: existing `bge-base-en-v1.5` output, cosine distance.
- `"sparse"`: BM25 sparse vector.

**Migration:** Qdrant cannot add a named vector to an existing
unnamed-vector collection in place. The existing `mtg_rules` collection
(dense-only, ~11k points from prior verification runs) must be deleted
before the upgraded pipeline runs — a one-time manual step (`docker
compose run --rm worker` won't help here; delete via the Qdrant HTTP API
or dashboard), documented in the implementation plan, not automated. The
upgraded `ensure_collection` still just creates-if-missing, same as
today — it does not detect or migrate a stale schema.

**New file `sparse_embedder.py`** (mirrors `embedder.py`'s seam pattern
exactly — a `Protocol`-typed model, a thin wrapper class, a lazily-
imported real-model factory):
- `SparseVector` — a small dataclass, `indices: list[int]`,
  `values: list[float]`.
- `SparseEncoderModel` — `Protocol` with `.embed(texts) -> Sequence[object]`
  (each yielding `.indices`/`.values`), matching `fastembed`'s
  `SparseTextEmbedding.embed()` shape.
- `SparseEmbedder(model, ...)` — `.encode(texts: list[str]) ->
  list[SparseVector]`.
- `load_bm25_sparse_embedder(model_name: str) -> SparseEmbedder` — real
  factory, `from fastembed import SparseTextEmbedding` imported inside
  the function body.

**`qdrant_store.py` changes:**
- `ensure_collection(dense_size: int)` — `vectors_config` becomes
  `{"dense": VectorParams(size=dense_size, distance=Distance.COSINE)}`;
  add `sparse_vectors_config={"sparse": SparseVectorParams()}`.
- `upsert(chunks, dense_vectors, sparse_vectors)` — signature gains a
  third parameter; each `PointStruct.vector` becomes `{"dense":
  dense_vector, "sparse": qmodels.SparseVector(indices=..., values=...)}`
  instead of a bare vector. This is a breaking change to the existing
  tests in `test_qdrant_store.py` — they get updated as part of this
  work, not left broken.

**`pipeline.py` changes:** `embed_and_store` gains a `sparse_embedder:
SparseEmbedder` parameter (after `embedder`, before
`retrieve_batch_size`); wherever it currently calls
`embedder.encode(texts)` then `store.upsert(to_embed, vectors)`, it now
also calls `sparse_embedder.encode(texts)` and passes both vector lists
to the updated `upsert`.

**`cli.py` changes:** constructs a `SparseEmbedder` alongside the
existing `Embedder` (same lazy-import-inside-the-command-body style
already used for the dense one) and threads it into `embed_and_store`.

**Payload addition (small, opportunistic — the forced re-embed makes
this free):** `sources/cards.py` and `sources/rulings.py`'s payload
dicts currently have `card_name` but no `oracle_id` — confirmed by
reading both files. `mtg-api`'s dedup logic (part B) needs a stable
identifier to match a Qdrant vector hit against an Aho-Corasick card
match, and `oracle_id` is the identifier already used everywhere else in
this codebase (`ids.py`'s point-ID schemes key off it) — a name-string
match would be strictly worse (punctuation/casing drift, double-faced
naming quirks). Both payload dicts gain `"oracle_id": row["oracle_id"]`.
`sources/rules.py`'s payload is untouched (rules have no `oracle_id`).

## Part B: mtg-api query architecture

Built directly inside `mtg-api` (not a new package, per explicit
decision) — `mtg-api` now depends on `pyahocorasick`, `sentence-
transformers`, and `fastembed` directly. Real cost worth restating: this
makes the API process load the same ~400MB dense model `mtg-worker`
already loads, plus the BM25 sparse model, both once at first use (not
per-request) — the API container gets heavier and slower to cold-start,
comparable to `mtg-worker` today. Both are cached process-wide via
`functools.lru_cache` on their FastAPI dependency-provider functions
(lazy — nothing loads until the first real request hits `/api/v1/query`;
this matters because `mtg-api`'s existing test suite must keep running
without ever touching either model).

**New config** (`mtg_api.config.Settings`, still env prefix `MTG_API_`):
- `parsed_dir: Path = Path("../mtg-worker/mtg-ingestion/data/parsed")` —
  where to find the newest `cards_*.jsonl` for the card dictionary.
- `dense_model_name: str = "BAAI/bge-base-en-v1.5"`
- `sparse_model_name: str = "Qdrant/bm25"`
- `hybrid_dense_weight: float = 0.5`
- `hybrid_sparse_weight: float = 0.5`
- `hybrid_top_k: int = 10` — final result count after fusion.
- `hybrid_per_branch_limit: int = 50` — candidates fetched from each of
  the dense/sparse searches before fusion (must exceed `hybrid_top_k` to
  give fusion a real pool to work with).
- `hybrid_score_threshold: float = 0.0` — minimum combined score to keep
  a hit.

**Compose change:** the `backend` service needs read access to the same
data `worker` already bind-mounts — add `./mtg-worker/mtg-ingestion/data/parsed:/app/data/parsed:ro`
and `MTG_API_PARSED_DIR: /app/data/parsed` to the `backend` service in
the root `docker-compose.yml`.

**New file `card_matcher.py`:**
- `CardMatcher(cards: list[dict])` — builds a `pyahocorasick.Automaton`
  once from `card["name"].lower()` for every card, keyed by the
  lowercased name, value is the lowercased name (used to look the full
  card dict back up post-match).
- `.find_matches(query: str) -> list[dict]` — lowercases the query,
  iterates automaton matches, keeps only matches bounded by non-
  alphanumeric characters (or string start/end) on both sides — this is
  what stops "Bolt" matching inside a longer word that happens to
  contain those letters — and returns the full card dict for each
  surviving match (deduplicated by name).
- `load_card_matcher(cards_path: Path) -> CardMatcher` — reads the JSONL
  file, builds the matcher.

**New file `retrieval.py`:**
- `_normalize(scores: dict[str, float]) -> dict[str, float]` — min-max
  normalization to `[0, 1]`; an empty input returns `{}`; a single
  distinct value maps everything to `1.0` (avoids a divide-by-zero).
- `hybrid_search(client, collection_name, dense_vector, sparse_vector,
  per_branch_limit, dense_weight, sparse_weight, score_threshold,
  top_k) -> list[tuple[str, float, dict]]` — runs two separate
  `client.query_points(..., using="dense"/"sparse", ...)` calls (each
  capped at `per_branch_limit`), normalizes each result set's scores
  independently, combines as `dense_weight * dense_norm.get(id, 0) +
  sparse_weight * sparse_norm.get(id, 0)` for the union of both ID sets,
  drops anything below `score_threshold`, sorts descending, truncates to
  `top_k`. Returns `(point_id, combined_score, payload)` tuples. Chosen
  over Qdrant's built-in RRF/DBSF fusion because those don't accept
  literal tunable weights — you specifically asked for dense/sparse
  weights, not rank-based fusion.
- Requires `qdrant-client>=1.10` (the version that added
  `query_points`'s `using=` named-vector parameter) — both `mtg-api` and
  `mtg-embed`'s dependency floors bump to match.

**`models.py` changes:** `QueryResult` gains two fields: `match_type:
str` (`"card_name_match"` or `"vector_hit"`) and `oracle_id: str | None
= None` (the dedup key, and useful for later citation work). `source`'s
existing values (`"rule"`, `"ruling"`, `"oracle"`) gain a fourth,
`"card"`, for exact name matches.

**`main.py` changes:** `POST /api/v1/query`'s dummy body is replaced:
1. `matcher.find_matches(request.query)` → one `QueryResult` per match,
   `source="card"`, `title=card["name"]`, `text=card.get("oracle_text",
   "")`, `score=1.0`, `match_type="card_name_match"`,
   `oracle_id=card.get("oracle_id")`. Collect the matched `oracle_id`s
   into a set for the dedup step below.
2. Encode `request.query` once with the dense embedder and once with the
   sparse embedder (each a 1-item `.encode([...])` call).
3. `hybrid_search(...)` with the five config values from `settings`.
4. For each hit: if its payload's `oracle_id` is already in the matched
   set from step 1, skip it (already represented by the exact card
   match). Otherwise build a `QueryResult` with `source=payload
   ["source_type"]`, `title` = `payload.get("rule_id")` for rules,
   `payload.get("card_name")` for rulings/oracle, `text=payload["text"]`,
   `score` = the fused score, `match_type="vector_hit"`,
   `oracle_id=payload.get("oracle_id")`.
5. `QueryResponse(query=request.query, results=card_results +
   vector_results)`.

**Dependency-injection seam** (matches the existing `get_qdrant_client`/
`get_celery_client` pattern, so every one of these is overridable in
tests without touching a real model, index, or network): `get_card_matcher()`,
`get_dense_embedder()`, `get_sparse_embedder()`, each `@lru_cache`-wrapped
so the real, expensive version only ever builds once per process and only
on first actual use.

## Testing plan

**Part A** (all network/model-free, seams mirror the existing
`Embedder`/`QdrantStore` test patterns):
- `SparseEmbedder` against a fake `SparseEncoderModel`.
- `QdrantStore.ensure_collection`/`upsert` against Qdrant's `:memory:`
  mode, asserting both named vectors round-trip.
- `embed_and_store` against fakes of both embedders, asserting both get
  called with the same `to_embed` texts.
- `cards.py`/`rulings.py` payload tests updated to assert `oracle_id` is
  present.

**Part B:**
- `CardMatcher.find_matches`: an exact single-word match, a multi-word
  match, a word-boundary near-miss that must NOT match (e.g. a card
  named "Bolt" against the query "Voltaic Boltcaster"), case-
  insensitivity, an empty query.
- `_normalize`: empty input, single distinct value, a normal spread.
- `hybrid_search` against a fake Qdrant client (two canned
  `query_points` responses) — asserts the weighted combination, the
  threshold cutoff, and the `top_k` truncation independently.
- `POST /api/v1/query` end to end via `TestClient`, with `get_card_matcher`/
  `get_dense_embedder`/`get_sparse_embedder`/`get_qdrant_client` all
  overridden to fakes — covering: a card-only match, a vector-only
  match, a case where a vector hit's `oracle_id` duplicates a card match
  (must be dropped), and the fully-dummy-free happy path.
- One end-to-end compose smoke check after part A's re-embed: a real
  query against real data, confirming both card and vector-hit results
  come back with sane scores.

## Out of scope

- No answer synthesis or citation formatting — this stage returns
  sources, not prose.
- No cross-reference expansion ("see rule X" resolution).
- No per-request override of the hybrid weights/limits/threshold — they
  are process-wide config, not query parameters, for this stage.
- No reload endpoint for the card dictionary (confirmed decision) — a
  fresh `cards_*.jsonl` is picked up only on `mtg-api` restart.
- No auth on the query endpoint.

## Dependencies

`mtg-worker/mtg-embed/pyproject.toml`: add `fastembed>=0.3`; bump
`qdrant-client` floor to `>=1.10`.
`mtg-api/pyproject.toml`: add `pyahocorasick>=2.0`, `sentence-
transformers>=3.0`, `fastembed>=0.3`; bump its existing `qdrant-
client>=1.9` floor to `>=1.10` (already a dependency via the existing
`get_qdrant_client`/health-check code).
