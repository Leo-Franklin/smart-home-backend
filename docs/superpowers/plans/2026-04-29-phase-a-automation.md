# Phase A — 联动自动化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在已有事件节点之间插入响应逻辑，实现成员在家自动录制（A1）、陌生设备入网告警（A2）、摄像头掉线检测（A3）、录制完成自动投屏（A4）。

**Architecture:** 在 `presence_service._fire_event` 注入两个可选回调（auto_start_cb / auto_stop_cb），在 `_run_scan` 提取纯函数检测未知设备，新增 `CameraHealthChecker` 服务，在 `main._on_recording_complete` 追加 DLNA 投屏逻辑。所有 DB/NAS 业务逻辑保留在 `main.py` 的回调中，服务层只调用回调。

**Tech Stack:** FastAPI, SQLAlchemy async, SQLite (JSON column), asyncio, ffprobe (subprocess), pytest-asyncio, httpx AsyncClient

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Modify | `app/models/member.py` | 新增 `auto_record_cameras: JSON` |
| Modify | `app/models/camera.py` | 新增 `is_online`, `last_probe_at`, `auto_cast_dlna` |
| Modify | `app/schemas/member.py` | 同步新字段到 Create/Update/Out |
| Modify | `app/schemas/camera.py` | 同步新字段到 CameraOut/CameraUpdate |
| Modify | `app/database.py` | ALTER TABLE 迁移新列 |
| Modify | `app/config.py` | 新增 `CAMERA_HEALTH_INTERVAL_SECONDS`, `SERVER_PORT` |
| Create | `app/services/camera_health.py` | CameraHealthChecker（A3） |
| Modify | `app/services/presence_service.py` | 注入回调，`_fire_event` 触发自动录制（A1） |
| Modify | `app/routers/devices.py` | 提取 `_find_unknown_devices` 纯函数 + 广播（A2） |
| Modify | `app/main.py` | 定义 auto_start_cb / auto_stop_cb / auto_cast 逻辑，启动 CameraHealthChecker |
| Create | `tests/test_a2_unknown_device.py` | A2 单元测试 |
| Create | `tests/test_a3_camera_health.py` | A3 单元测试 |
| Create | `tests/test_a1_presence_recording.py` | A1 单元测试 |
| Create | `tests/test_a4_auto_cast.py` | A4 单元测试 |

---

## Task 1: DB 模型 — Member 和 Camera 新列

**Files:**
- Modify: `app/models/member.py`
- Modify: `app/models/camera.py`
- Modify: `app/database.py`

- [ ] **Step 1: 在 Member 模型添加 auto_record_cameras**

在 `app/models/member.py` 顶部增加 `JSON` import，并在 `Member` 类中添加字段：

```python
# app/models/member.py — 完整文件（仅展示变更部分）
from sqlalchemy import String, Boolean, DateTime, Text, ForeignKey, func, JSON   # 新增 JSON
```

在 `Member` 类的 `created_at` 字段后添加：

```python
    auto_record_cameras: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
```

- [ ] **Step 2: 在 Camera 模型添加 is_online / last_probe_at / auto_cast_dlna**

`app/models/camera.py` — 在 `is_recording` 字段后、`created_at` 字段前插入：

```python
    is_online: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_probe_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    auto_cast_dlna: Mapped[str | None] = mapped_column(String(256), nullable=True)
```

- [ ] **Step 3: 在 init_db 添加 ALTER TABLE 迁移**

`app/database.py` 的 `for stmt in (...)` 块末尾追加 4 条：

```python
            "ALTER TABLE members ADD COLUMN auto_record_cameras JSON DEFAULT '[]'",
            "ALTER TABLE cameras ADD COLUMN is_online BOOLEAN DEFAULT 1",
            "ALTER TABLE cameras ADD COLUMN last_probe_at DATETIME",
            "ALTER TABLE cameras ADD COLUMN auto_cast_dlna VARCHAR(256)",
```

- [ ] **Step 4: 验证模型能正确导入**

```bash
cd D:/Project/Demo/smart_home/backend
uv run python -c "from app.models.member import Member; from app.models.camera import Camera; print('OK')"
```

Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add app/models/member.py app/models/camera.py app/database.py
git commit -m "feat(A): add auto_record_cameras, is_online, last_probe_at, auto_cast_dlna columns"
```

---

## Task 2: Schema 更新 — Member 和 Camera

**Files:**
- Modify: `app/schemas/member.py`
- Modify: `app/schemas/camera.py`

- [ ] **Step 1: 更新 MemberCreate / MemberUpdate / MemberOut**

`app/schemas/member.py` — 在各 schema 中添加字段：

```python
class MemberCreate(BaseModel):
    name: str
    avatar_url: str | None = None
    webhook_url: str | None = None
    auto_record_cameras: list[str] = []          # 新增


