from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import VerificationRunStatus, VerificationType
from app.schemas.common import UtcDatetime


class VerificationRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    backup_job_id: int
    backup_record_id: int | None
    sql_instance_id: int | None
    triggered_by: str
    verification_type: VerificationType
    status: VerificationRunStatus
    started_at: UtcDatetime | None
    finished_at: UtcDatetime | None
    msdb_backup_date: datetime | None = Field(
        default=None,
        description=(
            "Backup finish time as reported by msdb.dbo.backupset.backup_finish_date "
            "on the source SQL Server. Unlike every other datetime field in this API, "
            "this value reflects that SQL Server's OWN local clock, NOT UTC -- it is "
            "sourced from a system outside this application's control and no timezone "
            "conversion is applied (see app/core/sql_client.py). Do not assume this "
            "value is UTC when displaying it."
        ),
    )
    msdb_is_damaged: bool | None
    verifyonly_output: str | None
    error_message: str | None
    created_at: UtcDatetime
    updated_at: UtcDatetime
