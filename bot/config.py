"""Bot-only settings, loaded from `bot/.env`.

Deliberately a separate `BotSettings` class with its own `env_file`, NOT
merged into `app.core.config.Settings` -- see `bot/crypto.py`'s module
docstring for the full rationale (importing anything under `app.core`
would force the bot's env to carry secrets it has no functional need for,
e.g. `JWT_SECRET_KEY`/`AGENT_API_KEY`, widening its trust boundary).
"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class BotSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file="bot/.env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Telegram Bot API token, from @BotFather. Never log this value.
    TELEGRAM_BOT_TOKEN: str

    # Base URL of the Backup Orchestrator API this bot talks to.
    BOT_API_BASE_URL: str = "http://localhost:8000"

    # Fernet key used to encrypt/decrypt the locally-cached bearer tokens
    # (bot/crypto.py, bot/auth_store.py). Must be the SAME key the API
    # server uses for FERNET_KEY (app/core/config.py) -- this bot process
    # only ever decrypts tokens it itself encrypted after a successful
    # /telegram-link call, but sharing the key keeps both sides of the
    # system on one auditable secret.
    FERNET_KEY: str

    # How often (seconds) the background alert-poller (bot/poller.py)
    # checks GET /api/alerts for new push-eligible alerts.
    BOT_ALERT_POLL_INTERVAL_SECONDS: int = 60

    # Per-request HTTP timeout (seconds) for calls to the Backup
    # Orchestrator API (bot/api_client.py).
    BOT_HTTP_TIMEOUT_SECONDS: float = 15.0

    # Local sqlite file backing bot/auth_store.py (linked_users +
    # notification_state tables). Needs the same operational care as the
    # API's own DB file (backup/retention, filesystem permissions) --
    # losing it forces every linked user to /link again and resets the
    # alert-poll high-water-mark (which would re-notify every still-ACTIVE
    # alert on the next tick).
    BOT_STATE_DB_PATH: str = "./bot_state.db"

    # How long (seconds) a pending /restore confirmation stays valid before
    # it silently expires (bot/handlers/restore.py, bot/state.py).
    BOT_RESTORE_CONFIRMATION_TTL_SECONDS: int = 300

    # How many mismatched confirmation replies are tolerated before a
    # pending /restore is auto-cancelled.
    BOT_RESTORE_CONFIRMATION_MAX_ATTEMPTS: int = 3

    # Optional comma-separated allowlist of Telegram chat ids permitted to
    # interact with this bot at all (e.g. "123456789,-100987654321"). Unset
    # (None) by default -- every chat is allowed; only tighten this if the
    # bot token itself might be exposed to untrusted chats.
    BOT_ALLOWED_CHAT_IDS: str | None = None

    BOT_LOG_LEVEL: str = "INFO"


@lru_cache
def get_bot_settings() -> BotSettings:
    return BotSettings()


settings = get_bot_settings()
