# Analytics API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add 7 analytics endpoints + 1 device heatmap endpoint so the frontend `/analytics` page stops returning 404s.

**Architecture:** A new `device_online_log` table stores hourly device-presence buckets written by the scanner; a new `analytics` router computes all chart data from this table plus the existing `Device` and `Recording` tables. The heatmap endpoint is added directly to the existing `devices` router.

**Tech Stack:** FastAPI, SQLAlchemy 2.x async, SQLite (aiosqlite), pytest-asyncio, httpx AsyncClient

---

## File Map

| Action | File | Responsibility |
|---|---|---|
| Create | `app/models/device_online_log.py` | `DeviceOnlineLog` ORM model |
| Create | `app/routers/analytics.py` | All 7 analytics endpoints |
| Modify | `app/routers/devices.py` | Add `_log_scan_result()` helper + call in `_run_scan()` + `GET /devices/heatmap` |
| Modify | `app/database.py` | Import new model in `init_db()` |
| Modify | `app/main.py` | Register analytics router |
| Create | `tests/test_analytics.py` | All analytics + heatmap tests |

---

## Task 1: DeviceOnlineLog Model

**Files:**
- Create: `app/models/device_online_log.py`
- Modify: `app/database.py`
- Test: `tests/test_analytics.py`

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

```
cd D:/Project/Demo/smart_home/backend
pytest tests/test_analytics.py::test_device_online_log_insert -v
```

Expected: `ImportError` or `ModuleNotFoundError` — `device_online_log` does not exist yet.

- [ ] **Step 3: Create the model**

```python
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
    device_type: Mapped[str] = mapped_column(String(32), default="unknown")
    online_count: Mapped[int] = mapped_column(Integer, default=0)
    scan_count: Mapped[int] = mapped_column(Integer, default=0)

    __table_args__ = (UniqueConstraint("mac", "bucket_hour", name="uq_device_hour"),)
```

- [ ] **Step 4: Register model in `init_db()` so `create_all` creates the table**

In `app/database.py`, change line 23 from:
```python
from app.models import camera, recording, device, member, dlna_device  # noqa: F401
```
to:
```python
from app.models import camera, recording, device, member, dlna_device, device_online_log  # noqa: F401
```

- [ ] **Step 5: Run test to verify it passes**

```
pytest tests/test_analytics.py::test_device_online_log_insert -v
```

Expected: `PASSED`

- [ ] **Step 6: Commit**

```bash
git add app/models/device_online_log.py app/database.py tests/test_analytics.py
git commit -m "feat: add DeviceOnlineLog model for hourly device presence tracking"
```

---

## Task 2: Simple Analytics Endpoints

Endpoints that use only `Device` and `Recording` tables — no history needed.

**Files:**
- Create: `app/routers/analytics.py`
- Modify: `app/main.py`
- Test: `tests/test_analytics.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_analytics.py`:

```python
import os
import pytest_asyncio
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
        db.add(Camera(device_mac="AA:BB:CC:DD:EE:01", name="Cam1", rtsp_url="rtsp://x"))
        await db.commit()
        db.add_all([
            Recording(camera_mac="AA:BB:CC:DD:EE:01", file_path="/f1",
                      started_at=datetime(2024, 1, 10, 10, 0), status="completed"),
            Recording(camera_mac="AA:BB:CC:DD:EE:01", file_path="/f2",
                      started_at=datetime(2024, 1, 10, 12, 0), status="completed"),
            Recording(camera_mac="AA:BB:CC:DD:EE:01", file_path="/f3",
                      started_at=datetime(2024, 1, 11, 9, 0), status="completed"),
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
    assert date_counts.get("2024-01-10") == 2
    assert date_counts.get("2024-01-11") == 1


@pytest.mark.asyncio
async def test_new_devices(seeded_client):
    resp = await seeded_client.get("/api/v1/analytics/new-devices?range=90d&group_by=week")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data) >= 1
    assert all("period" in row and "count" in row for row in data)
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_analytics.py::test_device_type_stats tests/test_analytics.py::test_response_time tests/test_analytics.py::test_recording_calendar tests/test_analytics.py::test_new_devices -v
```

Expected: `404 Not Found` (router not registered yet)

- [ ] **Step 3: Create `app/routers/analytics.py`**

