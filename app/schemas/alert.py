from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import AlertChannel, AlertSeverity, AlertStatus, AlertType
from app.schemas.common import UtcDatetime

# NOTE: there is no public AlertCreate schema -- alerts are only ever
# created by the system (detectors / scheduler), never directly by an API
# client. That creation path (and its "exactly one FK" helper) is out of
# scope for this module; see the TODO on app.models.alert.Alert.


class AlertAcknowledgeRequest(BaseModel):
    """Empty body -- `acknowledged_by` is derived from the authenticated
    JWT principal in the router handler, not client-supplied (see
    app.routers.alerts.acknowledge_alert)."""

    model_config = ConfigDict(extra="forbid")


class AlertResolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resolved_note: str | None = Field(default=None, max_length=2000)


class AlertRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    alert_type: AlertType
    severity: AlertSeverity
    entity_type: str
    server_id: int | None
    disk_id: int | None
    backup_job_id: int | None
    job_run_id: int | None
    restore_operation_id: int | None
    backup_record_id: int | None
    title: str
    message: str
    status: AlertStatus
    channel: AlertChannel
    delivered_telegram_at: UtcDatetime | None
    acknowledged_by: str | None
    acknowledged_at: UtcDatetime | None
    resolved_at: UtcDatetime | None
    resolved_note: str | None
    created_at: UtcDatetime
    updated_at: UtcDatetime
