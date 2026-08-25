from __future__ import annotations

from pathlib import Path

import typer

from mtg_embed.config import settings
from mtg_embed.pipeline import embed_and_store
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


@app.command("run")
def run(
    source: str = typer.Option("all", help="rules|cards|rulings|all"),
    limit: int | None = typer.Option(None, help="Cap rows read per source, for cheap verification runs."),
) -> None:
    sources = _SOURCES if source == "all" else (source,)
    for name in sources:
        if name not in _SOURCES:
            raise typer.BadParameter(f"Unknown source {name!r}; expected one of {_SOURCES} or 'all'.")

    # Imported here, not at module top, so argument validation above never
    # requires the model weights or a Qdrant connection.
    from qdrant_client import QdrantClient

    from mtg_embed.embedder import load_sentence_transformer_embedder
    from mtg_embed.qdrant_store import QdrantStore

    client = QdrantClient(host=settings.qdrant_host, port=settings.qdrant_port)
    store = QdrantStore(client, settings.collection_name)
    embedder = load_sentence_transformer_embedder(settings.model_name, settings.embed_batch_size)
    store.ensure_collection(embedder.vector_size)

    summaries = []
    skipped_no_card = 0

    if "rules" in sources:
        rules_path = _latest(settings.parsed_dir, "rules_*.jsonl")
        chunks = load_rule_chunks(rules_path, limit=limit)
        summaries.append(embed_and_store(chunks, store, embedder, settings.retrieve_batch_size))

    if "cards" in sources:
        cards_path = _latest(settings.parsed_dir, "cards_*.jsonl")
        chunks = load_card_chunks(cards_path, limit=limit)
        summaries.append(embed_and_store(chunks, store, embedder, settings.retrieve_batch_size))

    if "rulings" in sources:
        rulings_path = _latest(settings.parsed_dir, "rulings_*.jsonl")
        cards_path = _latest(settings.parsed_dir, "cards_*.jsonl")
        chunks, skipped_no_card = load_ruling_chunks(rulings_path, cards_path, limit=limit)
        summaries.append(embed_and_store(chunks, store, embedder, settings.retrieve_batch_size))

    typer.echo("")
    typer.echo("Embedding summary:")
    grand_total = grand_embedded = grand_skipped = 0
    for s in summaries:
        typer.echo(
            f"  {s.source_type}: embedded={s.embedded} skipped_unchanged={s.skipped_unchanged} "
            f"total_seen={s.total_seen}"
        )
        grand_total += s.total_seen
        grand_embedded += s.embedded
        grand_skipped += s.skipped_unchanged
    if skipped_no_card:
        typer.echo(f"  rulings skipped (no matching card): {skipped_no_card}")
    typer.echo(f"  TOTAL: embedded={grand_embedded} skipped_unchanged={grand_skipped} total_seen={grand_total}")


if __name__ == "__main__":
    app()
