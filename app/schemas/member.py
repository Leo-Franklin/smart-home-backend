from datetime import datetime
from pydantic import BaseModel
from app.schemas.device import DeviceOut


class MemberCreate(BaseModel):
    name: str
    avatar_url: str | None = None
    webhook_url: str | None = None


class MemberUpdate(BaseModel):
    name: str | None = None
    avatar_url: str | None = None
    webhook_url: str | None = None


class MemberOut(BaseModel):
    id: int
    name: str
    avatar_url: str | None
    webhook_url: str | None
    is_home: bool
    last_arrived_at: datetime | None
    last_left_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class MemberDeviceCreate(BaseModel):
    mac: str
    label: str | None = None


class MemberDeviceOut(BaseModel):
    id: int
    member_id: int
    mac: str
    label: str | None
    device_info: DeviceOut | None = None

    model_config = {"from_attributes": True}


class PresenceLogOut(BaseModel):
    id: int
    member_id: int
    event: str
    triggered_by_mac: str | None
    occurred_at: datetime

    model_config = {"from_attributes": True}
