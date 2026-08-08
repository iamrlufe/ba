from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import JOB_RUN_TERMINAL_STATUSES, JobRunStatus, VerificationStatus


class JobRunTriggerRequest(BaseModel):
    """Empty body for "trigger a run now" requests. No fields (yet)."""

    model_config = ConfigDict(extra="forbid")


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
    started_at: datetime | None
    finished_at: datetime | None
    file_path: str | None
    file_size_bytes: int | None
    duration_seconds: int | None
    verification_status: VerificationStatus
    verification_details: str | None
    error_message: str | None
    created_at: datetime
    # log_output intentionally excluded -- see JobRunLogRead.


class JobRunLogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    log_output: str | None


class JobRunSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: JobRunStatus
    started_at: datetime | None
    finished_at: datetime | None
    verification_status: VerificationStatus
