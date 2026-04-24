from datetime import datetime
from sqlalchemy import String, Boolean, Integer, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class Schedule(Base):
    __tablename__ = "schedules"

    id: Mapped[int] = mapped_column(primary_key=True)
    camera_mac: Mapped[str] = mapped_column(
        String(17), ForeignKey("cameras.device_mac"), nullable=False
    )
    name: Mapped[str | None] = mapped_column(String(128))
    cron_expr: Mapped[str] = mapped_column(String(64), nullable=False)
    segment_duration: Mapped[int] = mapped_column(Integer, default=1800)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, onupdate=func.now())
