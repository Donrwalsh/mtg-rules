from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MTG_WORKER_")

    broker_url: str = "redis://redis:6379/0"
    result_backend: str = "redis://redis:6379/0"


settings = Settings()
