# tests/test_analytics.py
import pytest
import pytest_asyncio
from datetime import datetime, timedelta
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
    assert rows[0].bucket_hour == datetime(2024, 1, 15, 14, 0, 0)
    assert rows[0].online_count == 3
    assert rows[0].scan_count == 5
from httpx import AsyncClient, ASGITransport


_JWT_KEY = "test_secret_key_that_is_at_least_32_characters_long"
_ADMIN_PW = "testpassword12345"


@pytest_asyncio.fixture
async def client(mem_db, monkeypatch):
    """AsyncClient backed by in-memory DB, with auth token."""
    monkeypatch.setenv("JWT_SECRET_KEY", _JWT_KEY)
    monkeypatch.setenv("ADMIN_PASSWORD", _ADMIN_PW)

    from app.config import get_settings
    get_settings.cache_clear()

    from app.database import get_db
    from app.main import app as fastapi_app

    async def override_get_db():
        async with mem_db() as session:
            yield session

    fastapi_app.dependency_overrides[get_db] = override_get_db

    from app.auth import create_access_token
    token = create_access_token("admin", _JWT_KEY)
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=fastapi_app), base_url="http://test"
    ) as c:
        c.headers.update(headers)
        yield c

    fastapi_app.dependency_overrides.pop(get_db, None)
    get_settings.cache_clear()


@pytest_asyncio.fixture
async def seeded_client(mem_db, monkeypatch):
    """client fixture with Device and Recording rows pre-seeded."""
    monkeypatch.setenv("JWT_SECRET_KEY", _JWT_KEY)
    monkeypatch.setenv("ADMIN_PASSWORD", _ADMIN_PW)

    from app.config import get_settings
    get_settings.cache_clear()

    from app.models.device import Device
    from app.models.recording import Recording
    from app.models.camera import Camera

    # Use recent dates so they fall within the 90-day range filter
    day0 = datetime.now() - timedelta(days=5)   # 2 recordings on this day
    day1 = datetime.now() - timedelta(days=4)   # 1 recording on this day
    day0_str = day0.strftime("%Y-%m-%d")
    day1_str = day1.strftime("%Y-%m-%d")

    async with mem_db() as db:
        db.add_all([
            Device(mac="AA:BB:CC:DD:EE:01", device_type="camera",
                   alias="Cam1", response_time_ms=30.0, is_online=True),
            Device(mac="AA:BB:CC:DD:EE:02", device_type="phone",
                   hostname="phone1", response_time_ms=120.0, is_online=True),
            Device(mac="AA:BB:CC:DD:EE:03", device_type="camera",
                   response_time_ms=None, is_online=False),
        ])
        await db.commit()
        db.add(Camera(device_mac="AA:BB:CC:DD:EE:01", onvif_host="192.168.1.10", rtsp_url="rtsp://x"))
        await db.commit()
        db.add_all([
            Recording(camera_mac="AA:BB:CC:DD:EE:01", file_path="/f1",
                      started_at=day0.replace(hour=10, minute=0), status="completed"),
            Recording(camera_mac="AA:BB:CC:DD:EE:01", file_path="/f2",
                      started_at=day0.replace(hour=12, minute=0), status="completed"),
            Recording(camera_mac="AA:BB:CC:DD:EE:01", file_path="/f3",
                      started_at=day1.replace(hour=9, minute=0), status="completed"),
        ])
        await db.commit()

    from app.database import get_db
    from app.main import app as fastapi_app

    async def override_get_db():
        async with mem_db() as session:
            yield session

    fastapi_app.dependency_overrides[get_db] = override_get_db

    from app.auth import create_access_token
    token = create_access_token("admin", _JWT_KEY)
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=fastapi_app), base_url="http://test"
    ) as c:
        c.headers.update(headers)
        # Expose computed date strings for assertions
        c._day0_str = day0_str
        c._day1_str = day1_str
        yield c

    fastapi_app.dependency_overrides.pop(get_db, None)
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_device_type_stats(seeded_client):
    resp = await seeded_client.get("/api/v1/analytics/device-type-stats")
    assert resp.status_code == 200
    data = resp.json()["data"]
    types = {row["type"]: row["count"] for row in data}
    assert types["camera"] == 2
    assert types["phone"] == 1


@pytest.mark.asyncio
async def test_response_time(seeded_client):
    resp = await seeded_client.get("/api/v1/analytics/response-time")
    assert resp.status_code == 200
    data = resp.json()["data"]
    # Only devices with response_time_ms set (2 of 3)
    assert len(data) == 2
    assert all("avg_ms" in row and "name" in row and "mac" in row for row in data)
    # alias takes priority over hostname
    names = [row["name"] for row in data]
    assert "Cam1" in names
    assert "phone1" in names


@pytest.mark.asyncio
async def test_recording_calendar(seeded_client):
    resp = await seeded_client.get("/api/v1/analytics/recording-calendar?range=90d")
    assert resp.status_code == 200
    data = resp.json()["data"]
    date_counts = {row["date"]: row["count"] for row in data}
    assert date_counts.get(seeded_client._day0_str) == 2
    assert date_counts.get(seeded_client._day1_str) == 1


@pytest.mark.asyncio
async def test_new_devices(seeded_client):
    resp = await seeded_client.get("/api/v1/analytics/new-devices?range=90d")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data) >= 1
    assert all("period" in row and "count" in row for row in data)


