from __future__ import annotations

from dataclasses import dataclass

from mtg_embed.embedder import Embedder
from mtg_embed.models import EmbeddableChunk
from mtg_embed.qdrant_store import QdrantStore


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
            vectors = embedder.encode([c.text_to_embed for c in to_embed])
            store.upsert(to_embed, vectors)
            embedded += len(to_embed)

    return RunSummary(
        source_type=source_type,
        total_seen=len(chunks),
        embedded=embedded,
        skipped_unchanged=skipped,
    )