```python
# app/routers/analytics.py
from datetime import datetime, timedelta
from fastapi import APIRouter, Query
from sqlalchemy import select, func, case
from app.deps import DBDep, CurrentUser
from app.models.device import Device
from app.models.recording import Recording

router = APIRouter(prefix="/analytics", tags=["analytics"])

_RANGE_DAYS = {"7d": 7, "30d": 30, "90d": 90}


def _days(range_str: str) -> int:
    return _RANGE_DAYS.get(range_str, 7)


@router.get("/device-type-stats")
async def device_type_stats(db: DBDep, _: CurrentUser):
    result = await db.execute(
        select(Device.device_type, func.count().label("count"))
        .group_by(Device.device_type)
        .order_by(func.count().desc())
    )
    return {"data": [{"type": row.device_type, "count": row.count} for row in result]}


@router.get("/response-time")
async def response_time(db: DBDep, _: CurrentUser):
    result = await db.execute(
        select(Device.mac, Device.alias, Device.hostname, Device.response_time_ms)
        .where(Device.response_time_ms.isnot(None))
        .order_by(Device.response_time_ms.desc())
    )
    return {
        "data": [
            {
                "mac": row.mac,
                "name": row.alias or row.hostname or row.mac,
                "avg_ms": row.response_time_ms,
            }
            for row in result
        ]
    }


@router.get("/recording-calendar")
async def recording_calendar(
    db: DBDep,
    _: CurrentUser,
    range_str: str = Query("90d", alias="range"),
):
    since = datetime.now() - timedelta(days=_days(range_str))
    result = await db.execute(
        select(
            func.strftime("%Y-%m-%d", Recording.started_at).label("date"),
            func.count().label("count"),
        )
        .where(Recording.started_at >= since)
        .group_by(func.strftime("%Y-%m-%d", Recording.started_at))
        .order_by("date")
    )
    return {"data": [{"date": row.date, "count": row.count} for row in result]}


@router.get("/new-devices")
async def new_devices(
    db: DBDep,
    _: CurrentUser,
    range_str: str = Query("90d", alias="range"),
    group_by: str = Query("week"),
):
    since = datetime.now() - timedelta(days=_days(range_str))
    result = await db.execute(
        select(
            func.strftime("%Y-W%W", Device.created_at).label("period"),
            func.count().label("count"),
        )
        .where(Device.created_at >= since)
        .group_by(func.strftime("%Y-W%W", Device.created_at))
        .order_by("period")
    )
    return {"data": [{"period": row.period, "count": row.count} for row in result]}
```

- [ ] **Step 4: Register the router in `app/main.py`**

Add import at line 23 (alongside other router imports):
```python
from app.routers import system, devices, cameras, recordings, schedules, ws
from app.routers import members, dlna
from app.routers import analytics  # add this line
```

Add `include_router` call after the existing ones (around line 365):
```python
app.include_router(analytics.router, prefix=API_PREFIX)
```

- [ ] **Step 5: Run tests to verify they pass**

```
pytest tests/test_analytics.py::test_device_type_stats tests/test_analytics.py::test_response_time tests/test_analytics.py::test_recording_calendar tests/test_analytics.py::test_new_devices -v
```

Expected: all 4 `PASSED`

- [ ] **Step 6: Commit**

```bash
git add app/routers/analytics.py app/main.py tests/test_analytics.py
git commit -m "feat: add simple analytics endpoints (device-type-stats, response-time, recording-calendar, new-devices)"
```

---

## Task 3: Scanner Log Upsert

**Files:**
- Modify: `app/routers/devices.py`
- Test: `tests/test_analytics.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_analytics.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_analytics.py::test_log_scan_result_online_device tests/test_analytics.py::test_log_scan_result_offline_device tests/test_analytics.py::test_log_scan_result_upserts_on_second_scan -v
```

Expected: `ImportError` — `_log_scan_result` not defined yet

- [ ] **Step 3: Add `_log_scan_result` helper to `app/routers/devices.py`**

Add these imports at the top of `app/routers/devices.py`:

```python
from datetime import datetime
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.device_online_log import DeviceOnlineLog
```

Add this function anywhere before `_run_scan`:

```python
async def _log_scan_result(
    db: AsyncSession,
    enriched: list[dict],
    bucket_hour: datetime,
) -> None:
    """Upsert per-device presence into DeviceOnlineLog for the given hour bucket."""
    online_macs = {d["mac"] for d in enriched}

    all_result = await db.execute(select(Device.mac, Device.device_type))
    all_devices = all_result.all()
    if not all_devices:
        return

    rows = [
        {
            "mac": d.mac,
            "bucket_hour": bucket_hour,
            "device_type": d.device_type or "unknown",
            "online_count": 1 if d.mac in online_macs else 0,
            "scan_count": 1,
        }
        for d in all_devices
    ]

    stmt = sqlite_insert(DeviceOnlineLog).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["mac", "bucket_hour"],
        set_={
            "online_count": DeviceOnlineLog.online_count + stmt.excluded.online_count,
            "scan_count": DeviceOnlineLog.scan_count + 1,
        },
    )
    await db.execute(stmt)
    await db.commit()
```

