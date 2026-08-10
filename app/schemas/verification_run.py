from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.enums import VerificationRunStatus, VerificationType


class VerificationRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    backup_job_id: int
    backup_record_id: int | None
    sql_instance_id: int | None
    triggered_by: str
    verification_type: VerificationType
    status: VerificationRunStatus
    started_at: datetime | None
    finished_at: datetime | None
    msdb_backup_date: datetime | None
    msdb_is_damaged: bool | None
    verifyonly_output: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime
