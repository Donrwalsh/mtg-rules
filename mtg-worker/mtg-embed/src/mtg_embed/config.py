from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All configuration lives here so the rest of the pipeline stays pure and testable.

    Override any of these with an env var, e.g. MTG_EMBED_QDRANT_HOST=qdrant.
    """

    model_config = SettingsConfigDict(env_prefix="MTG_EMBED_")

    qdrant_host: str = "localhost"
    qdrant_port: int = 6333
    collection_name: str = "mtg_rules"
    parsed_dir: Path = Path("../mtg-ingestion/data/parsed")
    model_name: str = "BAAI/bge-base-en-v1.5"
    embed_batch_size: int = 32
    retrieve_batch_size: int = 256


settings = Settings()
