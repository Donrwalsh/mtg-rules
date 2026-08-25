# mtg-embed: embedding pipeline + Qdrant vector store

Status: approved, ready for implementation planning
Date: 2026-08-25

## Purpose

This is the second stage of a longer-term RAG-driven MTG rules assistant.
The first stage (`mtg-ingestion`) already fetches and parses three source
types into JSONL: Comprehensive Rules (hierarchical, leaf-level), Scryfall
card rulings, and Scryfall oracle card text. This stage takes that JSONL
output and produces embedded, queryable points in a self-hosted Qdrant
instance, so a later (out-of-scope) query stage can retrieve across all
three sources in one search and cite them.

Success for this stage: running the pipeline against the full corpus
produces one Qdrant point per input chunk (skipping unchanged chunks on
repeat runs), with enough payload on each point that retrieval needs no
second lookup elsewhere.

## Confirmed input schema

Read directly from `mtg-ingestion/data/parsed/*.jsonl` (verified against the
actual files, not assumed):

- **Rules** (`rules_*.jsonl`): `rule_id, text, parent_id, content_hash`.
  Hierarchy is flat with `parent_id` links (e.g. `"100.1a"` → parent
  `"100.1"` → parent `"100"` → parent `null`). Leaf-level subrules are
  already present as their own rows.
- **Cards** (`cards_*.jsonl`): `oracle_id, name, oracle_text, type_line,
  mana_cost, content_hash`.
- **Rulings** (`rulings_*.jsonl`): `oracle_id, published_at, comment,
  content_hash`. **No `card_name` field** — the ruling prefix and payload's
  `card_name` require joining against the cards file by `oracle_id`.

## Out of scope

- No retrieval/query-time logic — this stage only embeds and stores.
- No cross-reference expansion ("see rule X" resolution) — deferred to the
  query stage.

## Package layout

New top-level sibling package, independent of `mtg-ingestion` (no shared
import, no path dependency — it reads the JSONL files by their known field
names directly):

```
mtg-embed/
  src/mtg_embed/
    config.py        # Settings: qdrant_host, qdrant_port, collection_name,
                      # parsed_dir, model_name, batch_size, retrieve_batch_size
    models.py         # EmbeddableChunk(id, source_type, text_to_embed, payload)
    sources/
      rules.py        # JSONL -> EmbeddableChunk; walks parent_id for section prefix
      rulings.py      # JSONL (joined against cards.jsonl by oracle_id) -> EmbeddableChunk
      cards.py        # JSONL -> EmbeddableChunk
    qdrant_store.py    # collection setup, batched existing-hash lookup, batched upsert
    embedder.py         # thin wrapper around SentenceTransformer; confirms vector dim
    pipeline.py           # orchestrates: load -> filter unchanged -> embed -> upsert -> summary
    cli.py                 # `mtg-embed run [--source rules|rulings|cards|all] [--limit N]`
  tests/
  pyproject.toml
  Dockerfile
  docker-compose.yml   # qdrant service + embed service (bind-mounts ../mtg-ingestion/data:ro)
```

`--limit N` caps rows read per source, for cheap verification runs without
touching the full corpus.

## Config

`mtg_embed.config.Settings` (pydantic-settings, env prefix `MTG_EMBED_`),
mirroring the pattern already used in `mtg-ingestion/src/mtg_ingestion/config.py`:

| Setting | Default | Purpose |
|---|---|---|
| `qdrant_host` | `"localhost"` | Qdrant server host |
| `qdrant_port` | `6333` | Qdrant server REST port |
| `collection_name` | `"mtg_rules"` | single collection for all source types |
| `parsed_dir` | `Path("../mtg-ingestion/data/parsed")` | where to read the three JSONL sources |
| `model_name` | `"BAAI/bge-base-en-v1.5"` | sentence-transformers model |
| `embed_batch_size` | `32` | rows per `model.encode()` call |
| `retrieve_batch_size` | `256` | point IDs per Qdrant `retrieve()` call for the hash check |

`docker-compose.yml` runs Qdrant as a service (official `qdrant/qdrant`
image, named volume for storage) and an `embed` service that talks to it
over `qdrant:6333` inside the compose network, with
`../mtg-ingestion/data:/app/data:ro` bind-mounted read-only.

## Point ID, payload, and chunk text

**Point ID** (deterministic, so re-runs upsert instead of duplicating —
Qdrant requires int or UUID, so natural keys are hashed into UUID5 under a
fixed namespace):

