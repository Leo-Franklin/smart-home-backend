from datetime import datetime
from sqlalchemy import String, Boolean, DateTime, Text, func
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class DLNADevice(Base):
    __tablename__ = "dlna_devices"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    udn: Mapped[str] = mapped_column(String(256), unique=True, nullable=False, index=True)
    friendly_name: Mapped[str | None] = mapped_column(String(256))
    device_type: Mapped[str | None] = mapped_column(String(256))
    manufacturer: Mapped[str | None] = mapped_column(String(128))
    model_name: Mapped[str | None] = mapped_column(String(128))
    ip: Mapped[str | None] = mapped_column(String(45))
    location_url: Mapped[str | None] = mapped_column(Text)
    av_transport_url: Mapped[str | None] = mapped_column(Text)
    rendering_control_url: Mapped[str | None] = mapped_column(Text)
    is_online: Mapped[bool] = mapped_column(Boolean, default=True)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
