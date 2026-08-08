"""Application settings, loaded from environment variables / .env file.

Only the settings needed by the models/schemas layer are defined here
(DATABASE_URL, FERNET_KEY). Anything HTTP/auth-related belongs to a future
module.
"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # SQLAlchemy async URL, e.g. "sqlite+aiosqlite:///./backup_orchestrator.db"
    DATABASE_URL: str = "sqlite+aiosqlite:///./backup_orchestrator.db"

    # Fernet key used to encrypt/decrypt secrets at rest (app/core/security.py).
    # Must be a urlsafe-base64-encoded 32-byte key, e.g. output of
    # `cryptography.fernet.Fernet.generate_key()`.
    # NOTE: no insecure default is provided on purpose -- a missing key must
    # fail loudly rather than silently encrypting with a well-known value.
    FERNET_KEY: str

    # Echo SQL statements (debugging only, never enable in production since
    # it can log parameter values).
    SQL_ECHO: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
