"""Pydantic settings — fail loud on missing required config."""

from __future__ import annotations

from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment / .env file.

    Raises:
        ValidationError: On any missing required field or type mismatch.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    padres_db_path: Path = Path("data/duckdb/padres.db")
    padres_trades_db_path: Path | None = None
    log_level: str = "INFO"

    # X/Twitter (phase 3)
    x_api_key: str | None = None
    x_api_secret: str | None = None
    x_access_token: str | None = None
    x_access_secret: str | None = None
    x_bearer_token: str | None = None

    @field_validator("padres_db_path", mode="before")
    @classmethod
    def _ensure_db_suffix(cls, v: object) -> object:
        p = Path(str(v))
        if p.suffix != ".db":
            raise ValueError(f"padres_db_path must end in .db, got: {p}")
        return v


_settings: Settings | None = None


def get_settings() -> Settings:
    """Return cached Settings instance (lazy init)."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
