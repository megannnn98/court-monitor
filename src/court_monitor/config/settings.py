"""Environment-based settings (prefix CM_). No secrets in the repo."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CM_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Core
    database_url: str = "sqlite:///./court_monitor.db"
    log_level: str = "INFO"
    config_dir: str = "config"

    # HTTP politeness / identity
    http_user_agent: str = (
        "court-monitor/0.1 (+https://example.invalid; contact: ops@example.invalid)"
    )
    http_timeout: float = 20.0
    http_per_host_concurrency: int = 2
    http_delay_seconds: float = 1.5
    http_max_retries: int = 3

    # Airtable (read_only until mapping validated — see docs/airtable-discovery.md)
    airtable_mode: str = "read_only"
    airtable_token: str | None = None

    # LLM (disabled by default; extraction uses deterministic rules)
    llm_mode: str = "disabled"
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str | None = None

    # Name NER (disabled by default; opt-in via CM_NER_MODE=spacy — requires
    # the `nlp` extra and `python -m spacy download ru_core_news_lg`)
    ner_mode: str = "disabled"

    @property
    def config_path(self) -> Path:
        return Path(self.config_dir)

    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


# Convenient module-level singleton imported across the package.
settings = get_settings()
