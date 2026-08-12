"""All enum types shared across models and schemas.

Stored in SQLite as VARCHAR (native_enum=False) via the SQLAlchemy Enum
type wherever used on a model column -- see the `values_callable` pattern
used consistently in each model module.
"""
from enum import Enum


class ProtocolType(str, Enum):
    FTP = "FTP"
    SFTP = "SFTP"


class ServerStatus(str, Enum):
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"
    UNREACHABLE = "UNREACHABLE"
    OFFLINE = "OFFLINE"


class BackupType(str, Enum):
    FULL = "FULL"
    DIFFERENTIAL = "DIFFERENTIAL"
    TRANSACTION_LOG = "TRANSACTION_LOG"
    CUSTOM = "CUSTOM"


class TriggerMode(str, Enum):
    SCHEDULE = "SCHEDULE"
    WATCH = "WATCH"


class WatchEventType(str, Enum):
    FILE_LOCK_TIMEOUT = "FILE_LOCK_TIMEOUT"


class JobRunStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    WARNING = "WARNING"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    TIMEOUT = "TIMEOUT"


class VerificationStatus(str, Enum):
    NOT_REQUESTED = "NOT_REQUESTED"
    PENDING = "PENDING"
    PASSED = "PASSED"
    FAILED = "FAILED"


class AlertType(str, Enum):
    JOB_FAILED = "JOB_FAILED"
    JOB_MISSED = "JOB_MISSED"
    JOB_TIMEOUT = "JOB_TIMEOUT"
    # NOTE: VERIFICATION_FAILED is reserved for a different, unrelated
    # concept -- JobRun.verification_status, agent-self-reported -- and is
    # untouched by app/workers/backup_verification.py. That module raises
    # BACKUP_VERIFICATION_FAILED instead; do not conflate the two.
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    BACKUP_VERIFICATION_FAILED = "BACKUP_VERIFICATION_FAILED"
    # Deliberately NOT reusing BACKUP_VERIFICATION_FAILED: that literal is
    # reserved for the RESTORE_VERIFYONLY flow (see docstring above); this
    # one is raised only by app.workers.copy_verification (FTP copy-integrity
    # checks reported by the future standalone agent).
    FTP_COPY_INTEGRITY_FAILED = "FTP_COPY_INTEGRITY_FAILED"
    DISK_SPACE_LOW = "DISK_SPACE_LOW"
    DISK_SPACE_CRITICAL = "DISK_SPACE_CRITICAL"
    SERVER_UNREACHABLE = "SERVER_UNREACHABLE"
    AGENT_OFFLINE = "AGENT_OFFLINE"
    RESTORE_FAILED = "RESTORE_FAILED"
    WATCH_FILE_LOCK_TIMEOUT = "WATCH_FILE_LOCK_TIMEOUT"


class AlertSeverity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class AlertStatus(str, Enum):
    ACTIVE = "ACTIVE"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RESOLVED = "RESOLVED"


class AlertChannel(str, Enum):
    TELEGRAM = "TELEGRAM"
    WEB = "WEB"
    BOTH = "BOTH"


class RestoreMode(str, Enum):
    ALL = "ALL"
    EXISTING = "EXISTING"
    MISSING = "MISSING"


class RestoreStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    DONE = "DONE"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class RequestChannel(str, Enum):
    WEB = "WEB"
    TELEGRAM = "TELEGRAM"


class VerificationRunStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    OK = "OK"
    CORRUPT = "CORRUPT"
    MISSING = "MISSING"
    ERROR = "ERROR"


class UserRole(str, Enum):
    ADMIN = "ADMIN"
    OPERATOR = "OPERATOR"


class AgentCredentialAccessAuthMethod(str, Enum):
    """How a caller authenticated to
    `GET /api/agents/{server_id}/connection-config` -- backs
    `AgentCredentialAccessLog.auth_method` (app/models/agent_credential_access_log.py).
    """

    CONNECTION_CONFIG_KEY = "connection_config_key"
    ADMIN_JWT = "admin_jwt"


class AgentCredentialAccessOutcome(str, Enum):
    """Result of a call to
    `GET /api/agents/{server_id}/connection-config` -- backs
    `AgentCredentialAccessLog.outcome`. Exactly one row is written per
    call, regardless of outcome (success or any denial).
    """

    SUCCESS = "success"
    DENIED_DISABLED = "denied_disabled"
    DENIED_DELETED = "denied_deleted"
    DENIED_NO_CREDENTIALS = "denied_no_credentials"
    NOT_FOUND = "not_found"
    UNAUTHORIZED = "unauthorized"
    DECRYPTION_FAILED = "decryption_failed"


class VerificationType(str, Enum):
    """Discriminates what kind of check a VerificationRun row represents.

    RESTORE_VERIFYONLY: app/workers/backup_verification.py's SQL Server
    RESTORE VERIFYONLY flow (the only kind that existed before this enum
    was introduced -- every pre-existing row backfills to this value).
    FTP_COPY_INTEGRITY: a filesystem-level SHA-256 comparison against a
    `<file>.sha256` sidecar, reported by a future standalone agent via
    app/workers/copy_verification.py.
    """

    RESTORE_VERIFYONLY = "RESTORE_VERIFYONLY"
    FTP_COPY_INTEGRITY = "FTP_COPY_INTEGRITY"


# --- Terminal status sets, shared by model docstrings and Pydantic transition
# validators (app/schemas/job_run.py, app/schemas/restore_operation.py). ---

JOB_RUN_TERMINAL_STATUSES = frozenset(
    {
        JobRunStatus.SUCCESS,
        JobRunStatus.FAILED,
        JobRunStatus.WARNING,
        JobRunStatus.CANCELLED,
        JobRunStatus.TIMEOUT,
    }
)

RESTORE_TERMINAL_STATUSES = frozenset(
    {RestoreStatus.DONE, RestoreStatus.FAILED, RestoreStatus.CANCELLED}
)

VERIFICATION_RUN_TERMINAL_STATUSES = frozenset(
    {
        VerificationRunStatus.OK,
        VerificationRunStatus.CORRUPT,
        VerificationRunStatus.MISSING,
        VerificationRunStatus.ERROR,
    }
)
