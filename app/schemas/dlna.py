from datetime import datetime
from pydantic import BaseModel


class DLNADeviceOut(BaseModel):
    id: int
    udn: str
    friendly_name: str | None
    device_type: str | None
    manufacturer: str | None
    model_name: str | None
    ip: str | None
    location_url: str | None
    av_transport_url: str | None
    rendering_control_url: str | None
    is_online: bool
    last_seen: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class CastRequest(BaseModel):
    device_id: int
    media_url: str


class TransportInfoOut(BaseModel):
    current_transport_state: str
    current_transport_status: str
    current_speed: str
