from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All configuration lives here so fetch/parse/storage stay pure and testable.

    Override any of these with an env var, e.g. MTG_INGEST_DATA_DIR=/srv/data.
    """

    model_config = SettingsConfigDict(env_prefix="MTG_INGEST_")

    data_dir: Path = Path("data")
    rules_page_url: str = "https://magic.wizards.com/en/rules"
    scryfall_bulk_data_url: str = "https://api.scryfall.com/bulk-data"
    http_timeout_seconds: float = 60.0

    # Scryfall's API guidelines ask API clients to identify themselves;
    # Wizards doesn't require it, but it's a cheap courtesy to send everywhere.
    user_agent: str = "mtg-rules-ingestion/0.1 (personal portfolio project)"

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def parsed_dir(self) -> Path:
        return self.data_dir / "parsed"


settings = Settings()
