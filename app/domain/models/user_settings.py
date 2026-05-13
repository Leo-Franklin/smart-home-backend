from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class UserSettings(Base):
    __tablename__ = "user_settings"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    language: Mapped[str] = mapped_column(String(16), default="zh-CN")