- [ ] **Step 4: Call `_log_scan_result` inside `_run_scan`**

In `_run_scan`, inside the `async with AsyncSessionLocal() as db:` block, add after the A2 section (after the `except Exception as e: logger.warning(...)` block for A2):

```python
            # Analytics: log hourly device presence
            try:
                bucket_hour = now.replace(minute=0, second=0, microsecond=0)
                await _log_scan_result(db, enriched, bucket_hour)
            except Exception as e:
                logger.warning(f"[Analytics] 设备在线日志写入失败，不影响扫描结果: {e}")
```

- [ ] **Step 5: Run tests to verify they pass**

```
pytest tests/test_analytics.py::test_log_scan_result_online_device tests/test_analytics.py::test_log_scan_result_offline_device tests/test_analytics.py::test_log_scan_result_upserts_on_second_scan -v
```

Expected: all 3 `PASSED`

- [ ] **Step 6: Commit**

```bash
git add app/routers/devices.py app/models/device_online_log.py tests/test_analytics.py
git commit -m "feat: log hourly device presence to DeviceOnlineLog on each scan"
```

---

## Task 4: History Analytics Endpoints

**Files:**
- Modify: `app/routers/analytics.py`
- Test: `tests/test_analytics.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_analytics.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_analytics.py::test_online_trend tests/test_analytics.py::test_device_stability tests/test_analytics.py::test_type_activity -v
```

Expected: `404 Not Found`

- [ ] **Step 3: Add history endpoints to `app/routers/analytics.py`**

Add these imports at the top of `app/routers/analytics.py`:

```python
from sqlalchemy import Integer, case
from app.models.device_online_log import DeviceOnlineLog
```

Add these three endpoints to `app/routers/analytics.py`:

```python
_KNOWN_TYPES = ["camera", "computer", "phone", "iot", "unknown"]


@router.get("/online-trend")
async def online_trend(
    db: DBDep,
    _: CurrentUser,
    range_str: str = Query("7d", alias="range"),
):
    since = datetime.now() - timedelta(days=_days(range_str))

    # Per hour bucket: count of devices with online_count > 0
    subq = (
        select(
            func.strftime("%Y-%m-%d", DeviceOnlineLog.bucket_hour).label("date"),
            func.sum(
                case((DeviceOnlineLog.online_count > 0, 1), else_=0)
            ).label("online_count"),
        )
        .where(DeviceOnlineLog.bucket_hour >= since)
        .group_by(DeviceOnlineLog.bucket_hour)
        .subquery()
    )

    result = await db.execute(
        select(
            subq.c.date,
            func.avg(subq.c.online_count).label("avg_count"),
        )
        .group_by(subq.c.date)
        .order_by(subq.c.date)
    )
    return {
        "data": [
            {"timestamp": f"{row.date}T00:00:00", "count": round(row.avg_count)}
            for row in result
        ]
    }


@router.get("/device-stability")
async def device_stability(
    db: DBDep,
    _: CurrentUser,
    range_str: str = Query("7d", alias="range"),
):
    since = datetime.now() - timedelta(days=_days(range_str))

    agg = await db.execute(
        select(
            DeviceOnlineLog.mac,
            func.sum(DeviceOnlineLog.online_count).label("total_online"),
            func.sum(DeviceOnlineLog.scan_count).label("total_scans"),
        )
        .where(DeviceOnlineLog.bucket_hour >= since)
        .group_by(DeviceOnlineLog.mac)
    )
    rows = agg.all()

    macs = [r.mac for r in rows]
    name_result = await db.execute(
        select(Device.mac, Device.alias, Device.hostname).where(Device.mac.in_(macs))
    )
    name_map = {d.mac: (d.alias or d.hostname or d.mac) for d in name_result.all()}

    data = [
        {
            "mac": r.mac,
            "name": name_map.get(r.mac, r.mac),
            "uptime_pct": round(r.total_online / r.total_scans * 100, 1) if r.total_scans else 0.0,
        }
        for r in rows
    ]
    data.sort(key=lambda x: x["uptime_pct"], reverse=True)
    return {"data": data}


@router.get("/type-activity")
async def type_activity(
    db: DBDep,
    _: CurrentUser,
    range_str: str = Query("7d", alias="range"),
):
    since = datetime.now() - timedelta(days=_days(range_str))

    result = await db.execute(
        select(
            func.cast(func.strftime("%H", DeviceOnlineLog.bucket_hour), Integer).label("hour"),
            DeviceOnlineLog.device_type,
            func.avg(
                case(
                    (DeviceOnlineLog.scan_count > 0,
                     DeviceOnlineLog.online_count * 1.0 / DeviceOnlineLog.scan_count),
                    else_=0.0,
                )
            ).label("fraction"),
        )
        .where(DeviceOnlineLog.bucket_hour >= since)
        .group_by(
            func.strftime("%H", DeviceOnlineLog.bucket_hour),
            DeviceOnlineLog.device_type,
        )
    )

    pivot: dict[int, dict[str, float]] = {
        h: {t: 0.0 for t in _KNOWN_TYPES} for h in range(24)
    }
    for row in result:
        if row.device_type in _KNOWN_TYPES:
            pivot[row.hour][row.device_type] = round(row.fraction or 0.0, 3)

    return {
        "data": [{"hour": h, **fractions} for h, fractions in sorted(pivot.items())]
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/test_analytics.py::test_online_trend tests/test_analytics.py::test_device_stability tests/test_analytics.py::test_type_activity -v
```

