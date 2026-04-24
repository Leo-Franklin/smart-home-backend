from datetime import datetime
from pydantic import BaseModel


class ScheduleCreate(BaseModel):
    camera_mac: str
    name: str | None = None
    cron_expr: str
    segment_duration: int = 1800
    enabled: bool = True


class ScheduleUpdate(BaseModel):
    name: str | None = None
    cron_expr: str | None = None
    segment_duration: int | None = None
    enabled: bool | None = None


class ScheduleOut(BaseModel):
    id: int
    camera_mac: str
    name: str | None
    cron_expr: str
    segment_duration: int
    enabled: bool
    created_at: datetime
    updated_at: datetime | None

    model_config = {"from_attributes": True}
