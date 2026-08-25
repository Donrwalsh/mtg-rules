from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MTG_API_")

    qdrant_host: str = "qdrant"
    qdrant_port: int = 6333
    cors_origins: list[str] = ["http://localhost:3000"]
    broker_url: str = "redis://redis:6379/0"
    result_backend: str = "redis://redis:6379/0"


settings = Settings()