Expected: all 3 `PASSED`

- [ ] **Step 5: Commit**

```bash
git add app/routers/analytics.py tests/test_analytics.py
git commit -m "feat: add history analytics endpoints (online-trend, device-stability, type-activity)"
```

---

## Task 5: Device Heatmap Endpoint

**Files:**
- Modify: `app/routers/devices.py`
- Test: `tests/test_analytics.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_analytics.py`:

```python
@pytest.mark.asyncio
async def test_device_heatmap(history_client):
    resp = await history_client.get("/api/v1/devices/heatmap?range=7d")
    assert resp.status_code == 200
    cells = resp.json()["cells"]
    assert isinstance(cells, list)
    # Each cell has day (0-6), hour (0-23), value (int)
    for cell in cells:
        assert "day" in cell and "hour" in cell and "value" in cell
        assert 0 <= cell["day"] <= 6
        assert 0 <= cell["hour"] <= 23


@pytest.mark.asyncio
async def test_device_heatmap_type_filter(history_client):
    resp = await history_client.get("/api/v1/devices/heatmap?range=7d&device_type=camera")
    assert resp.status_code == 200
    cells = resp.json()["cells"]
    assert isinstance(cells, list)
    # With camera filter, should only reflect camera device data
    assert all(cell["value"] >= 0 for cell in cells)
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_analytics.py::test_device_heatmap tests/test_analytics.py::test_device_heatmap_type_filter -v
```

Expected: `404 Not Found`

- [ ] **Step 3: Add heatmap endpoint to `app/routers/devices.py`**

Add these imports at the top of `app/routers/devices.py` (alongside existing imports):

```python
from datetime import timedelta
from sqlalchemy import case, Integer
from app.models.device_online_log import DeviceOnlineLog
```

Add this endpoint to the devices router (before the `/{mac}` routes to avoid path conflicts):

```python
_HEATMAP_RANGE_DAYS = {"7d": 7, "30d": 30, "90d": 90}


@router.get("/heatmap")
async def device_heatmap(
    db: DBDep,
    _: CurrentUser,
    range_str: str = Query("7d", alias="range"),
    device_type: str = Query(""),
):
    days = _HEATMAP_RANGE_DAYS.get(range_str, 7)
    since = datetime.now() - timedelta(days=days)

    q = (
        select(
            func.cast(func.strftime("%w", DeviceOnlineLog.bucket_hour), Integer).label("day"),
            func.cast(func.strftime("%H", DeviceOnlineLog.bucket_hour), Integer).label("hour"),
            func.sum(
                case((DeviceOnlineLog.online_count > 0, 1), else_=0)
            ).label("value"),
        )
        .where(DeviceOnlineLog.bucket_hour >= since)
        .group_by(
            func.strftime("%w", DeviceOnlineLog.bucket_hour),
            func.strftime("%H", DeviceOnlineLog.bucket_hour),
        )
    )

    if device_type:
        types = [t.strip() for t in device_type.split(",") if t.strip()]
        if types:
            q = q.where(DeviceOnlineLog.device_type.in_(types))

    result = await db.execute(q)
    return {
        "cells": [
            {"day": row.day, "hour": row.hour, "value": row.value}
            for row in result
        ]
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/test_analytics.py::test_device_heatmap tests/test_analytics.py::test_device_heatmap_type_filter -v
```

Expected: both `PASSED`

- [ ] **Step 5: Run full analytics test suite**

```
pytest tests/test_analytics.py -v
```

Expected: all tests `PASSED`

- [ ] **Step 6: Run full test suite to check for regressions**

```
pytest -v
```

Expected: all tests `PASSED`

- [ ] **Step 7: Commit**

```bash
git add app/routers/devices.py tests/test_analytics.py
git commit -m "feat: add device heatmap endpoint to devices router"
```
