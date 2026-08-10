"""Schemas for the future standalone FTP copy-integrity agent's report
endpoint (POST /api/backup-records/{id}/report-copy-verification).

`AgentCopyVerificationStatus` is deliberately a separate enum from
`VerificationRunStatus` -- the agent must never be coupled to this
backend's internal status vocabulary. See
app/workers/copy_verification.py::map_agent_copy_status for the mapping.
"""
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AgentCopyVerificationStatus(str, Enum):
    OK = "OK"
    MISMATCH = "MISMATCH"
    MISSING_SIDECAR = "MISSING_SIDECAR"
    FILE_UNREADABLE = "FILE_UNREADABLE"


class CopyVerificationReportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: AgentCopyVerificationStatus
    actual_checksum: str | None = Field(default=None, min_length=1, max_length=128)
    checked_at: datetime

    @model_validator(mode="after")
    def _checksum_required_when_computed(self) -> "CopyVerificationReportRequest":
        needs_checksum = self.status in (
            AgentCopyVerificationStatus.OK, AgentCopyVerificationStatus.MISMATCH
        )
        if needs_checksum and self.actual_checksum is None:
            raise ValueError("actual_checksum is required when status is OK or MISMATCH")
        if not needs_checksum and self.actual_checksum is not None:
            raise ValueError(
                "actual_checksum must be omitted when status is MISSING_SIDECAR or FILE_UNREADABLE"
            )
        return self