class MemberUpdate(BaseModel):
    name: str | None = None
    avatar_url: str | None = None
    webhook_url: str | None = None
    auto_record_cameras: list[str] | None = None  # 新增


class MemberOut(BaseModel):
    id: int
    name: str
    avatar_url: str | None
    webhook_url: str | None
    is_home: bool
    last_arrived_at: datetime | None
    last_left_at: datetime | None
    auto_record_cameras: list[str]               # 新增
    created_at: datetime

    model_config = {"from_attributes": True}
```

- [ ] **Step 2: 更新 CameraOut / CameraUpdate**

`app/schemas/camera.py` — 在 `CameraUpdate` 添加 `auto_cast_dlna`，在 `CameraOut` 添加三个新字段：

```python
from datetime import datetime
from pydantic import BaseModel, field_validator


class CameraCreate(BaseModel):
    device_mac: str
    onvif_host: str
    onvif_port: int = 2020

    @field_validator("onvif_host")
    @classmethod
    def onvif_host_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("onvif_host 不能为空")
        return v.strip()
    onvif_user: str | None = None
    onvif_password: str | None = None
    rtsp_port: int = 554
    rtsp_url: str | None = None
    stream_profile: str = "mainStream"


class CameraUpdate(BaseModel):
    onvif_host: str | None = None
    onvif_port: int | None = None
    onvif_user: str | None = None
    onvif_password: str | None = None
    rtsp_port: int | None = None
    rtsp_url: str | None = None
    stream_profile: str | None = None
    auto_cast_dlna: str | None = None            # 新增


class CameraOut(BaseModel):
    id: int
    device_mac: str
    onvif_host: str
    onvif_port: int
    onvif_user: str | None
    rtsp_port: int
    rtsp_url: str | None
    stream_profile: str
    is_recording: bool
    is_online: bool                              # 新增
    last_probe_at: datetime | None               # 新增
    auto_cast_dlna: str | None                   # 新增
    created_at: datetime

    model_config = {"from_attributes": True}
```

- [ ] **Step 3: 验证 schema 导入**

```bash
uv run python -c "from app.schemas.member import MemberCreate, MemberOut; from app.schemas.camera import CameraOut; print('OK')"
```

Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add app/schemas/member.py app/schemas/camera.py
git commit -m "feat(A): update member and camera schemas with new fields"
```

---

## Task 3: A2 — 陌生设备入网告警

**Files:**
- Modify: `app/routers/devices.py`
- Create: `tests/test_a2_unknown_device.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_a2_unknown_device.py`：

```python
from datetime import datetime, timedelta
from unittest.mock import MagicMock
from app.routers.devices import _find_unknown_devices


def _make_device(last_seen_seconds_ago: int | None = None):
    """Helper: create a mock Device with last_seen."""
    device = MagicMock()
    if last_seen_seconds_ago is None:
        device.last_seen = None
    else:
        device.last_seen = datetime.now() - timedelta(seconds=last_seen_seconds_ago)
    return device


def test_new_mac_not_in_member_devices_is_unknown():
    enriched = [{"mac": "AA:BB:CC:DD:EE:FF", "ip": "192.168.1.100", "vendor": "Apple", "hostname": None}]
    existing_map = {}      # device not in DB yet → truly new
    bound_macs = set()
    now = datetime.now()

    result = _find_unknown_devices(enriched, existing_map, bound_macs, now)

    assert len(result) == 1
    assert result[0]["mac"] == "AA:BB:CC:DD:EE:FF"


def test_bound_mac_is_not_unknown():
    enriched = [{"mac": "AA:BB:CC:DD:EE:FF", "ip": "192.168.1.100", "vendor": "Apple", "hostname": None}]
    existing_map = {}
    bound_macs = {"AA:BB:CC:DD:EE:FF"}
    now = datetime.now()

    result = _find_unknown_devices(enriched, existing_map, bound_macs, now)

    assert result == []


def test_recently_seen_unknown_mac_is_suppressed():
    """A device seen 2 hours ago should NOT trigger another alert."""
    mac = "BB:CC:DD:EE:FF:00"
    enriched = [{"mac": mac, "ip": "192.168.1.101", "vendor": "Unknown", "hostname": None}]
    existing_map = {mac: _make_device(last_seen_seconds_ago=7200)}  # 2 hours ago
    bound_macs = set()
    now = datetime.now()

    result = _find_unknown_devices(enriched, existing_map, bound_macs, now, staleness_hours=24)

    assert result == []


def test_stale_unknown_mac_triggers_alert():
    """A device last seen 25 hours ago should trigger an alert again."""
    mac = "CC:DD:EE:FF:00:11"
    enriched = [{"mac": mac, "ip": "192.168.1.102", "vendor": "Unknown", "hostname": None}]
    existing_map = {mac: _make_device(last_seen_seconds_ago=25 * 3600)}  # 25 hours ago
    bound_macs = set()
    now = datetime.now()

    result = _find_unknown_devices(enriched, existing_map, bound_macs, now, staleness_hours=24)

    assert len(result) == 1
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
uv run pytest tests/test_a2_unknown_device.py -v
```

