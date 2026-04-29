# tests/test_analytics.py
import pytest
import pytest_asyncio
from datetime import datetime
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import select
from sqlalchemy.pool import StaticPool
from app.database import Base


@pytest_asyncio.fixture
async def mem_db():
    """In-memory SQLite engine with all tables created.

    StaticPool forces all sessions to share one connection so data committed
    in one session is immediately visible to the next — required for SQLite :memory:.
    """
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # Import all models so Base.metadata knows about them
    from app.models import camera, recording, device, member, dlna_device  # noqa: F401
    from app.models import device_online_log  # noqa: F401
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    yield Session
    await engine.dispose()


@pytest.mark.asyncio
async def test_device_online_log_insert(mem_db):
    from app.models.device_online_log import DeviceOnlineLog
    async with mem_db() as db:
        db.add(DeviceOnlineLog(
            mac="AA:BB:CC:DD:EE:FF",
            bucket_hour=datetime(2024, 1, 15, 14, 0, 0),
            device_type="camera",
            online_count=3,
            scan_count=5,
        ))
        await db.commit()
        result = await db.execute(select(DeviceOnlineLog))
        rows = result.scalars().all()
    assert len(rows) == 1
    assert rows[0].mac == "AA:BB:CC:DD:EE:FF"
    assert rows[0].online_count == 3
    assert rows[0].scan_count == 5
