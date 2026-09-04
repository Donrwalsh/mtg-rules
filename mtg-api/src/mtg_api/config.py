from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MTG_API_")

    qdrant_host: str = "qdrant"
    qdrant_port: int = 6333
    cors_origins: list[str] = ["http://localhost:3000"]
    broker_url: str = "redis://redis:6379/0"
    result_backend: str = "redis://redis:6379/0"
    collection_name: str = "mtg_rules"
    parsed_dir: Path = Path("../mtg-worker/mtg-ingestion/data/parsed")
    dense_model_name: str = "BAAI/bge-base-en-v1.5"
    sparse_model_name: str = "Qdrant/bm25"
    hybrid_dense_weight: float = 0.5
    hybrid_sparse_weight: float = 0.5
    hybrid_top_k: int = 10
    hybrid_per_branch_limit: int = 50
    hybrid_score_threshold: float = 0.0


settings = Settings()