Expected: `ImportError` 或 `AttributeError`（`_find_unknown_devices` 未定义）

- [ ] **Step 3: 在 devices.py 提取纯函数**

在 `app/routers/devices.py` 的 `_run_scan` 函数**之前**插入：

```python
def _find_unknown_devices(
    enriched: list[dict],
    existing_map: dict,
    bound_macs: set[str],
    now: datetime,
    staleness_hours: int = 24,
) -> list[dict]:
    """Return devices not bound to any member that are new or stale (not seen recently)."""
    result = []
    for data in enriched:
        mac = data["mac"]
        if mac in bound_macs:
            continue
        existing = existing_map.get(mac)
        is_new = existing is None
        is_stale = (
            existing is not None
            and existing.last_seen is not None
            and (now - existing.last_seen).total_seconds() > staleness_hours * 3600
        )
        if is_new or is_stale:
            result.append(data)
    return result
```

- [ ] **Step 4: 在 _run_scan 的 DB 块末尾调用该函数并广播**

在 `_run_scan` 函数的 `async with AsyncSessionLocal() as db:` 块中，`await db.commit()` 之后、`await ws_manager.broadcast("scan_completed", results)` 之前插入：

```python
            # A2: unknown device detection
            bound_result = await db.execute(select(MemberDevice.mac))
            bound_macs = {row[0] for row in bound_result.all()}
            unknowns = _find_unknown_devices(enriched, existing_map, bound_macs, now)
            for u in unknowns:
                await ws_manager.broadcast("unknown_device_detected", {
                    "mac": u["mac"],
                    "ip": u["ip"],
                    "vendor": u.get("vendor"),
                    "hostname": u.get("hostname"),
                    "first_seen": now.isoformat(),
                })
            if unknowns:
                logger.info(f"[A2] 发现 {len(unknowns)} 台陌生设备")
```

确认 `MemberDevice` 已在 `devices.py` 顶部导入（已有）。

- [ ] **Step 5: 运行测试，确认通过**

```bash
uv run pytest tests/test_a2_unknown_device.py -v
```

Expected: 4 tests PASSED

- [ ] **Step 6: Commit**

```bash
git add app/routers/devices.py tests/test_a2_unknown_device.py
git commit -m "feat(A2): detect unknown devices after scan and broadcast unknown_device_detected"
```

---

## Task 4: A3 — CameraHealthChecker 服务

**Files:**
- Create: `app/services/camera_health.py`
- Create: `tests/test_a3_camera_health.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_a3_camera_health.py`：

```python
import asyncio
import pytest
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_probe_rtsp_success():
    from app.services.camera_health import CameraHealthChecker

    checker = CameraHealthChecker(interval=60)

    class FakeProc:
        returncode = 0
        async def wait(self):
            return 0

    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=FakeProc()):
        result = await checker._probe_rtsp("rtsp://192.168.1.100:554/stream")

    assert result is True


@pytest.mark.asyncio
async def test_probe_rtsp_nonzero_exit_returns_false():
    from app.services.camera_health import CameraHealthChecker

    checker = CameraHealthChecker(interval=60)

    class FakeProc:
        returncode = 1
        async def wait(self):
            return 1

    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=FakeProc()):
        result = await checker._probe_rtsp("rtsp://192.168.1.100:554/stream")

    assert result is False


@pytest.mark.asyncio
async def test_probe_rtsp_timeout_returns_false():
    from app.services.camera_health import CameraHealthChecker

    checker = CameraHealthChecker(interval=60)

    class SlowProc:
        returncode = None
        async def wait(self):
            await asyncio.sleep(100)

    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=SlowProc()):
        result = await checker._probe_rtsp("rtsp://192.168.1.100:554/stream")

    assert result is False


@pytest.mark.asyncio
async def test_probe_rtsp_exception_returns_false():
    from app.services.camera_health import CameraHealthChecker

    checker = CameraHealthChecker(interval=60)

    with patch("asyncio.create_subprocess_exec", side_effect=OSError("ffprobe not found")):
        result = await checker._probe_rtsp("rtsp://192.168.1.100:554/stream")

    assert result is False
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
uv run pytest tests/test_a3_camera_health.py -v
```

