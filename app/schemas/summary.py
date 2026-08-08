from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.models.enums import JobRunStatus
from app.schemas.alert import AlertRead

DailyJobStatusValue = Literal["OK", "FAIL", "MISSED"]


class DailyJobStatus(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    backup_job_id: int
    name: str
    status: DailyJobStatusValue
    last_run_id: int | None
    last_run_status: JobRunStatus | None
    last_run_finished_at: datetime | None


class DailySummaryCounts(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    active_alerts_total: int
    jobs_ok: int
    jobs_fail: int
    jobs_missed: int


class DailySummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    generated_at: datetime
    window_start: datetime
    window_end: datetime
    active_alerts: list[AlertRead]
    jobs: list[DailyJobStatus]
    counts: DailySummaryCounts