@pytest.mark.asyncio
async def test_log_scan_result_online_device(mem_db):
    from app.models.device import Device
    from app.models.device_online_log import DeviceOnlineLog
    from app.routers.devices import _log_scan_result

    bucket = datetime(2024, 1, 15, 14, 0, 0)

    async with mem_db() as db:
        db.add(Device(mac="AA:BB:CC:DD:EE:FF", device_type="camera", is_online=True))
        await db.commit()

    async with mem_db() as db:
        await _log_scan_result(db, [{"mac": "AA:BB:CC:DD:EE:FF"}], bucket)

    async with mem_db() as db:
        result = await db.execute(select(DeviceOnlineLog))
        rows = result.scalars().all()

    assert len(rows) == 1
    assert rows[0].mac == "AA:BB:CC:DD:EE:FF"
    assert rows[0].online_count == 1
    assert rows[0].scan_count == 1


@pytest.mark.asyncio
async def test_log_scan_result_offline_device(mem_db):
    from app.models.device import Device
    from app.models.device_online_log import DeviceOnlineLog
    from app.routers.devices import _log_scan_result

    bucket = datetime(2024, 1, 15, 14, 0, 0)

    async with mem_db() as db:
        db.add(Device(mac="BB:BB:CC:DD:EE:FF", device_type="phone", is_online=False))
        await db.commit()

    # Scan found nothing — enriched list is empty
    async with mem_db() as db:
        await _log_scan_result(db, [], bucket)

    async with mem_db() as db:
        result = await db.execute(select(DeviceOnlineLog))
        rows = result.scalars().all()

    assert len(rows) == 1
    assert rows[0].online_count == 0   # was not found in scan
    assert rows[0].scan_count == 1


@pytest.mark.asyncio
async def test_log_scan_result_upserts_on_second_scan(mem_db):
    from app.models.device import Device
    from app.models.device_online_log import DeviceOnlineLog
    from app.routers.devices import _log_scan_result

    bucket = datetime(2024, 1, 15, 14, 0, 0)
    enriched = [{"mac": "CC:CC:CC:DD:EE:FF"}]

    async with mem_db() as db:
        db.add(Device(mac="CC:CC:CC:DD:EE:FF", device_type="iot", is_online=True))
        await db.commit()

    # Two scans in the same hour bucket
    async with mem_db() as db:
        await _log_scan_result(db, enriched, bucket)
    async with mem_db() as db:
        await _log_scan_result(db, enriched, bucket)

    async with mem_db() as db:
        result = await db.execute(select(DeviceOnlineLog))
        rows = result.scalars().all()

    assert len(rows) == 1           # still one row — upserted, not inserted twice
    assert rows[0].online_count == 2
    assert rows[0].scan_count == 2


@pytest_asyncio.fixture
async def history_client(mem_db, monkeypatch):
    """Client with DeviceOnlineLog rows pre-seeded for history-dependent endpoints."""
    monkeypatch.setenv("JWT_SECRET_KEY", _JWT_KEY)
    monkeypatch.setenv("ADMIN_PASSWORD", _ADMIN_PW)

    from app.config import get_settings
    get_settings.cache_clear()

    from app.models.device import Device
    from app.models.device_online_log import DeviceOnlineLog

    async with mem_db() as db:
        db.add_all([
            Device(mac="AA:BB:CC:DD:EE:01", device_type="camera", alias="Cam1", is_online=True),
            Device(mac="AA:BB:CC:DD:EE:02", device_type="phone", hostname="ph1", is_online=True),
        ])
        await db.commit()

        # Seed 3 hours worth of log entries (within last 7 days)
        from datetime import timedelta
        now = datetime.now()
        for h in range(3):
            bucket = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=h)
            db.add_all([
                DeviceOnlineLog(mac="AA:BB:CC:DD:EE:01", bucket_hour=bucket,
                                device_type="camera", online_count=3, scan_count=4),
                DeviceOnlineLog(mac="AA:BB:CC:DD:EE:02", bucket_hour=bucket,
                                device_type="phone", online_count=2, scan_count=4),
            ])
        await db.commit()

    from app.database import get_db
    from app.main import app as fastapi_app

    async def override_get_db():
        async with mem_db() as session:
            yield session

    fastapi_app.dependency_overrides[get_db] = override_get_db

    from app.auth import create_access_token
    token = create_access_token("admin", _JWT_KEY)
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=fastapi_app), base_url="http://test"
    ) as c:
        c.headers.update(headers)
        yield c

    fastapi_app.dependency_overrides.pop(get_db, None)
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_online_trend(history_client):
    resp = await history_client.get("/api/v1/analytics/online-trend?range=7d")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data) >= 1
    assert all("timestamp" in row and "count" in row for row in data)
    assert all(isinstance(row["count"], int) for row in data)


@pytest.mark.asyncio
async def test_device_stability(history_client):
    resp = await history_client.get("/api/v1/analytics/device-stability?range=7d")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data) == 2
    macs = {row["mac"] for row in data}
    assert "AA:BB:CC:DD:EE:01" in macs
    cam_row = next(r for r in data if r["mac"] == "AA:BB:CC:DD:EE:01")
    # 3 online out of 4 scans each hour = 75%
    assert cam_row["uptime_pct"] == 75.0
    assert cam_row["name"] == "Cam1"


@pytest.mark.asyncio
async def test_type_activity(history_client):
    resp = await history_client.get("/api/v1/analytics/type-activity?range=7d")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data) == 24   # one entry per hour of day (0–23)
    assert all("hour" in row for row in data)
    hours = [row["hour"] for row in data]
    assert hours == list(range(24))