Expected: `ModuleNotFoundError: No module named 'app.services.camera_health'`

- [ ] **Step 3: 实现 CameraHealthChecker**

创建 `app/services/camera_health.py`：

```python
import asyncio
from datetime import datetime
from loguru import logger
from sqlalchemy import select
from app.database import AsyncSessionLocal
from app.models.camera import Camera
from app.services.ws_manager import ws_manager


class CameraHealthChecker:
    def __init__(self, interval: int = 60):
        self._interval = interval
        self._task: asyncio.Task | None = None

    async def start(self):
        self._task = asyncio.create_task(self._loop())
        logger.info(f"CameraHealthChecker 已启动，间隔 {self._interval}s")

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("CameraHealthChecker 已停止")

    async def _loop(self):
        while True:
            try:
                async with AsyncSessionLocal() as db:
                    await self._check_all(db)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"CameraHealthChecker 轮询异常: {e}")
            await asyncio.sleep(self._interval)

    async def _check_all(self, db):
        result = await db.execute(select(Camera).where(Camera.rtsp_url.isnot(None)))
        cameras = result.scalars().all()
        await asyncio.gather(
            *[self._check_camera(db, cam) for cam in cameras],
            return_exceptions=True,
        )

    async def _check_camera(self, db, camera: Camera):
        was_online = camera.is_online
        is_now_online = await self._probe_rtsp(camera.rtsp_url)
        camera.last_probe_at = datetime.now()
        camera.is_online = is_now_online
        await db.commit()

        if was_online and not is_now_online:
            await ws_manager.broadcast("camera_offline", {"mac": camera.device_mac})
            logger.warning(f"[CameraHealth] 摄像头掉线: {camera.device_mac}")
        elif not was_online and is_now_online:
            await ws_manager.broadcast("camera_online", {"mac": camera.device_mac})
            logger.info(f"[CameraHealth] 摄像头恢复: {camera.device_mac}")

    async def _probe_rtsp(self, rtsp_url: str) -> bool:
        try:
            proc = await asyncio.create_subprocess_exec(
                "ffprobe", "-v", "quiet",
                "-show_entries", "format=duration",
                "-i", rtsp_url,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=5)
            return proc.returncode == 0
        except (asyncio.TimeoutError, Exception):
            return False
```

- [ ] **Step 4: 运行测试，确认通过**

```bash
uv run pytest tests/test_a3_camera_health.py -v
```

Expected: 4 tests PASSED

- [ ] **Step 5: Commit**

```bash
git add app/services/camera_health.py tests/test_a3_camera_health.py
git commit -m "feat(A3): add CameraHealthChecker service with ffprobe RTSP probing"
```

---

## Task 5: A3 — Config 新增 + main.py 接入 CameraHealthChecker

**Files:**
- Modify: `app/config.py`
- Modify: `app/main.py`

- [ ] **Step 1: 在 config.py 添加两个配置项**

在 `Settings` 类的 `scan_interval_seconds` 字段附近添加：

```python
    camera_health_interval_seconds: int = 60   # A3: camera probe interval
    server_port: int = 8000                    # A4: for constructing DLNA media URLs
```

- [ ] **Step 2: 验证**

```bash
uv run python -c "from app.config import get_settings; s = get_settings(); print(s.camera_health_interval_seconds)"
```

Expected: `60`  
（如果 `.env` 已有值则输出对应值）

- [ ] **Step 3: 在 main.py lifespan 中启动/停止 CameraHealthChecker**

在 `app/main.py` 顶部 import 块添加：

```python
from app.services.camera_health import CameraHealthChecker
```

在 `lifespan` 函数中，`recorder.set_callbacks(...)` 这一行之前，添加 CameraHealthChecker 实例化：

```python
    camera_health_checker = CameraHealthChecker(settings.camera_health_interval_seconds)
```

在 `await presence_service.start()` 这一行之后添加：

```python
    await camera_health_checker.start()
    app.state.camera_health_checker = camera_health_checker
```

在 `yield` 之后的清理块（`await recorder.stop_monitor()` 这一行之前）添加：

```python
    await camera_health_checker.stop()
```

- [ ] **Step 4: 运行已有测试，确认不回归**

```bash
uv run pytest tests/test_api.py -v
```

Expected: 3 tests PASSED（health, login_success, login_fail）

- [ ] **Step 5: Commit**

```bash
git add app/config.py app/main.py
git commit -m "feat(A3): wire CameraHealthChecker in lifespan, add config fields"
```

---

## Task 6: A1 — Presence 触发录制

**Files:**
- Modify: `app/services/presence_service.py`
- Modify: `app/main.py`
- Create: `tests/test_a1_presence_recording.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_a1_presence_recording.py`：

