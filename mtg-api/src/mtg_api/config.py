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
    # Extra hits from a rules-only hybrid search. Rules are a few percent of
    # the collection, so the mixed search rarely ranks them. 0 disables it.
    rules_top_k: int = 0
    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-120b"
    ollama_url: str = "http://host.docker.internal:11434"
    ollama_model: str = "phi4"
    postgres_dsn: str = "postgresql+psycopg://mtg:mtg@postgres:5432/mtg"
    card_ruling_limit: int = 20
    # None means "don't send it": the model's own default applies.
    generation_temperature: float | None = None
    generation_max_tokens: int | None = None
    # Enables per-request overrides, eval response fields and
    # GET /api/v1/config. Never on in production.
    eval_mode: bool = False


settings = Settings()

# Settings an eval-mode request may override for itself (never globally).
OVERRIDABLE_SETTINGS: tuple[str, ...] = (
    "hybrid_dense_weight",
    "hybrid_sparse_weight",
    "hybrid_top_k",
    "hybrid_per_branch_limit",
    "hybrid_score_threshold",
    "rules_top_k",
    "card_ruling_limit",
    "collection_name",
    "ollama_model",
    "generation_temperature",
    "generation_max_tokens",
)

# The subset that changes the generated answer (not the retrieved context).
GENERATION_SETTINGS: frozenset[str] = frozenset(
    {"ollama_model", "generation_temperature", "generation_max_tokens"}
)
