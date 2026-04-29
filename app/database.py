from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import text
from app.config import get_settings


class Base(DeclarativeBase):
    pass


_settings = get_settings()
engine = create_async_engine(
    _settings.database_url,
    echo=_settings.debug,
    connect_args={"check_same_thread": False},
)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def init_db() -> None:
    # Import all models so create_all picks them up
    from app.models import camera, recording, device, member, dlna_device  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Idempotent column migrations — wrapped individually so one failure doesn't block others
        for stmt in (
            "ALTER TABLE cameras ADD COLUMN rtsp_url TEXT",
            "ALTER TABLE devices ADD COLUMN hostname TEXT",
            "ALTER TABLE devices ADD COLUMN open_ports TEXT",
            "ALTER TABLE devices ADD COLUMN response_time_ms REAL",
            "ALTER TABLE members ADD COLUMN auto_record_cameras JSON DEFAULT '[]'",
            "ALTER TABLE cameras ADD COLUMN is_online BOOLEAN DEFAULT 1",
            "ALTER TABLE cameras ADD COLUMN last_probe_at DATETIME",
            "ALTER TABLE cameras ADD COLUMN auto_cast_dlna VARCHAR(256)",
        ):
            try:
                await conn.execute(text(stmt))
            except Exception:
                pass


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session