```python
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime


@pytest.mark.asyncio
async def test_arrived_triggers_auto_start_recording():
    """When a member arrives and has auto_record_cameras, auto_start_cb is called."""
    from app.services.presence_service import PresenceService

    auto_start_cb = AsyncMock()
    auto_stop_cb = AsyncMock()
    svc = PresenceService(poll_interval=30)
    await svc.start(auto_start_cb=auto_start_cb, auto_stop_cb=auto_stop_cb)
    svc._task.cancel()  # don't actually run the loop

    member = MagicMock()
    member.id = 1
    member.name = "Alice"
    member.is_home = False
    member.webhook_url = None
    member.auto_record_cameras = ["AA:BB:CC:DD:EE:FF"]

    session = AsyncMock()
    session.add = MagicMock()
    session.commit = AsyncMock()

    svc._initialized = True  # skip first-run baseline

    with patch.object(svc, "_send_webhook", new_callable=AsyncMock):
        await svc._fire_event(session, member, is_home=True, triggered_mac="AA:BB:CC:DD:EE:FF")

    # Give create_task callbacks time to be scheduled
    await asyncio.sleep(0)

    auto_start_cb.assert_called_once_with("AA:BB:CC:DD:EE:FF")
    auto_stop_cb.assert_not_called()


@pytest.mark.asyncio
async def test_left_triggers_auto_stop_when_no_other_home_member():
    """When a member leaves and no other member is home with same camera, auto_stop_cb fires."""
    from app.services.presence_service import PresenceService
    from app.models.member import Member

    auto_start_cb = AsyncMock()
    auto_stop_cb = AsyncMock()
    svc = PresenceService(poll_interval=30)
    await svc.start(auto_start_cb=auto_start_cb, auto_stop_cb=auto_stop_cb)
    svc._task.cancel()

    member = MagicMock()
    member.id = 1
    member.name = "Alice"
    member.is_home = True
    member.webhook_url = None
    member.auto_record_cameras = ["AA:BB:CC:DD:EE:FF"]

    # No other members home
    session = AsyncMock()
    session.add = MagicMock()
    session.commit = AsyncMock()

    from sqlalchemy.engine import Result
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []  # no other home members
    session.execute = AsyncMock(return_value=mock_result)

    svc._initialized = True

    with patch.object(svc, "_send_webhook", new_callable=AsyncMock):
        await svc._fire_event(session, member, is_home=False, triggered_mac="AA:BB:CC:DD:EE:FF")

    await asyncio.sleep(0)

    auto_stop_cb.assert_called_once_with("AA:BB:CC:DD:EE:FF")
    auto_start_cb.assert_not_called()


@pytest.mark.asyncio
async def test_no_auto_record_cameras_no_callback():
    """Members without auto_record_cameras do not trigger any recording callback."""
    from app.services.presence_service import PresenceService

    auto_start_cb = AsyncMock()
    auto_stop_cb = AsyncMock()
    svc = PresenceService(poll_interval=30)
    await svc.start(auto_start_cb=auto_start_cb, auto_stop_cb=auto_stop_cb)
    svc._task.cancel()

    member = MagicMock()
    member.id = 2
    member.name = "Bob"
    member.is_home = False
    member.webhook_url = None
    member.auto_record_cameras = []  # empty list

    session = AsyncMock()
    session.add = MagicMock()
    session.commit = AsyncMock()

    svc._initialized = True

    with patch.object(svc, "_send_webhook", new_callable=AsyncMock):
        await svc._fire_event(session, member, is_home=True, triggered_mac=None)

    await asyncio.sleep(0)

    auto_start_cb.assert_not_called()
    auto_stop_cb.assert_not_called()
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
uv run pytest tests/test_a1_presence_recording.py -v
```

Expected: FAILED（`_fire_event` 的 signature 不接受 callbacks）

- [ ] **Step 3: 更新 PresenceService**

`app/services/presence_service.py` — 修改 `__init__` 和 `start`，并添加 `_trigger_auto_stop` 方法，并在 `_fire_event` 末尾插入 A1 逻辑：

在 `__init__` 中添加两个字段：
```python
        self._auto_start_cb = None   # async (camera_mac: str) -> None
        self._auto_stop_cb = None    # async (camera_mac: str) -> None
```

将 `start` 方法签名改为：
```python
    async def start(self, auto_start_cb=None, auto_stop_cb=None):
        self._auto_start_cb = auto_start_cb
        self._auto_stop_cb = auto_stop_cb
        self._task = asyncio.create_task(self._loop())
        logger.info(f"PresenceService 已启动，轮询间隔 {self._poll_interval}s")
```

