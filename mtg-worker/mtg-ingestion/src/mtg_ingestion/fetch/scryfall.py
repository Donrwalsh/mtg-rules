from __future__ import annotations

from pathlib import Path
from typing import Literal, NamedTuple

import httpx

from mtg_ingestion.config import settings

BulkDataType = Literal["oracle_cards", "rulings"]


class _BulkLocation(NamedTuple):
    url: str
    filename: str  # extension reflects the wire format actually served


def _resolve_bulk_location(client: httpx.Client, data_type: BulkDataType) -> _BulkLocation:
    """Look up today's download location for a Scryfall bulk-data file.

    Scryfall migrated bulk data from a single JSON array (`download_uri`)
    to gzip-compressed JSONL (`jsonl_download_uri`) on July 20, 2026. We
    prefer the new field -- required going forward -- and fall back to the
    old one only in case a transitional response still includes it, so
    this doesn't need another edit if Scryfall's rollout wasn't instant
    everywhere.
    """
    response = client.get(settings.scryfall_bulk_data_url)
    response.raise_for_status()

    for entry in response.json()["data"]:
        if entry["type"] != data_type:
            continue
        if "jsonl_download_uri" in entry:
            return _BulkLocation(url=entry["jsonl_download_uri"], filename=f"{data_type}.jsonl.gz")
        if "download_uri" in entry:
            return _BulkLocation(url=entry["download_uri"], filename=f"{data_type}.json")
        raise RuntimeError(
            f"Scryfall bulk-data entry for type={data_type!r} has neither "
            "'jsonl_download_uri' nor 'download_uri' -- inspect the "
            "/bulk-data response directly, their API may have changed again."
        )

    raise RuntimeError(f"No Scryfall bulk-data entry found for type={data_type!r}")


def fetch_bulk_data(data_type: BulkDataType, raw_dir: Path | None = None) -> Path:
    """Stream-download a Scryfall bulk-data file (oracle cards or rulings).

    Bytes are written to disk exactly as received. Current-format files
    arrive as a real gzip archive (saved with a .jsonl.gz extension) and
    are *not* decompressed here -- httpx only auto-decodes a standard HTTP
    Content-Encoding header, and Scryfall serves this payload as
    application/gzip, i.e. the compression is the file format itself, not
    a transport-layer encoding. Decompression happens in the parse stage,
    where it can be done streaming instead of all at once.
    """
    raw_dir = raw_dir or settings.raw_dir
    raw_dir.mkdir(parents=True, exist_ok=True)

    headers = {
        "User-Agent": settings.user_agent,
        "Accept": "application/json;q=0.9,*/*;q=0.1",
    }

    with httpx.Client(
        timeout=settings.http_timeout_seconds, headers=headers, follow_redirects=True
    ) as client:
        location = _resolve_bulk_location(client, data_type)
        dest = raw_dir / location.filename

        with client.stream("GET", location.url) as response:
            response.raise_for_status()
            with dest.open("wb") as f:
                for chunk in response.iter_bytes():
                    f.write(chunk)

    return dest


def fetch_oracle_cards(raw_dir: Path | None = None) -> Path:
    return fetch_bulk_data("oracle_cards", raw_dir)


def fetch_rulings(raw_dir: Path | None = None) -> Path:
    return fetch_bulk_data("rulings", raw_dir)
