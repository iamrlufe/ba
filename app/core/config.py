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

    # How often (seconds) the background alert-detection worker
    # (app/workers/alert_worker.py) runs its three periodic checks (missed
    # runs, agent-offline, job timeouts). Independent of the once-daily
    # summary cadence below.
    ALERT_WORKER_TICK_INTERVAL_SECONDS: int = 300

    # Whether the background alert-detection worker loop is started at all
    # (see app/main.py lifespan). Set to False to run the API with no
    # background task (e.g. a secondary/read replica process).
    ALERT_WORKER_ENABLED: bool = True

    # Minutes of Server.last_seen_at staleness before the worker marks a
    # server OFFLINE and raises an AGENT_OFFLINE alert. Distinct from the
    # per-job missed_run_grace_minutes column, which governs JOB_MISSED
    # detection instead (see app/models/backup_job.py).
    AGENT_OFFLINE_THRESHOLD_MINUTES: int = 10

    # Hour/minute (UTC, 24h) at which the worker builds and logs the
    # once-daily alert/job-status summary (app/workers/daily_summary.py).
    # GET /api/summary/daily always computes a fresh snapshot on demand
    # regardless of this schedule -- these only gate the worker's own
    # once-a-day background invocation.
    DAILY_SUMMARY_HOUR_UTC: int = 8
    DAILY_SUMMARY_MINUTE_UTC: int = 0

    # Lifetime (minutes) of a bot-scoped JWT minted by
    # POST /api/auth/telegram-link (app/core/auth.py::create_bot_access_token).
    # Deliberately long-lived (default 30 days) by design, since the
    # Telegram bot is a separate always-on process, not a short-lived
    # interactive session. Revocation: only User.is_active=False reliably
    # blocks a bot-scoped token (get_current_user re-checks is_active live
    # on every request, for EVERY token that user has ever been issued).
    # Re-running /link mints a fresh token and overwrites
    # User.telegram_bot_token_encrypted, but does NOT invalidate the
    # previous token -- nothing compares a presented JWT against that
    # stored value, so an old token remains valid until its own natural
    # expiry even after re-linking. This is the existing no-revocation-list
    # JWT scheme (see app/core/auth.py module docstring) applied as-is to
    # the long-lived bot token; "re-link to revoke" is not a real
    # mitigation and must not be told to users as one.
    BOT_TOKEN_EXPIRE_MINUTES: int = 43200  # 30 days

    # Whether the background alert-detection worker (via
    # _maybe_run_backup_verifications) drives SQL Server backup
    # verification at all (app/workers/backup_verification.py). Does NOT
    # gate POST /api/backup-jobs/{id}/verify, which always runs on demand
    # regardless of this flag -- mirrors the DAILY_SUMMARY_* /
    # GET /api/summary/daily split above.
    BACKUP_VERIFICATION_ENABLED: bool = True

    # How often (seconds) the background worker sweeps every enabled,
    # sql_instance_id-set BackupJob and runs a verification
    # (check_backup_verifications). Deliberately a long, once-a-day-ish
    # default -- RESTORE VERIFYONLY is expensive on the SQL Server side.
    BACKUP_VERIFICATION_INTERVAL_SECONDS: int = 86400

    # Timeout (seconds) for establishing the SQL Server connection used for
    # both the msdb.dbo.backupset query and RESTORE VERIFYONLY (see
    # app/core/sql_client.py::SqlConnectionParams.connect_timeout_seconds).
    BACKUP_VERIFICATION_CONNECT_TIMEOUT_SECONDS: int = 30

    # Timeout (seconds) for the RESTORE VERIFYONLY statement itself --
    # can legitimately take a long time for large backup files.
    BACKUP_VERIFICATION_RESTORE_TIMEOUT_SECONDS: int = 1800

    # Extra grace period (seconds), on top of
    # BACKUP_VERIFICATION_CONNECT_TIMEOUT_SECONDS +
    # BACKUP_VERIFICATION_RESTORE_TIMEOUT_SECONDS, before
    # check_stuck_verifications forcibly marks a RUNNING VerificationRun as
    # ERROR (process crash/restart mid-verification -- see that function's
    # docstring in app/workers/backup_verification.py).
    BACKUP_VERIFICATION_STUCK_RUN_GRACE_SECONDS: int = 900


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
