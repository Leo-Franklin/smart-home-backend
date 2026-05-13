# app/models/device_online_log.py
from datetime import datetime
from sqlalchemy import String, Integer, DateTime, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class DeviceOnlineLog(Base):
    __tablename__ = "device_online_log"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    mac: Mapped[str] = mapped_column(String(17), nullable=False, index=True)
    bucket_hour: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    device_type: Mapped[str] = mapped_column(String(32), default="unknown", server_default="unknown")
    online_count: Mapped[int] = mapped_column(Integer, default=0)
    scan_count: Mapped[int] = mapped_column(Integer, default=0)

    __table_args__ = (UniqueConstraint("mac", "bucket_hour", name="uq_device_hour"),)
