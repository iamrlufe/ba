from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import JOB_RUN_TERMINAL_STATUSES, JobRunStatus, VerificationStatus
from app.schemas._datetime import normalize_to_utc
from app.schemas.common import UtcDatetime


class JobRunTriggerRequest(BaseModel):
    """Empty body for "trigger a run now" requests. No fields (yet)."""

    model_config = ConfigDict(extra="forbid")


class JobRunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    backup_job_id: int
    triggered_by: str = Field(default="manual", min_length=1, max_length=50)


class JobRunUpdate(BaseModel):
    """Structural validation only.

    This schema cannot know the run's *current* status (Pydantic schemas
    are stateless), so it does not -- and cannot -- enforce the "no
    terminal -> PENDING/RUNNING" transition rule by itself. Callers MUST
    call `is_valid_transition(old_status, new_status)` below before
    applying this update, e.g.:

        if update.status is not None and not is_valid_transition(run.status, update.status):
            raise ValueError(...)
    """

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    status: JobRunStatus | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    file_path: str | None = Field(default=None, max_length=500)
    file_size_bytes: int | None = Field(default=None, ge=0)
    verification_status: VerificationStatus | None = None
    verification_details: str | None = None
    error_message: str | None = None
    log_output: str | None = None
    percent: int | None = Field(default=None, ge=0, le=100)
    current_file: str | None = Field(default=None, max_length=500)
    bytes_done: int | None = Field(default=None, ge=0)

    @field_validator("started_at", "finished_at")
    @classmethod
    def _normalize_started_finished_to_utc(cls, value: datetime | None) -> datetime | None:
        return normalize_to_utc(value)


class JobRunCompleteRequest(BaseModel):
    """Body for POST /api/job-runs/{id}/complete -- terminal-only.

    `status` must be one of the terminal JobRunStatus values (SUCCESS,
    WARNING, FAILED, CANCELLED); non-terminal transitions must go through
    `PATCH /api/job-runs/{id}` instead.
    """

    model_config = ConfigDict(extra="forbid")

    status: JobRunStatus
    finished_at: datetime | None = None
    file_path: str | None = Field(default=None, max_length=500)
    file_size_bytes: int | None = Field(default=None, ge=0)
    verification_status: VerificationStatus | None = None
    verification_details: str | None = None
    error_message: str | None = None
    log_output: str | None = None

    @field_validator("status")
    @classmethod
    def _status_must_be_terminal(cls, value: JobRunStatus) -> JobRunStatus:
        if value not in JOB_RUN_TERMINAL_STATUSES:
            raise ValueError(
                f"status must be one of the terminal statuses {sorted(s.value for s in JOB_RUN_TERMINAL_STATUSES)}"
            )
        return value

    @field_validator("finished_at")
    @classmethod
    def _normalize_finished_at_to_utc(cls, value: datetime | None) -> datetime | None:
        return normalize_to_utc(value)


def is_valid_transition(old_status: JobRunStatus, new_status: JobRunStatus) -> bool:
    """Whether transitioning a JobRun from old_status to new_status is allowed.

    Rule: once a run has reached a terminal status (SUCCESS, FAILED,
    WARNING, CANCELLED), it can never move back to PENDING or RUNNING.
    All other transitions are permitted here (finer-grained state-machine
    rules, if any, belong to the service layer).
    """
    if old_status in JOB_RUN_TERMINAL_STATUSES and new_status in (
        JobRunStatus.PENDING,
        JobRunStatus.RUNNING,
    ):
        return False
    return True


class JobRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    backup_job_id: int
    status: JobRunStatus
    triggered_by: str
    started_at: UtcDatetime | None
    finished_at: UtcDatetime | None
    file_path: str | None
    file_size_bytes: int | None
    duration_seconds: int | None
    verification_status: VerificationStatus
    verification_details: str | None
    error_message: str | None
    percent: int | None
    current_file: str | None
    bytes_done: int | None
    created_at: UtcDatetime
    # log_output intentionally excluded -- see JobRunLogRead.


class JobRunLogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    log_output: str | None


class JobRunSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: JobRunStatus
    started_at: UtcDatetime | None
    finished_at: UtcDatetime | None
    verification_status: VerificationStatus