在 `_fire_event` 方法的 `await session.commit()` 和 webhook 发送代码之间插入：

```python
        # A1: trigger auto recordings
        auto_cams = member.auto_record_cameras if isinstance(member.auto_record_cameras, list) else []
        if auto_cams:
            if is_home and self._auto_start_cb:
                for cam_mac in auto_cams:
                    asyncio.create_task(self._auto_start_cb(cam_mac))
            elif not is_home and self._auto_stop_cb:
                await self._trigger_auto_stop(session, member, auto_cams)
```

在 `_send_webhook` 方法之前添加新方法：

```python
    async def _trigger_auto_stop(self, session, member, camera_macs: list[str]):
        from app.models.member import Member as MemberModel
        other_home = (await session.execute(
            select(MemberModel).where(MemberModel.is_home == True, MemberModel.id != member.id)
        )).scalars().all()

        for cam_mac in camera_macs:
            other_wants = any(
                isinstance(m.auto_record_cameras, list) and cam_mac in m.auto_record_cameras
                for m in other_home
            )
            if not other_wants and self._auto_stop_cb:
                asyncio.create_task(self._auto_stop_cb(cam_mac))
```

- [ ] **Step 4: 运行 A1 测试，确认通过**

```bash
uv run pytest tests/test_a1_presence_recording.py -v
```

Expected: 3 tests PASSED

- [ ] **Step 5: 在 main.py 定义两个回调并注入**

在 `app/main.py` 的 `lifespan` 函数中，`recorder.set_callbacks(...)` 这一行**之前**，定义 A1 回调：

```python
    async def _auto_start_recording(camera_mac: str):
        from app.models.camera import Camera as CameraModel
        from app.models.recording import Recording as RecordingModel
        async with AsyncSessionLocal() as db:
            cam = (await db.execute(
                select(CameraModel).where(CameraModel.device_mac == camera_mac)
            )).scalar_one_or_none()
            if not cam or not cam.rtsp_url or cam.is_recording:
                return
            rec = RecordingModel(
                camera_mac=camera_mac,
                file_path="(pending)",
                started_at=datetime.now(),
                status="recording",
            )
            db.add(rec)
            cam.is_recording = True
            await db.commit()
            await db.refresh(rec)
            rec_id = rec.id

        try:
            await recorder.start_recording(camera_mac, cam.rtsp_url, settings.recording_segment_seconds)
        except Exception as e:
            logger.error(f"[A1] 自动录制启动失败 {camera_mac}: {e}")
            async with AsyncSessionLocal() as db:
                rec_db = (await db.execute(
                    select(RecordingModel).where(RecordingModel.id == rec_id)
                )).scalar_one_or_none()
                if rec_db:
                    rec_db.status = "failed"
                    rec_db.error_msg = str(e)
                cam_db = (await db.execute(
                    select(CameraModel).where(CameraModel.device_mac == camera_mac)
                )).scalar_one_or_none()
                if cam_db:
                    cam_db.is_recording = False
                await db.commit()
            return

        if camera_mac in recorder.active:
            recorder.active[camera_mac].recording_id = rec_id

    async def _auto_stop_recording(camera_mac: str):
        from app.models.camera import Camera as CameraModel
        from app.models.recording import Recording as RecordingModel
        output_path = await recorder.stop_recording(camera_mac)
        ended_at = datetime.now()

        async with AsyncSessionLocal() as db:
            cam = (await db.execute(
                select(CameraModel).where(CameraModel.device_mac == camera_mac)
            )).scalar_one_or_none()
            if cam:
                cam.is_recording = False

            rec = (await db.execute(
                select(RecordingModel)
                .where(RecordingModel.camera_mac == camera_mac, RecordingModel.status == "recording")
                .order_by(RecordingModel.started_at.desc())
                .limit(1)
            )).scalar_one_or_none()

            if rec:
                if output_path and output_path.exists():
                    loop = asyncio.get_running_loop()
                    try:
                        dest = await loop.run_in_executor(
                            None, lambda: nas_syncer.sync_file(output_path, camera_mac)
                        )
                        rec.file_path = str(dest)
                        rec.file_size = dest.stat().st_size if dest.exists() else None
                    except Exception as e:
                        logger.error(f"[A1] 停止录制 NAS 同步失败 {camera_mac}: {e}")
                        rec.file_path = str(output_path)
                        rec.file_size = output_path.stat().st_size if output_path.exists() else None
                    rec.status = "completed"
                else:
                    rec.status = "failed"
                    rec.error_msg = "presence-triggered stop: no valid output"
                rec.ended_at = ended_at

            await db.commit()

        await ws_manager.broadcast("recording_completed", {"camera_mac": camera_mac})
        logger.info(f"[A1] 自动停止录制完成: {camera_mac}")
```

