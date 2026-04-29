from datetime import datetime
from pydantic import BaseModel, field_validator


class CameraCreate(BaseModel):
    device_mac: str
    onvif_host: str
    onvif_port: int = 2020

    @field_validator("onvif_host")
    @classmethod
    def onvif_host_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("onvif_host 不能为空")
        return v.strip()
    onvif_user: str | None = None
    onvif_password: str | None = None
    rtsp_port: int = 554
    rtsp_url: str | None = None
    stream_profile: str = "mainStream"


class CameraUpdate(BaseModel):
    onvif_host: str | None = None
    onvif_port: int | None = None
    onvif_user: str | None = None
    onvif_password: str | None = None
    rtsp_port: int | None = None
    rtsp_url: str | None = None
    stream_profile: str | None = None
    auto_cast_dlna: str | None = None


class CameraOut(BaseModel):
    id: int
    device_mac: str
    onvif_host: str
    onvif_port: int
    onvif_user: str | None
    rtsp_port: int
    rtsp_url: str | None
    stream_profile: str
    is_recording: bool
    is_online: bool
    last_probe_at: datetime | None
    auto_cast_dlna: str | None
    created_at: datetime

    model_config = {"from_attributes": True}
