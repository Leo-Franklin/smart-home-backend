from datetime import datetime
from sqlalchemy import String, Boolean, Integer, DateTime, Text, func, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class Camera(Base):
    __tablename__ = "cameras"

    id: Mapped[int] = mapped_column(primary_key=True)
    device_mac: Mapped[str] = mapped_column(
        String(17), ForeignKey("devices.mac"), unique=True, nullable=False
    )
    onvif_host: Mapped[str] = mapped_column(String(64), nullable=False)
    onvif_port: Mapped[int] = mapped_column(Integer, default=2020)
    onvif_user: Mapped[str | None] = mapped_column(String(64))
    onvif_password: Mapped[str | None] = mapped_column(String(256))  # AES-encrypted
    rtsp_port: Mapped[int] = mapped_column(Integer, default=554)
    rtsp_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    stream_profile: Mapped[str] = mapped_column(String(32), default="mainStream")
    is_recording: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
