from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import SecretStr
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
    # ONNX Runtime threads per embedding model. None uses every core; cap it
    # on a host shared with other apps.
    embed_threads: int | None = None
    hybrid_dense_weight: float = 0.5
    hybrid_sparse_weight: float = 0.5
    hybrid_top_k: int = 10
    hybrid_per_branch_limit: int = 50
    hybrid_score_threshold: float = 0.0
    # Extra hits from a rules-only hybrid search. Rules are a few percent of
    # the collection, so the mixed search rarely ranks them. 0 disables it.
    rules_top_k: int = 5
    # Google Gemini writes the answers.
    gemini_api_key: SecretStr = SecretStr("")
    gemini_model: str = "gemini-3.5-flash"
    gemini_url: str = "https://generativelanguage.googleapis.com"
    gemini_timeout_seconds: float = 60.0
    # Longest wait between two chunks of a streamed answer (the thinking
    # before the first chunk included). gemini_timeout_seconds caps the total.
    gemini_stream_chunk_timeout_seconds: float = 30.0
    postgres_dsn: str = "postgresql+psycopg://mtg:mtg@postgres:5432/mtg"
    card_ruling_limit: int = 20
    # None means "don't send it": the model's own default applies.
    # Temperature 0 so the same question and context give the same answer.
    generation_temperature: float | None = 0.0
    generation_max_tokens: int | None = None
    # Gemini 3.x thinkingConfig.thinkingLevel. None means "don't send it":
    # the model's default thinking applies. A typo here would otherwise
    # turn every generate call into a Gemini 400, burning visitor quota.
    generation_thinking_level: Literal["minimal", "low", "medium", "high"] | None = None
    # Enables per-request overrides, eval response fields and
    # GET /api/v1/config. Never on in production.
    eval_mode: bool = False
    # Enables POST /api/v1/ingest, POST /api/v1/embed and GET /api/v1/tasks.
    # Off in production, which runs no Celery worker.
    task_endpoints: bool = True
    # Longest question accepted by POST /api/v1/query (422 above it).
    max_query_chars: int = 500
    # Per-IP quotas and the global daily cap on Gemini spend. Off in dev;
    # production turns it on (and must then set both prices).
    gating_enabled: bool = False
    daily_budget_usd: float = 1.0
    # USD per million tokens; thinking tokens bill as output.
    gemini_input_price_per_mtok: float = 0.0
    gemini_output_price_per_mtok: float = 0.0
    ip_daily_llm_limit: int = 20
    ip_window_llm_limit: int = 5
    ip_window_minutes: int = 10
    # Answers being written at once, site-wide and per IP bucket. Over
    # either, a request gets a 429 before anything streams. Eval mode is
    # exempt from both; an admin only from the per-IP cap.
    max_concurrent_generations: int = 4
    max_concurrent_generations_per_ip: int = 1
    # Reuse answers to identical questions. Always bypassed in eval mode.
    answer_cache_enabled: bool = True
    # Unlocks the admin login. Empty disables it.
    admin_password: SecretStr = SecretStr("")


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
    "gemini_model",
    "generation_temperature",
    "generation_max_tokens",
    "generation_thinking_level",
)

# The subset that changes the generated answer (not the retrieved context).
GENERATION_SETTINGS: frozenset[str] = frozenset(
    {
        "gemini_model",
        "generation_temperature",
        "generation_max_tokens",
        "generation_thinking_level",
    }
)


def generator_label(s: Settings) -> str:
    """Provider-qualified model, e.g. "gemini:gemini-3.5-flash", plus the
    thinking level when one is set. Eval answer caches and the production
    answer cache are keyed on it."""
    label = f"gemini:{s.gemini_model}"
    if s.generation_thinking_level:
        label += f":think={s.generation_thinking_level}"
    return label
