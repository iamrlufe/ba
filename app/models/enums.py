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


class BackupType(str, Enum):
    FULL = "FULL"
    DIFFERENTIAL = "DIFFERENTIAL"
    TRANSACTION_LOG = "TRANSACTION_LOG"
    CUSTOM = "CUSTOM"


class JobRunStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    WARNING = "WARNING"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class VerificationStatus(str, Enum):
    NOT_REQUESTED = "NOT_REQUESTED"
    PENDING = "PENDING"
    PASSED = "PASSED"
    FAILED = "FAILED"


class AlertType(str, Enum):
    JOB_FAILED = "JOB_FAILED"
    JOB_MISSED = "JOB_MISSED"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    DISK_SPACE_LOW = "DISK_SPACE_LOW"
    DISK_SPACE_CRITICAL = "DISK_SPACE_CRITICAL"
    SERVER_UNREACHABLE = "SERVER_UNREACHABLE"
    RESTORE_FAILED = "RESTORE_FAILED"


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


class UserRole(str, Enum):
    ADMIN = "ADMIN"
    OPERATOR = "OPERATOR"


# --- Terminal status sets, shared by model docstrings and Pydantic transition
# validators (app/schemas/job_run.py, app/schemas/restore_operation.py). ---

JOB_RUN_TERMINAL_STATUSES = frozenset(
    {JobRunStatus.SUCCESS, JobRunStatus.FAILED, JobRunStatus.WARNING, JobRunStatus.CANCELLED}
)

RESTORE_TERMINAL_STATUSES = frozenset(
    {RestoreStatus.DONE, RestoreStatus.FAILED, RestoreStatus.CANCELLED}
)
