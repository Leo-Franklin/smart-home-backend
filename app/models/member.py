from datetime import datetime
from sqlalchemy import String, Boolean, DateTime, Text, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class Member(Base):
    __tablename__ = "members"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    avatar_url: Mapped[str | None] = mapped_column(String(512))
    webhook_url: Mapped[str | None] = mapped_column(String(512))
    is_home: Mapped[bool] = mapped_column(Boolean, default=False)
    last_arrived_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_left_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    devices: Mapped[list["MemberDevice"]] = relationship(
        "MemberDevice", back_populates="member", cascade="all, delete-orphan"
    )
    logs: Mapped[list["PresenceLog"]] = relationship(
        "PresenceLog", back_populates="member", cascade="all, delete-orphan"
    )


class MemberDevice(Base):
    __tablename__ = "member_devices"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), nullable=False)
    mac: Mapped[str] = mapped_column(String(17), nullable=False, index=True)
    label: Mapped[str | None] = mapped_column(String(64))

    member: Mapped["Member"] = relationship("Member", back_populates="devices")


class PresenceLog(Base):
    __tablename__ = "presence_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    member_id: Mapped[int] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), nullable=False)
    event: Mapped[str] = mapped_column(String(16), nullable=False)  # "arrived" | "left"
    triggered_by_mac: Mapped[str | None] = mapped_column(String(17))
    occurred_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    member: Mapped["Member"] = relationship("Member", back_populates="logs")