将 `await presence_service.start()` 这一行改为：

```python
    await presence_service.start(
        auto_start_cb=_auto_start_recording,
        auto_stop_cb=_auto_stop_recording,
    )
```

- [ ] **Step 6: 运行全部测试，确认不回归**

```bash
uv run pytest tests/ -v
```

Expected: 全部 PASSED（test_api.py × 3, test_a1 × 3, test_a2 × 4, test_a3 × 4）

- [ ] **Step 7: Commit**

```bash
git add app/services/presence_service.py app/main.py tests/test_a1_presence_recording.py
git commit -m "feat(A1): presence-triggered auto recording start/stop with NAS sync"
```

---

## Task 7: A4 — 录制完成后自动 DLNA 投屏

**Files:**
- Modify: `app/main.py`
- Create: `tests/test_a4_auto_cast.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_a4_auto_cast.py`：

```python
import pytest
import socket
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, call
from dataclasses import dataclass
from datetime import datetime


@dataclass
class FakeTask:
    camera_mac: str
    output_path: Path
    started_at: datetime
    recording_id: int | None = None


def _make_cast_context(auto_cast_dlna: str | None, av_transport_url: str | None = "http://tv/avt"):
    """Return mocks for DB session that simulates the _on_recording_complete DB queries."""
    recording = MagicMock()
    recording.id = 1
    recording.status = None
    recording.file_path = None
    recording.file_size = None
    recording.ended_at = None
    recording.duration = None

    camera = MagicMock()
    camera.device_mac = "AA:BB:CC:DD:EE:FF"
    camera.is_recording = True
    camera.auto_cast_dlna = auto_cast_dlna

    dlna_device = MagicMock()
    dlna_device.av_transport_url = av_transport_url

    return recording, camera, dlna_device


@pytest.mark.asyncio
async def test_auto_cast_dlna_calls_controller_when_configured(tmp_path):
    """When camera.auto_cast_dlna is set, DLNAController.set_uri + play are called."""
    from app import main as main_module

    output_file = tmp_path / "rec.mp4"
    output_file.write_bytes(b"0" * 1024 * 20)  # 20 KB fake video

    task = FakeTask(
        camera_mac="AA:BB:CC:DD:EE:FF",
        output_path=output_file,
        started_at=datetime(2026, 4, 29, 10, 0, 0),
        recording_id=1,
    )

    recording, camera, dlna_device = _make_cast_context(auto_cast_dlna="uuid:some-udn-123")

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)

    execute_results = [
        MagicMock(**{"scalar_one_or_none.return_value": recording}),  # Recording query
        MagicMock(**{"scalar_one_or_none.return_value": camera}),     # Camera query
        MagicMock(**{"scalar_one_or_none.return_value": dlna_device}),# DLNADevice query
    ]
    mock_session.execute = AsyncMock(side_effect=execute_results)
    mock_session.commit = AsyncMock()

    mock_ctrl = AsyncMock()

    with (
        patch("app.main.AsyncSessionLocal", return_value=mock_session),
        patch("app.main.DLNAController", return_value=mock_ctrl),
        patch.object(main_module.nas_syncer, "sync_file", return_value=output_file),
    ):
        await main_module._on_recording_complete(task)

    mock_ctrl.set_uri.assert_called_once()
    mock_ctrl.play.assert_called_once()


@pytest.mark.asyncio
async def test_no_auto_cast_when_field_is_none(tmp_path):
    """When auto_cast_dlna is None, DLNAController is not called."""
    from app import main as main_module

    output_file = tmp_path / "rec.mp4"
    output_file.write_bytes(b"0" * 1024 * 20)

    task = FakeTask(
        camera_mac="BB:CC:DD:EE:FF:00",
        output_path=output_file,
        started_at=datetime(2026, 4, 29, 10, 0, 0),
        recording_id=2,
    )

    recording, camera, _ = _make_cast_context(auto_cast_dlna=None)

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)

    execute_results = [
        MagicMock(**{"scalar_one_or_none.return_value": recording}),
        MagicMock(**{"scalar_one_or_none.return_value": camera}),
    ]
    mock_session.execute = AsyncMock(side_effect=execute_results)
    mock_session.commit = AsyncMock()

    with (
        patch("app.main.AsyncSessionLocal", return_value=mock_session),
        patch("app.main.DLNAController") as mock_ctrl_cls,
        patch.object(main_module.nas_syncer, "sync_file", return_value=output_file),
    ):
        await main_module._on_recording_complete(task)

    mock_ctrl_cls.assert_not_called()
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
uv run pytest tests/test_a4_auto_cast.py -v
```

