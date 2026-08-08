from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.enums import (
    RESTORE_TERMINAL_STATUSES,
    RequestChannel,
    RestoreMode,
    RestoreStatus,
)


class RestoreOperationBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    database_name: str = Field(min_length=1, max_length=255)
    mode: RestoreMode


class RestoreOperationCreate(RestoreOperationBase):
    backup_record_id: int
    sql_instance_id: int
    confirmation_database_name: str = Field(min_length=1, max_length=255)

    # requested_by / requested_by_channel are intentionally NOT part of the
    # request body -- they are filled in by the server from the
    # authentication context (auth module is out of scope for now).

    @model_validator(mode="after")
    def _confirmation_must_match(self) -> "RestoreOperationCreate":
        if self.confirmation_database_name != self.database_name:
            raise ValueError(
                "confirmation_database_name must exactly match database_name"
            )
        return self


class RestoreOperationUpdate(BaseModel):
    """Structural validation only, plus the CANCELLED-from-RUNNING guard.

    Like `app.schemas.job_run.JobRunUpdate`, this schema is stateless and
    cannot know the restore operation's *current* status by itself for the
    general terminal-state check, so callers MUST call
    `is_valid_transition(old_status, new_status)` before applying updates
    that change `status`. However the single most safety-critical rule --
    RUNNING can never transition to CANCELLED, because an in-progress SQL
    Server RESTORE DATABASE cannot be safely interrupted -- is captured in
    `is_valid_transition` itself and must never be bypassed.
    """

    model_config = ConfigDict(from_attributes=True, extra="forbid")

    status: RestoreStatus | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    log: str | None = None
    error_message: str | None = None


def is_valid_transition(old_status: RestoreStatus, new_status: RestoreStatus) -> bool:
    """Whether transitioning a RestoreOperation from old_status to new_status is allowed.

    Rules:
      - CANCELLED is only reachable from PENDING. In particular,
        RUNNING -> CANCELLED is forbidden (a SQL Server RESTORE DATABASE in
        progress cannot be safely interrupted without risking leaving the
        database in RESTORING/SUSPECT state).
      - Once a restore has reached a terminal status (DONE, FAILED,
        CANCELLED), it can never move back to a non-terminal status.
    """
    if new_status == RestoreStatus.CANCELLED and old_status != RestoreStatus.PENDING:
        return False
    if old_status in RESTORE_TERMINAL_STATUSES and new_status not in RESTORE_TERMINAL_STATUSES:
        return False
    return True


class RestoreOperationRead(RestoreOperationBase):
    id: int
    backup_record_id: int
    sql_instance_id: int
    server_id: int | None
    requested_by: str
    requested_by_channel: RequestChannel
    status: RestoreStatus
    requested_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    # log intentionally excluded -- see RestoreOperationLogRead.


class RestoreOperationLogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    log: str | None
