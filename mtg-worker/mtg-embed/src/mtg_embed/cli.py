from __future__ import annotations

from pathlib import Path

import typer

from mtg_embed.config import settings
from mtg_embed.pipeline import RunSummary, embed_and_store, prune_stale
from mtg_embed.sources.cards import load_card_chunks
from mtg_embed.sources.rules import load_rule_chunks
from mtg_embed.sources.rulings import load_ruling_chunks

app = typer.Typer(help="Embed the parsed MTG rules/cards/rulings corpus into Qdrant.")

_SOURCES = ("rules", "cards", "rulings")


def _latest(directory: Path, pattern: str) -> Path:
    matches = sorted(directory.glob(pattern))
    if not matches:
        raise FileNotFoundError(f"No files matching {pattern!r} in {directory}")
    return matches[-1]


def _format_summary_line(
    source_name: str, summary: RunSummary, pruned_skipped: bool = False
) -> str:
    """Format a summary line for display, using source_name even when summary.source_type is empty."""
    pruned = "skipped" if pruned_skipped else summary.pruned
    return (
        f"  {source_name}: embedded={summary.embedded} payload_updated={summary.payload_updated} "
        f"skipped_unchanged={summary.skipped_unchanged} pruned={pruned} "
        f"total_seen={summary.total_seen}"
    )


@app.command("run")
def run(
    source: str = typer.Option("all", help="rules|cards|rulings|all"),
    limit: int | None = typer.Option(
        None, help="Cap rows read per source, for cheap verification runs."
    ),
) -> None:
    sources = _SOURCES if source == "all" else (source,)
    for name in sources:
        if name not in _SOURCES:
            raise typer.BadParameter(
                f"Unknown source {name!r}; expected one of {_SOURCES} or 'all'."
            )

    # Imported here, not at module top, so argument validation above never
    # requires the model weights or a Qdrant connection.
    from qdrant_client import QdrantClient

    from mtg_embed.embedder import load_sentence_transformer_embedder
    from mtg_embed.qdrant_store import QdrantStore
    from mtg_embed.sparse_embedder import load_bm25_sparse_embedder

    client = QdrantClient(
        host=settings.qdrant_host, port=settings.qdrant_port, timeout=settings.qdrant_timeout
    )
    store = QdrantStore(client, settings.collection_name)
    embedder = load_sentence_transformer_embedder(settings.model_name, settings.embed_batch_size)
    sparse_embedder = load_bm25_sparse_embedder(settings.sparse_model_name)
    store.ensure_collection(embedder.vector_size)

    summaries = []
    skipped_no_card = 0
    # A --limit run sees only the first rows of each source; pruning then
    # would delete everything after them.
    prune = limit is None

    def process(name: str, chunks: list) -> None:
        summary = embed_and_store(
            chunks, store, embedder, sparse_embedder, settings.retrieve_batch_size
        )
        if prune:
            summary.pruned = prune_stale(chunks, store)
        summaries.append((name, summary))

    if "rules" in sources:
        rules_path = _latest(settings.parsed_dir, "rules_*.jsonl")
        process("rules", load_rule_chunks(rules_path, limit=limit))

    if "cards" in sources:
        cards_path = _latest(settings.parsed_dir, "cards_*.jsonl")
        process("cards", load_card_chunks(cards_path, limit=limit))

    if "rulings" in sources:
        rulings_path = _latest(settings.parsed_dir, "rulings_*.jsonl")
        cards_path = _latest(settings.parsed_dir, "cards_*.jsonl")
        chunks, skipped_no_card = load_ruling_chunks(rulings_path, cards_path, limit=limit)
        process("rulings", chunks)

    typer.echo("")
    typer.echo("Embedding summary:")
    grand_total = grand_embedded = grand_skipped = grand_repayloaded = grand_pruned = 0
    for source_name, s in summaries:
        typer.echo(_format_summary_line(source_name, s, pruned_skipped=not prune))
        grand_total += s.total_seen
        grand_embedded += s.embedded
        grand_skipped += s.skipped_unchanged
        grand_repayloaded += s.payload_updated
        grand_pruned += s.pruned
    if skipped_no_card:
        typer.echo(f"  rulings skipped (no matching card): {skipped_no_card}")
    typer.echo(
        f"  TOTAL: embedded={grand_embedded} payload_updated={grand_repayloaded} "
        f"skipped_unchanged={grand_skipped} pruned={grand_pruned if prune else 'skipped'} "
        f"total_seen={grand_total}"
    )


if __name__ == "__main__":
    app()