Expected: FAILED（`_on_recording_complete` 中无 DLNA 逻辑，`DLNAController` 未导入）

- [ ] **Step 3: 在 main.py 添加 DLNA 相关 import 和 A4 逻辑**

在 `app/main.py` 顶部 import 块添加：

```python
import socket as _socket
from app.models.dlna_device import DLNADevice
from app.services.dlna_service import DLNAController
```

在 `_on_recording_complete` 函数中，`await db.commit()` 之后，`logger.info(...)` 之前插入：

```python
        # A4: auto DLNA cast
        if cam and cam.auto_cast_dlna:
            dlna_dev = (await db.execute(
                select(DLNADevice).where(DLNADevice.udn == cam.auto_cast_dlna)
            )).scalar_one_or_none()
            if dlna_dev and dlna_dev.av_transport_url:
                asyncio.create_task(
                    _cast_recording(dlna_dev.av_transport_url, dest_str, task.camera_mac)
                )
```

在 `_on_recording_complete` 函数**之后**、`_on_recording_failed` 函数**之前**，添加辅助协程：

```python
async def _cast_recording(av_transport_url: str, file_path: str, camera_mac: str):
    """A4: serve recording file via dlna-media mount and cast to target device."""
    import shutil
    import time
    from pathlib import Path as _P

    src = _P(file_path)
    if not src.exists():
        logger.warning(f"[A4] 投屏跳过，文件不存在: {file_path}")
        return

    media_dir = _P("data/dlna_media")
    media_dir.mkdir(parents=True, exist_ok=True)
    fname = f"auto_{int(time.time())}_{src.name}"
    dest = media_dir / fname

    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, lambda: shutil.copy2(src, dest))
    except Exception as e:
        logger.error(f"[A4] 复制录制文件到 dlna_media 失败: {e}")
        return

    try:
        with _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
    except Exception:
        local_ip = "127.0.0.1"

    port = get_settings().server_port
    media_url = f"http://{local_ip}:{port}/dlna-media/{fname}"

    try:
        ctrl = DLNAController(av_transport_url)
        await ctrl.set_uri(media_url)
        await ctrl.play()
        logger.info(f"[A4] 自动投屏成功: {camera_mac} → {media_url}")
    except Exception as e:
        logger.error(f"[A4] 自动投屏失败: {e}")
    finally:
        await asyncio.sleep(3600)
        dest.unlink(missing_ok=True)
        logger.info(f"[A4] DLNA 临时文件已清理: {fname}")
```

- [ ] **Step 4: 运行 A4 测试，确认通过**

```bash
uv run pytest tests/test_a4_auto_cast.py -v
```

Expected: 2 tests PASSED

- [ ] **Step 5: 运行全部测试，确认无回归**

```bash
uv run pytest tests/ -v
```

Expected: 全部 PASSED（共 14 tests）

- [ ] **Step 6: Commit**

```bash
git add app/main.py tests/test_a4_auto_cast.py
git commit -m "feat(A4): auto DLNA cast after recording complete when auto_cast_dlna is set"
```

---

## 自检 (Self-Review)

### Spec coverage

| Spec 要求 | 实现任务 |
|-----------|----------|
| A1: Member.auto_record_cameras 字段 | Task 1 + Task 2 |
| A1: arrived → start, left → stop（多成员共享检查） | Task 6 |
| A1: recorder 依赖注入到 presence_service | Task 6 (Step 5, main.py callbacks) |
| A2: 扫描后检测陌生设备，去重 24h | Task 3 |
| A2: broadcast unknown_device_detected | Task 3 |
| A3: Camera.is_online / last_probe_at 字段 | Task 1 + Task 2 |
| A3: CameraHealthChecker 服务，ffprobe 探测 | Task 4 |
| A3: CAMERA_HEALTH_INTERVAL_SECONDS 配置 | Task 5 |
| A3: broadcast camera_offline / camera_online | Task 4 |
| A4: Camera.auto_cast_dlna 字段 | Task 1 + Task 2 |
| A4: 录制完成后触发 DLNAController | Task 7 |
| A4: 媒体文件复制到 dlna-media，1 小时清理 | Task 7 |

### Placeholder scan
无 TBD / TODO。所有方法签名、回调参数名、SQL 语句均已明确。

### Type consistency
- `auto_record_cameras`: `list[str]` in schema, `list` (JSON) in model — 一致
- `auto_cast_dlna`: `str | None` — 全链路一致
- `_auto_start_cb(camera_mac: str)` / `_auto_stop_cb(camera_mac: str)` — presence_service 和 main.py 回调签名一致
- `_find_unknown_devices` 返回 `list[dict]` — test 和实现一致
