from datetime import datetime
from pydantic import BaseModel


class CameraCreate(BaseModel):
    device_mac: str
    onvif_host: str
    onvif_port: int = 2020
    onvif_user: str | None = None
    onvif_password: str | None = None
    rtsp_port: int = 554
    stream_profile: str = "mainStream"


class CameraUpdate(BaseModel):
    onvif_host: str | None = None
    onvif_port: int | None = None
    onvif_user: str | None = None
    onvif_password: str | None = None
    rtsp_port: int | None = None
    stream_profile: str | None = None


class CameraOut(BaseModel):
    id: int
    device_mac: str
    onvif_host: str
    onvif_port: int
    onvif_user: str | None
    rtsp_port: int
    stream_profile: str
    is_recording: bool
    created_at: datetime

    model_config = {"from_attributes": True}
