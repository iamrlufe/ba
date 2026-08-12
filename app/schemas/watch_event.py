from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import WatchEventType
from app.schemas.alert import AlertRead


class WatchEventRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_type: WatchEventType
    active: bool
    file_path: str = Field(min_length=1, max_length=500)
    detail: str | None = Field(default=None, max_length=2000)


class WatchEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    alert_raised: AlertRead | None = None
    alert_resolved: AlertRead | None = None
