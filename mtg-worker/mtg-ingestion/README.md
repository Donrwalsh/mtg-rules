# mtg-ingestion

Fetches and parses the three raw data sources the MTG rules app needs:

- **Comprehensive Rules** (Wizards) — numbered rules and subrules
- **Oracle card text** (Scryfall `oracle_cards` bulk data) — one record per unique card
- **Rulings** (Scryfall `rulings` bulk data) — linked to cards by `oracle_id`

This is the fetch+parse stage only. Output is JSONL in `data/parsed/` — no
database, no embeddings yet. That's the next stage, built on top of this one.

## Setup (Docker -- recommended)

No local Python environment needed. From this directory:

```bash
docker compose build
docker compose run --rm ingestion run-all
```

Or run stages individually, useful for retrying just one source:

```bash
docker compose run --rm ingestion fetch-rules
docker compose run --rm ingestion fetch-cards
docker compose run --rm ingestion fetch-rulings

docker compose run --rm ingestion parse-rules
docker compose run --rm ingestion parse-cards
docker compose run --rm ingestion parse-rulings
```

`./data` is mounted into the container, so raw downloads and parsed JSONL
land on your host filesystem exactly as if you'd run it locally --
`data/raw/` for downloads, `data/parsed/` for output, one file per source
per day (e.g. `rules_2026-08-25.jsonl`).

Running tests still needs a local install (the image doesn't include
`pytest` -- see below).

## Setup (local venv -- alternative)

```bash
python3 -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

```bash
mtg-ingest run-all
```

Run the test suite (parsing logic only -- no network needed):

```bash
python -m pytest -v
```

## Notes on the two live fetches

- **Rules**: there's no stable "latest.txt" URL from Wizards — the file is
  dated and the link on https://magic.wizards.com/en/rules changes every
  time a new version ships. `fetch_rules_text` scrapes that page for the
  current link on every run rather than hardcoding a filename.
- **Scryfall**: bulk-data download URLs rotate on every refresh (roughly
  daily). `fetch_bulk_data` always hits Scryfall's `/bulk-data` listing
  endpoint first and follows whatever URL it returns that day.

Both live network calls need to run from an environment that can actually
reach `magic.wizards.com` and `api.scryfall.com` — they weren't reachable
from the sandbox this was scaffolded in, so exercise them from your own
machine before wiring up a scheduler.

## Known gaps (by design, for now)

- The rules parser stops before the Glossary section — glossary term
  lookups are a deliberate follow-up, not part of this MVP.
- Per-printing card data (set, collector number, art) is intentionally not
  modeled — only oracle-level card identity, since this is a rules Q&A
  project, not a collection tracker.
- `IngestionRun` (in `models.py`) is defined but not yet written anywhere —
  it's there so the diff/persistence stage can adopt it without changing
  the model shape.

## Next stage (not built yet)

Diff parsed JSONL against what's already in Postgres (via each record's
`content_hash`), embed only new/changed records, and upsert into pgvector.