- Rules: `uuid5(NAMESPACE, f"rule:{rule_id}")`
- Cards/oracle: `uuid5(NAMESPACE, f"oracle:{oracle_id}")`
- Rulings: `uuid5(NAMESPACE, f"ruling:{oracle_id}:{index}")`, where `index`
  is the ruling's position among that `oracle_id`'s rulings in the source
  file (stable across runs since Scryfall's bulk export order is stable
  per `oracle_id`).

**Payload** on every point: `source_type` (`"rule" | "ruling" | "oracle"`),
`content_hash`, `text` (the raw source text — so retrieval needs no second
lookup), plus:

- Rules only: `rule_id`, `section_id`, `section_title`
- Rulings + oracle: `card_name`

**Chunk text sent to the embedder** (prefix + source text, never bare
source text alone, except cards):

- **Rules:** resolve each `rule_id`'s top-level ancestor chain once per
  file (build a `rule_id -> (section_id, section_title, full_chain)` map by
  walking `parent_id`, not a per-chunk tree walk), then prefix:
  `"Section 601: Casting Spells > 601.2: Playing a Spell\n"` + the rule's
  own `text`.
- **Rulings:** build an `oracle_id -> Card` lookup once from the cards
  file, then prefix: `"{card_name} — {oracle_text[:200]}\nRuling: "` +
  `comment`. A ruling whose `oracle_id` has no matching card (Scryfall
  sometimes has rulings for cards outside a given oracle_cards snapshot)
  is skipped, with a count logged in the run summary — not a crash.
- **Cards:** no prefix. Chunk text is `name`, `type_line`, `mana_cost`, and
  `oracle_text` joined plainly.

## Idempotency and batching

Qdrant is the single source of truth for what's already embedded — no
second local state store to drift out of sync.

Per source type, per batch of `retrieve_batch_size` chunks:

1. Build all `EmbeddableChunk`s for the batch (ID, payload, chunk text
   already computed).
2. `client.retrieve(ids=[...batch ids...], with_payload=["content_hash"])`.
3. For each chunk, skip it if Qdrant already has that point ID **and** its
   stored `content_hash` matches the freshly computed one. Otherwise it's
   new or changed and proceeds.
4. Batch-encode the surviving chunks'
   text with `SentenceTransformer.encode(texts, batch_size=embed_batch_size)`
   — never one embedding call per chunk.
5. Batch-upsert the resulting `PointStruct`s (vector + payload) back to
   Qdrant.
6. Tally `embedded`, `skipped_unchanged`, and `total_seen` per source type.

This means repeated runs of the full ingest→embed pipeline only pay
embedding cost for rows that are new or whose `content_hash` changed since
the last run — the stated requirement that motivates the whole idempotency
design (re-running this regularly, e.g. after each rules or Scryfall data
refresh, must not re-embed the unchanged majority of the corpus every time).

## Collection setup

On first run, if `collection_name` doesn't exist yet: probe the embedder
once (`model.encode(["x"])`) to get the real output vector dimension
(confirmed via code, never hardcoded), then `create_collection` with that
dimension and cosine distance. Single collection for all three source
types, so cross-source retrieval and payload filtering (e.g. by
`source_type`) work in one query later.

## CLI and summary output

`mtg-embed run [--source rules|rulings|cards|all] [--limit N]`

At the end of a run, print a summary: per-source-type
`embedded / skipped_unchanged / total_seen`, plus a grand total — this is
the acceptance check (point count should roughly track input row count,
minus anything skipped for an unchanged `content_hash` on a re-run, or
skipped for a ruling with no matching card).

## Testing plan (TDD)

Network- and model-free logic is unit-tested directly, using fakes/seams
so the real `SentenceTransformer` and `QdrantClient` are never required for
unit tests:

- Rules section-prefix resolution (hierarchy walk via `parent_id`).
- Ruling prefix construction and the oracle_id join, including the
  no-matching-card skip path.
- Point ID determinism: same input → same ID across repeated calls, and
  across the three source types' distinct ID schemes.
- The "which chunks need (re-)embedding" filter, given a fake existing
  `{id: content_hash}` lookup standing in for Qdrant's `retrieve()`.

`Embedder` and `QdrantStore` are thin wrapper classes so `pipeline.py`'s
orchestration logic can be tested against fakes of each. One thin
integration smoke test exercises the real model and a real local Qdrant
(via docker compose) on a `--limit`-capped slice, to prove the seam wiring
end to end — this is what verification for this task runs, not the full
~120K-chunk corpus (that full run is left for a separate, later,
possibly-backgrounded invocation).

## Dependencies

Added to `mtg-embed/pyproject.toml`: `sentence-transformers`,
`qdrant-client`, `pydantic-settings`.
