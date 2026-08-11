from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.common import UtcDatetime


class BackupRecordBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    file_name: str = Field(min_length=1, max_length=255)
    remote_path: str = Field(min_length=1, max_length=500)
    file_size_bytes: int = Field(ge=0)
    checksum: str | None = Field(default=None, max_length=128)
    checksum_algorithm: str | None = Field(default=None, max_length=20)

    @model_validator(mode="after")
    def _checksum_fields_together(self) -> "BackupRecordBase":
        has_checksum = self.checksum is not None
        has_algorithm = self.checksum_algorithm is not None
        if has_checksum != has_algorithm:
            raise ValueError(
                "checksum and checksum_algorithm must both be set or both be omitted"
            )
        return self


class BackupRecordSyncItem(BackupRecordBase):
    job_run_id: int | None = None


class BackupRecordCreate(BackupRecordSyncItem):
    backup_job_id: int


class BackupRecordChecksumUpdate(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    checksum: str = Field(min_length=1, max_length=128)
    checksum_algorithm: str = Field(min_length=1, max_length=20)


class BackupRecordRead(BackupRecordBase):
    id: int
    backup_job_id: int
    job_run_id: int | None
    detected_at: UtcDatetime
    updated_at: UtcDatetime


class BackupRecordSyncResult(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    created: int
    updated: int
    items: list[BackupRecordRead]
