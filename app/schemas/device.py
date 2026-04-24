from datetime import datetime
from pydantic import BaseModel


class DeviceBase(BaseModel):
    alias: str | None = None
    device_type: str = "unknown"
    notes: str | None = None


class DeviceUpdate(DeviceBase):
    pass


class DeviceOut(BaseModel):
    id: int
    mac: str
    ip: str | None
    vendor: str | None
    device_type: str
    alias: str | None
    is_online: bool
    last_seen: datetime | None
    created_at: datetime
    updated_at: datetime | None
    notes: str | None

    model_config = {"from_attributes": True}
