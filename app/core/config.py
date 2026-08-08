"""Application settings, loaded from environment variables / .env file."""
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

    # JWT signing secret (HS256). Must be a long random string, e.g. output
    # of `python -c "import secrets; print(secrets.token_urlsafe(48))"`.
    JWT_SECRET_KEY: str

    # JWT signing algorithm.
    JWT_ALGORITHM: str = "HS256"

    # Access token lifetime in minutes. Single-token scheme (no refresh
    # token, no revocation list).
    JWT_EXPIRE_MINUTES: int = 60

    # Bootstrap admin, consumed once at startup (see app/main.py lifespan).
    # Both must be set together; if only one is set, startup fails loudly.
    BOOTSTRAP_ADMIN_USERNAME: str | None = None
    BOOTSTRAP_ADMIN_PASSWORD: str | None = None

    # Static shared-secret key agents/schedulers present via the X-Agent-Key
    # header to call agent-driven endpoints (heartbeat, job-run lifecycle,
    # backup-record upsert). Single key for all agents (no per-agent
    # provisioning/rotation in this pass) -- deliberately simple.
    AGENT_API_KEY: str


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
