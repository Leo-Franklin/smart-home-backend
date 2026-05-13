# 后端代码组织重构实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将后端代码重构为 DDD-lite 分层架构（api → domain/services → domain/models），解决 main.py（486行）和 routers 内业务逻辑过重的问题。

**Architecture:** 采用 DDD-lite 分层：API层（HTTP handlers）→ 领域服务层（业务逻辑）→ 领域模型层（SQLAlchemy ORM）。通过 re-export 桩保持向后兼容。

**Tech Stack:** Python, FastAPI, SQLAlchemy, pytest, APScheduler

---

## 阶段概览

| 阶段 | 内容 | 核心目标 |
|------|------|----------|
| 0 | 创建目录结构 + 向后兼容桩 | 基础设施就绪 |
| 1 | 新建 `recording_domain.py` 和 `presence_domain.py`（TDD） | main.py 瘦身核心 |
| 2 | 迁移 `domain/services/`（scanner, recorder, presence_service 等） | 服务层归位 |
| 3 | 迁移 `domain/models/`（全部 SQLAlchemy 模型） | 模型层归位 |
| 4 | 迁移 `api/`（原 routers）并瘦身的 devices.py | API 层归位 |
| 5 | 最终化 main.py + 清理向后兼容桩 | 验证 ≤120 行 |
| 6 | 全量测试通过 + 提交 | 完成 |

---

## 文件变更速查

### 新建
- `app/domain/__init__.py`
- `app/domain/models/__init__.py`
- `app/domain/models/device.py`（迁移）
- `app/domain/models/camera.py`（迁移）
- `app/domain/models/recording.py`（迁移）
- `app/domain/models/dlna_device.py`（迁移）
- `app/domain/models/member.py`（迁移）
- `app/domain/models/schedule.py`（迁移）
- `app/domain/models/device_online_log.py`（迁移）
- `app/domain/models/user_settings.py`（迁移）
- `app/domain/services/__init__.py`
- `app/domain/services/recording_domain.py`（新建）
- `app/domain/services/presence_domain.py`（新建）
- `app/domain/services/scanner.py`（迁移）
- `app/domain/services/recorder.py`（迁移）
- `app/domain/services/nas_syncer.py`（迁移）
- `app/domain/services/presence_service.py`（迁移）
- `app/domain/services/camera_health.py`（迁移）
- `app/domain/services/scheduler_service.py`（迁移）
- `app/domain/services/ws_manager.py`（迁移）
- `app/domain/services/dlna_service.py`（迁移）
- `app/domain/services/onvif_client.py`（迁移）
- `app/domain/repositories/__init__.py`
- `app/domain/repositories/device_repo.py`
- `app/domain/repositories/camera_repo.py`
- `app/domain/repositories/recording_repo.py`
- `app/domain/repositories/schedule_repo.py`
- `app/infrastructure/__init__.py`
- `app/infrastructure/persistence.py`
- `app/api/__init__.py`
- `app/api/devices.py`（迁移自 routers/devices.py）
- `app/api/cameras.py`（迁移）
- `app/api/recordings.py`（迁移）
- `app/api/schedules.py`（迁移）
- `app/api/members.py`（迁移）
- `app/api/dlna.py`（迁移）
- `app/api/analytics.py`（迁移）
- `app/api/system.py`（迁移）
- `app/api/user.py`（迁移）
- `app/api/ws.py`（迁移）

### 修改
- `app/models/__init__.py`（re-export 桩 → 委托给 domain.models）
- `app/services/__init__.py`（re-export 桩 → 委托给 domain.services）
- `app/routers/__init__.py`（re-export 桩 → 委托给 api）
- `app/main.py`（瘦身）
- `tests/` 下所有测试文件的 import 路径

---

## Task 0: 创建目录结构 + 向后兼容桩

### 创建目录

- [ ] **Step 1: 创建目录结构**

Run:
```bash
mkdir -p app/domain/models app/domain/services app/domain/repositories app/infrastructure app/api
```

- [ ] **Step 2: 创建 `app/domain/__init__.py`**

```python
# app/domain/__init__.py
"""Domain layer: models, services, repositories."""
```

- [ ] **Step 3: 创建 `app/domain/models/__init__.py`（re-export 桩）**

```python
# app/domain/models/__init__.py
"""Domain models - re-exported from app.models for backward compatibility."""
from app.models import *
```

- [ ] **Step 4: 创建 `app/domain/services/__init__.py`（re-export 桩）**

```python
# app/domain/services/__init__.py
"""Domain services - re-exported from app.services for backward compatibility."""
from app.services import *
```

- [ ] **Step 5: 创建 `app/domain/repositories/__init__.py`**

```python
# app/domain/repositories/__init__.py
"""Repository interfaces for decoupling ORM from business logic."""
```

- [ ] **Step 6: 创建 `app/infrastructure/__init__.py`**

```python
# app/infrastructure/__init__.py
"""Infrastructure layer: persistence, external protocols."""
```

- [ ] **Step 7: 创建 `app/api/__init__.py`（re-export 桩）**

```python
# app/api/__init__.py
"""API layer - re-exported from app.routers for backward compatibility."""
from app.routers import *
```

- [ ] **Step 8: 提交**

```bash
git add app/domain app/infrastructure app/api app/domain/models/__init__.py app/domain/services/__init__.py app/domain/repositories/__init__.py app/infrastructure/__init__.py
git commit -m "chore: create domain layer directory structure with backward-compat stubs"
```

---

## Task 1: 新建 `recording_domain.py`（TDD 驱动）

### 先写测试

- [ ] **Step 1: 创建测试文件 `tests/test_recording_domain.py`**

```python
import pytest
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# Test RecordingDomainService.on_recording_complete
@pytest.mark.asyncio
async def test_on_recording_complete_updates_recording_and_camera():
    """Recording completion should update DB status, sync to NAS, and broadcast WS."""
    from app.domain.services.recording_domain import RecordingDomainService

    # Setup mock task
    task = MagicMock()
    task.camera_mac = "AA:BB:CC:DD:EE:FF"
    task.output_path = Path("/tmp/test.mp4")
    task.started_at = datetime.now()
    task.recording_id = 1

    # Setup mock NAS syncer
    mock_dest = MagicMock()
    mock_dest.exists.return_value = True
    mock_dest.stat.return_value.st_size = 1024

    mock_nas_syncer = MagicMock()
    mock_nas_syncer.sync_file = AsyncMock(return_value=mock_dest)

    # Setup mock DB session
    mock_rec = MagicMock()
    mock_rec.status = "recording"
    mock_cam = MagicMock()
    mock_cam.is_recording = True
    mock_cam.device_mac = "AA:BB:CC:DD:EE:FF"
    mock_cam.auto_cast_dlna = None

    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.side_effect = [mock_rec, mock_cam]
    mock_db.execute = AsyncMock(return_value=mock_result)
    mock_db.commit = AsyncMock()

    svc = RecordingDomainService(nas_syncer=mock_nas_syncer)
    svc._ws_manager = MagicMock()
    svc._ws_manager.broadcast = AsyncMock()

    with patch("app.domain.services.recording_domain.AsyncSessionLocal", return_value=mock_db):
        await svc.on_recording_complete(task)

    mock_db.commit.assert_called()
    assert mock_rec.status == "completed"
    assert mock_cam.is_recording == False

@pytest.mark.asyncio
async def test_on_recording_complete_triggers_dlna_cast():
    """When camera has auto_cast_dlna set, DLNA cast should be triggered."""
    from app.domain.services.recording_domain import RecordingDomainService

    task = MagicMock()
    task.camera_mac = "AA:BB:CC:DD:EE:FF"
    task.output_path = Path("/tmp/test.mp4")
    task.started_at = datetime.now()
    task.recording_id = 1

    mock_dest = MagicMock()
    mock_dest.exists.return_value = True
    mock_dest.stat.return_value.st_size = 1024

    mock_nas_syncer = MagicMock()
    mock_nas_syncer.sync_file = AsyncMock(return_value=mock_dest)

    mock_dlna_dev = MagicMock()
    mock_dlna_dev.av_transport_url = "http://192.168.1.100:8080/av_transport"

    mock_rec = MagicMock()
    mock_cam = MagicMock()
    mock_cam.is_recording = True
    mock_cam.auto_cast_dlna = "uuid:dlna-device-1"

    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.side_effect = [mock_rec, mock_cam, mock_dlna_dev]
    mock_db.execute = AsyncMock(return_value=mock_result)
    mock_db.commit = AsyncMock()

    svc = RecordingDomainService(nas_syncer=mock_nas_syncer)
    svc._ws_manager = MagicMock()
    svc._ws_manager.broadcast = AsyncMock()
    svc._cast_recording = AsyncMock()

    with patch("app.domain.services.recording_domain.AsyncSessionLocal", return_value=mock_db):
        await svc.on_recording_complete(task)

    svc._cast_recording.assert_called_once()

@pytest.mark.asyncio
async def test_on_recording_failed_updates_recording():
    """Recording failure should mark status as failed and broadcast."""
    from app.domain.services.recording_domain import RecordingDomainService

    task = MagicMock()
    task.camera_mac = "AA:BB:CC:DD:EE:FF"
    task.recording_id = 1

    mock_rec = MagicMock()
    mock_cam = MagicMock()
    mock_cam.is_recording = True

    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.side_effect = [mock_rec, mock_cam]
    mock_db.execute = AsyncMock(return_value=mock_result)
    mock_db.commit = AsyncMock()

    svc = RecordingDomainService(nas_syncer=MagicMock())
    svc._ws_manager = MagicMock()
    svc._ws_manager.broadcast = AsyncMock()

    with patch("app.domain.services.recording_domain.AsyncSessionLocal", return_value=mock_db):
        await svc.on_recording_failed(task, retcode=1, stderr="test error")

    assert mock_rec.status == "failed"
    assert "test error" in mock_rec.error_msg
    mock_db.commit.assert_called()
```

- [ ] **Step 2: 运行测试验证失败**

Run:
```bash
cd D:/Project/Demo/smart_home/backend
pytest tests/test_recording_domain.py -v 2>&1 | head -30
```
Expected: FAIL — module not found

### 实现 `recording_domain.py`

- [ ] **Step 3: 创建 `app/domain/services/recording_domain.py`**

```python
import asyncio
import shutil
import socket
import time
from datetime import datetime
from pathlib import Path
from loguru import logger
from app.services.nas_syncer import NasSyncer
from app.services.ws_manager import ws_manager
from app.services.dlna_service import DLNAController
from app.database import AsyncSessionLocal
from app.models.recording import Recording
from app.models.camera import Camera
from app.models.dlna_device import DLNADevice
from app.config import get_settings


class RecordingDomainService:
    def __init__(self, nas_syncer: NasSyncer):
        self._nas_syncer = nas_syncer
        self._ws_manager = ws_manager

    async def on_recording_complete(self, task):
        """Handle recording completion: sync to NAS, update DB, trigger DLNA cast."""
        loop = asyncio.get_running_loop()
        try:
            dest = await loop.run_in_executor(
                None, lambda: self._nas_syncer.sync_file(task.output_path, task.camera_mac)
            )
            file_size = dest.stat().st_size if dest.exists() else None
            dest_str = str(dest)
        except Exception as e:
            logger.error(f"NAS同步失败 [{task.camera_mac}]: {e}")
            dest_str = str(task.output_path)
            file_size = task.output_path.stat().st_size if task.output_path.exists() else None

        ended_at = datetime.now()
        duration = int((ended_at - task.started_at).total_seconds())

        async with AsyncSessionLocal() as db:
            if task.recording_id:
                result = await db.execute(select(Recording).where(Recording.id == task.recording_id))
                rec = result.scalar_one_or_none()
                if rec:
                    rec.status = "completed"
                    rec.file_path = dest_str
                    rec.file_size = file_size
                    rec.ended_at = ended_at
                    rec.duration = duration

            cam = (await db.execute(select(Camera).where(Camera.device_mac == task.camera_mac))).scalar_one_or_none()
            if cam:
                cam.is_recording = False

            await db.commit()

            if cam and cam.auto_cast_dlna:
                dlna_dev = (await db.execute(
                    select(DLNADevice).where(DLNADevice.udn == cam.auto_cast_dlna)
                )).scalar_one_or_none()
                if dlna_dev and dlna_dev.av_transport_url:
                    await self._cast_recording(dlna_dev.av_transport_url, dest_str, task.camera_mac)

        logger.info(f"录制完成 [{task.camera_mac}] id={task.recording_id} 时长={duration}s")
        await self._ws_manager.broadcast("recording_completed", {
            "camera_mac": task.camera_mac,
            "recording_id": task.recording_id
        })

    async def on_recording_failed(self, task, retcode: int, stderr: str):
        """Handle recording failure: mark failed in DB and broadcast."""
        async with AsyncSessionLocal() as db:
            if task.recording_id:
                result = await db.execute(select(Recording).where(Recording.id == task.recording_id))
                rec = result.scalar_one_or_none()
                if rec:
                    rec.status = "failed"
                    rec.error_msg = (stderr or f"退出码 {retcode}")[:500]
                    rec.ended_at = datetime.now()

            cam = (await db.execute(select(Camera).where(Camera.device_mac == task.camera_mac))).scalar_one_or_none()
            if cam:
                cam.is_recording = False

            await db.commit()

        logger.error(f"录制失败 [{task.camera_mac}] id={task.recording_id} code={retcode}")
        await self._ws_manager.broadcast("recording_failed", {
            "camera_mac": task.camera_mac,
            "recording_id": task.recording_id
        })

    async def _cast_recording(self, av_transport_url: str, file_path: str, camera_mac: str):
        """Copy recording to dlna-media directory and cast to target DLNA device."""
        src = Path(file_path)
        if not src.exists():
            logger.warning(f"[A4] 投屏跳过，文件不存在: {file_path}")
            return

        media_dir = Path("data/dlna_media")
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
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
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
            return

        async def _cleanup():
            await asyncio.sleep(3600)
            dest.unlink(missing_ok=True)
            logger.info(f"[A4] DLNA 临时文件已清理: {fname}")

        asyncio.create_task(_cleanup())
```

- [ ] **Step 4: 运行测试验证通过**

Run:
```bash
pytest tests/test_recording_domain.py -v
```
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add tests/test_recording_domain.py app/domain/services/recording_domain.py
git commit -m "feat(domain): add RecordingDomainService with TDD tests"
```

---

## Task 2: 新建 `presence_domain.py`（TDD 驱动）

### 先写测试

- [ ] **Step 1: 创建测试文件 `tests/test_presence_domain.py`**

```python
import pytest
import asyncio
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_auto_start_recording_creates_recording_and_starts_recorder():
    """auto_start_recording should create DB record and call recorder.start_recording."""
    from app.domain.services.presence_domain import PresenceDomainService

    mock_recorder = MagicMock()
    mock_recorder.active = {}
    mock_recorder.start_recording = AsyncMock()
    mock_nas_syncer = MagicMock()

    svc = PresenceDomainService(recorder=mock_recorder, nas_syncer=mock_nas_syncer)

    mock_cam = MagicMock()
    mock_cam.device_mac = "AA:BB:CC:DD:EE:FF"
    mock_cam.rtsp_url = "rtsp://192.168.1.100:554/stream"
    mock_cam.onvif_user = "admin"
    mock_cam.onvif_password = "password"
    mock_cam.is_recording = False

    mock_rec = MagicMock()
    mock_rec.id = 42

    mock_db = AsyncMock()
    mock_db.add = MagicMock()
    mock_db.commit = AsyncMock()
    mock_db.refresh = AsyncMock(side_effect=lambda x: setattr(x, 'id', 42))

    mock_result = MagicMock()
    mock_result.scalar_one_or_none.side_effect = [mock_cam, mock_rec]
    mock_db.execute = AsyncMock(return_value=mock_result)

    with patch("app.domain.services.presence_domain.AsyncSessionLocal", return_value=mock_db):
        await svc.auto_start_recording("AA:BB:CC:DD:EE:FF")

    mock_recorder.start_recording.assert_called_once()
    assert mock_recorder.active["AA:BB:CC:DD:EE:FF"].recording_id == 42


@pytest.mark.asyncio
async def test_auto_stop_recording_stops_and_syncs():
    """auto_stop_recording should stop recorder, sync file, update DB."""
    from app.domain.services.presence_domain import PresenceDomainService

    output_path = Path("/tmp/test_output.mp4")
    mock_recorder = MagicMock()
    mock_recorder.stop_recording = AsyncMock(return_value=output_path)

    mock_dest = MagicMock()
    mock_dest.exists.return_value = True
    mock_dest.stat.return_value.st_size = 2048

    mock_nas_syncer = MagicMock()
    mock_nas_syncer.sync_file = AsyncMock(return_value=mock_dest)

    svc = PresenceDomainService(recorder=mock_recorder, nas_syncer=mock_nas_syncer)
    svc._ws_manager = MagicMock()
    svc._ws_manager.broadcast = AsyncMock()

    mock_cam = MagicMock()
    mock_cam.is_recording = True

    mock_rec = MagicMock()
    mock_rec.status = "recording"
    mock_rec.camera_mac = "AA:BB:CC:DD:EE:FF"

    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.side_effect = [mock_cam, mock_rec]
    mock_db.execute = AsyncMock(return_value=mock_result)
    mock_db.commit = AsyncMock()

    with patch("app.domain.services.presence_domain.AsyncSessionLocal", return_value=mock_db):
        await svc.auto_stop_recording("AA:BB:CC:DD:EE:FF")

    mock_recorder.stop_recording.assert_called_once_with("AA:BB:CC:DD:EE:FF")
    assert mock_rec.status == "completed"
    mock_db.commit.assert_called()
```

### 实现 `presence_domain.py`

- [ ] **Step 2: 创建 `app/domain/services/presence_domain.py`**

```python
import asyncio
from datetime import datetime
from pathlib import Path
from loguru import logger
from app.services.recorder import Recorder, RecordingTask
from app.services.nas_syncer import NasSyncer
from app.services.ws_manager import ws_manager
from app.database import AsyncSessionLocal
from app.models.camera import Camera as CameraModel
from app.models.recording import Recording as RecordingModel
from app.config import get_settings
from urllib.parse import urlparse, urlunparse


class PresenceDomainService:
    def __init__(self, recorder: Recorder, nas_syncer: NasSyncer):
        self._recorder = recorder
        self._nas_syncer = nas_syncer
        self._ws_manager = ws_manager

    async def auto_start_recording(self, camera_mac: str) -> None:
        """Start recording when presence is detected."""
        async with AsyncSessionLocal() as db:
            cam = (await db.execute(
                select(CameraModel).where(CameraModel.device_mac == camera_mac)
            )).scalar_one_or_none()
            if not cam or not cam.rtsp_url or cam.is_recording:
                return
            rtsp_url = cam.rtsp_url
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
            await self._recorder.start_recording(camera_mac, rtsp_url, get_settings().recording_segment_seconds)
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

        if camera_mac in self._recorder.active:
            self._recorder.active[camera_mac].recording_id = rec_id
        else:
            logger.warning(f"[A1] 录制任务已结束，无法设置 recording_id {rec_id}: {camera_mac}")

    async def auto_stop_recording(self, camera_mac: str) -> None:
        """Stop recording when presence is lost."""
        output_path = await self._recorder.stop_recording(camera_mac)
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
                            None, lambda: self._nas_syncer.sync_file(output_path, camera_mac)
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

        await self._ws_manager.broadcast("recording_completed", {"camera_mac": camera_mac})
        logger.info(f"[A1] 自动停止录制完成: {camera_mac}")
```

- [ ] **Step 3: 运行测试验证通过**

Run:
```bash
pytest tests/test_presence_domain.py -v
```
Expected: PASS

- [ ] **Step 4: 提交**

```bash
git add tests/test_presence_domain.py app/domain/services/presence_domain.py
git commit -m "feat(domain): add PresenceDomainService with TDD tests"
```

---

## Task 3: 迁移 `app/services/*.py` → `app/domain/services/`

迁移顺序（按依赖关系）：
1. `onvif_client.py`（无内部依赖）
2. `ws_manager.py`（无内部依赖）
3. `scheduler_service.py`（无内部依赖）
4. `nas_syncer.py`（无内部依赖）
5. `recorder.py`（无内部依赖）
6. `camera_health.py`
7. `presence_service.py`
8. `dlna_service.py`
9. `scanner.py`（最复杂，有多个内部辅助函数）

### 迁移 `onvif_client.py`

- [ ] **Step 1: 读取原始文件**

Run:
```bash
cat app/services/onvif_client.py
```

- [ ] **Step 2: 创建 `app/domain/services/onvif_client.py`**

内容直接复制自 `app/services/onvif_client.py`（文件内容不变，仅移动位置）。

- [ ] **Step 3: 更新 `app/domain/services/__init__.py`**

```python
# app/domain/services/__init__.py
from app.services import *
from app.domain.services.onvif_client import OnvifClient
from app.domain.services.ws_manager import ws_manager
from app.domain.services.scheduler_service import scheduler_service, SchedulerService
from app.domain.services.nas_syncer import NasSyncer
from app.domain.services.recorder import Recorder, RecordingTask
from app.domain.services.camera_health import CameraHealthChecker
from app.domain.services.presence_service import PresenceService, presence_service
from app.domain.services.dlna_service import DLNAController
from app.domain.services.scanner import Scanner
from app.domain.services.recording_domain import RecordingDomainService
from app.domain.services.presence_domain import PresenceDomainService
```

- [ ] **Step 4: 更新 `app/services/__init__.py` 为转发桩**

```python
# app/services/__init__.py
"""Backward compatibility: re-export from domain.services."""
from app.domain.services import *
```

- [ ] **Step 5: 运行测试验证**

Run:
```bash
pytest tests/ -v --tb=short 2>&1 | tail -20
```
Expected: 所有现有测试 PASS

- [ ] **Step 6: 提交**

```bash
git add app/domain/services/onvif_client.py app/domain/services/__init__.py app/services/__init__.py
git commit -m "chore: migrate onvif_client.py to domain/services"
```

### 迁移 `ws_manager.py`

- [ ] **Step 1: 创建 `app/domain/services/ws_manager.py`**

内容复制自 `app/services/ws_manager.py`。

- [ ] **Step 2: 提交**

```bash
git add app/domain/services/ws_manager.py
git commit -m "chore: migrate ws_manager.py to domain/services"
```

### 迁移 `scheduler_service.py`

- [ ] **Step 1: 创建 `app/domain/services/scheduler_service.py`**

内容复制自 `app/services/scheduler_service.py`。

- [ ] **Step 2: 提交**

```bash
git add app/domain/services/scheduler_service.py
git commit -m "chore: migrate scheduler_service.py to domain/services"
```

### 迁移 `nas_syncer.py`

- [ ] **Step 1: 创建 `app/domain/services/nas_syncer.py`**

内容复制自 `app/services/nas_syncer.py`。

- [ ] **Step 2: 提交**

```bash
git add app/domain/services/nas_syncer.py
git commit -m "chore: migrate nas_syncer.py to domain/services"
```

### 迁移 `recorder.py`

- [ ] **Step 1: 创建 `app/domain/services/recorder.py`**

内容复制自 `app/services/recorder.py`。

- [ ] **Step 2: 提交**

```bash
git add app/domain/services/recorder.py
git commit -m "chore: migrate recorder.py to domain/services"
```

### 迁移 `camera_health.py`

- [ ] **Step 1: 创建 `app/domain/services/camera_health.py`**

内容复制自 `app/services/camera_health.py`。

- [ ] **Step 2: 提交**

```bash
git add app/domain/services/camera_health.py
git commit -m "chore: migrate camera_health.py to domain/services"
```

### 迁移 `presence_service.py`

- [ ] **Step 1: 创建 `app/domain/services/presence_service.py`**

内容复制自 `app/services/presence_service.py`。

- [ ] **Step 2: 提交**

```bash
git add app/domain/services/presence_service.py
git commit -m "chore: migrate presence_service.py to domain/services"
```

### 迁移 `dlna_service.py`

- [ ] **Step 1: 创建 `app/domain/services/dlna_service.py`**

内容复制自 `app/services/dlna_service.py`。

- [ ] **Step 2: 提交**

```bash
git add app/domain/services/dlna_service.py
git commit -m "chore: migrate dlna_service.py to domain/services"
```

### 迁移 `scanner.py`（最复杂）

- [ ] **Step 1: 创建 `app/domain/services/scanner.py`**

内容复制自 `app/services/scanner.py`。

- [ ] **Step 2: 运行 scanner 相关测试**

Run:
```bash
pytest tests/test_scanner.py tests/test_a2_unknown_device.py -v
```
Expected: PASS

- [ ] **Step 3: 提交**

```bash
git add app/domain/services/scanner.py
git commit -m "chore: migrate scanner.py to domain/services"
```

---

## Task 4: 迁移 `app/models/` → `app/domain/models/`

迁移顺序（按依赖关系，无特定顺序，因为都是独立模型）：
1. `user_settings.py`
2. `device_online_log.py`
3. `schedule.py`
4. `dlna_device.py`
5. `recording.py`
6. `device.py`
7. `member.py`
8. `camera.py`

### 对每个模型文件

- [ ] **Step 1: 创建 `app/domain/models/<model_name>.py`**

内容复制自对应 `app/models/<model_name>.py`。

- [ ] **Step 2: 更新 `app/domain/models/__init__.py`**

```python
# app/domain/models/__init__.py
from app.domain.models.user_settings import UserSettings
from app.domain.models.device_online_log import DeviceOnlineLog
from app.domain.models.schedule import Schedule
from app.domain.models.dlna_device import DLNADevice
from app.domain.models.recording import Recording
from app.domain.models.device import Device
from app.domain.models.member import Member, MemberDevice
from app.domain.models.camera import Camera
```

- [ ] **Step 3: 更新 `app/models/__init__.py` 为转发桩**

```python
# app/models/__init__.py
"""Backward compatibility: re-export from domain.models."""
from app.domain.models import *
```

- [ ] **Step 4: 运行测试验证**

Run:
```bash
pytest tests/ -v --tb=short 2>&1 | tail -10
```
Expected: PASS

- [ ] **Step 5: 提交（批量）**

```bash
git add app/domain/models/ app/models/__init__.py app/domain/models/__init__.py
git commit -m "chore: migrate all models to domain/models with backward compat"
```

---

## Task 5: 迁移 `app/routers/` → `app/api/` 并瘦身的 devices.py

### 先瘦身的 `devices.py`（迁移到 `app/api/devices.py`）

- [ ] **Step 1: 读取原始 `app/routers/devices.py`**

确认需要迁移到 scanner.py 的函数：
- `_enrich_device` → `domain/services/scanner.py`
- `_find_unknown_devices` → `domain/services/scanner.py`
- `_log_scan_result` → `domain/services/scanner.py`
- `_run_scan` → `domain/services/scanner.py`

- [ ] **Step 2: 更新 `app/domain/services/scanner.py`，添加迁移的函数**

从 `app/routers/devices.py` 复制以下函数到 `app/domain/services/scanner.py`：
- `_enrich_device`
- `_find_unknown_devices`
- `_log_scan_result`
- `_run_scan`

并更新 `app/domain/services/__init__.py` 导出。

- [ ] **Step 3: 创建瘦身的 `app/api/devices.py`**

保留仅 HTTP handler 的函数，删除内联业务逻辑函数。

```python
# app/api/devices.py
import math
from datetime import datetime, timedelta
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select, func
from app.deps import DBDep, CurrentUser
from app.models.device import Device
from app.models.member import Member, MemberDevice
from app.models.device_online_log import DeviceOnlineLog
from app.schemas.device import DeviceOut, DeviceUpdate
from app.schemas import PagedResponse
from app.domain.services.scanner import Scanner
from app.domain.services.ws_manager import ws_manager
from app.config import get_settings
from loguru import logger

router = APIRouter(prefix="/devices", tags=["devices"])

@router.get("", response_model=PagedResponse[DeviceOut])
async def list_devices(...): ...

@router.post("/scan", status_code=status.HTTP_202_ACCEPTED, tags=["devices"])
async def trigger_scan(...): ...

# ... 其他 HTTP handlers
```

- [ ] **Step 4: 更新 `app/routers/__init__.py` 为转发桩**

```python
# app/routers/__init__.py
"""Backward compatibility: re-export from app.api."""
from app.api import *
```

- [ ] **Step 5: 更新 `app/api/__init__.py`**

```python
# app/api/__init__.py
"""API layer - HTTP handlers."""
from app.api.devices import router as devices_router
from app.api.cameras import router as cameras_router
# ... 其他 routers
```

- [ ] **Step 6: 运行测试验证**

Run:
```bash
pytest tests/ -v --tb=short 2>&1 | tail -10
```
Expected: PASS

- [ ] **Step 7: 提交**

```bash
git add app/api/devices.py app/api/__init__.py app/routers/__init__.py
git commit -m "refactor(api): migrate devices router to api/ and slim down"
```

### 迁移其他 routers（无瘦身的）

- [ ] **Step 8: 迁移 `cameras.py`, `recordings.py`, `schedules.py`, `members.py`, `dlna.py`, `analytics.py`, `system.py`, `user.py`, `ws.py`**

每个文件直接复制到 `app/api/` 目录：

Run:
```bash
cp app/routers/cameras.py app/api/cameras.py
cp app/routers/recordings.py app/api/recordings.py
cp app/routers/schedules.py app/api/schedules.py
cp app/routers/members.py app/api/members.py
cp app/routers/dlna.py app/api/dlna.py
cp app/routers/analytics.py app/api/analytics.py
cp app/routers/system.py app/api/system.py
cp app/routers/user.py app/api/user.py
cp app/routers/ws.py app/api/ws.py
```

- [ ] **Step 9: 更新 `app/api/__init__.py` 导出全部 router**

- [ ] **Step 10: 运行测试验证**

Run:
```bash
pytest tests/ -v --tb=short 2>&1 | tail -10
```
Expected: PASS

- [ ] **Step 11: 提交**

```bash
git add app/api/
git commit -m "chore: migrate remaining routers to api/ layer"
```

---

## Task 6: 最终化 `main.py`（瘦身到 ≤120 行）

### 重写 `main.py`

- [ ] **Step 1: 读取当前 `main.py`，识别所有要迁移的逻辑**

已迁移到 domain services 的：
- `_on_recording_complete` → `RecordingDomainService.on_recording_complete()`
- `_on_recording_failed` → `RecordingDomainService.on_recording_failed()`
- `_cast_recording` → 内嵌于 `RecordingDomainService._cast_recording()`
- `_make_scheduled_trigger` → `SchedulerService.make_trigger()` 或内嵌
- `_auto_start_recording` → `PresenceDomainService.auto_start_recording()`
- `_auto_stop_recording` → `PresenceDomainService.auto_stop_recording()`

- [ ] **Step 2: 重写 `main.py`**

目标 ≤120 行，结构如下：

```python
import asyncio, os, sys, threading, socket as _socket
from contextlib import asynccontextmanager
from datetime import datetime
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from loguru import logger
from pathlib import Path as _Path

from app.config import get_settings, is_packaged
from app.database import init_db, AsyncSessionLocal
from app.domain.models.camera import Camera
from app.domain.models.recording import Recording
from app.domain.models.schedule import Schedule as ScheduleModel
from app.domain.models.dlna_device import DLNADevice
from app.domain.services.scheduler_service import scheduler_service
from app.domain.services.recorder import Recorder, RecordingTask
from app.domain.services.nas_syncer import NasSyncer
from app.domain.services.ws_manager import ws_manager
from app.domain.services.presence_service import presence_service
from app.domain.services.camera_health import CameraHealthChecker
from app.domain.services.dlna_service import DLNAController
from app.domain.services.recording_domain import RecordingDomainService
from app.domain.services.presence_domain import PresenceDomainService
from app.api import devices, cameras, recordings, schedules, members, dlna, analytics, system, user, ws

settings = get_settings()

# Packaged mode PATH setup ...
# Loguru config ...
# Global service instances ...

recorder = Recorder(settings.recording_temp_dir)
nas_syncer = NasSyncer(...)
recording_domain = RecordingDomainService(nas_syncer=nas_syncer)
presence_domain = PresenceDomainService(recorder=recorder, nas_syncer=nas_syncer)

recorder.set_callbacks(
    on_complete=lambda t: recording_domain.on_recording_complete(t),
    on_failed=lambda t, rc, err: recording_domain.on_recording_failed(t, rc, err),
)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # startup: init_db, cleanup stale records, start scheduler,
    # restore schedules, start presence, start health checker
    # yield
    # shutdown: stop health checker, recorder, presence, scheduler
    ...

app = FastAPI(...)
app.add_middleware(...)
API_PREFIX = "/api/v1"
app.include_router(...)
app.mount(...)

@app.get("/hls/{path:path}")
async def serve_hls_file(path: str): ...

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException): ...

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception): ...

def _dev():
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
```

- [ ] **Step 3: 验证行数**

Run:
```bash
wc -l app/main.py
```
Expected: ≤120

- [ ] **Step 4: 运行测试验证**

Run:
```bash
pytest tests/ -v --tb=short 2>&1 | tail -10
```
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add app/main.py
git commit -m "refactor: slim main.py to ≤120 lines using domain services"
```

---

## Task 7: 创建仓储层接口（可选，如需要）

- [ ] **Step 1: 创建 `app/domain/repositories/*.py`**

```python
# app/domain/repositories/device_repo.py
from typing import Protocol
from app.domain.models.device import Device

class DeviceRepository(Protocol):
    async def get_by_mac(self, mac: str) -> Device | None: ...
    async def list_all(self) -> list[Device]: ...
```

- [ ] **Step 2: 提交**

```bash
git add app/domain/repositories/
git commit -m "feat(domain): add repository interfaces for future extensibility"
```

---

## Task 8: 全量测试 + 清理

- [ ] **Step 1: 运行全量测试**

Run:
```bash
pytest tests/ -v 2>&1 | tail -20
```
Expected: ALL PASS

- [ ] **Step 2: 验证 main.py 行数**

Run:
```bash
wc -l app/main.py
```
Expected: ≤120

- [ ] **Step 3: 最终提交**

```bash
git status
git commit -m "refactor: complete DDD-lite backend reorganization

- Moved models to app/domain/models/
- Moved services to app/domain/services/
- Moved routers to app/api/
- Added RecordingDomainService and PresenceDomainService
- Slimmed main.py to ≤120 lines
- Maintained full backward compatibility via re-export stubs"
```

---

## 自我检查清单

- [ ] Spec 覆盖完整：每个设计需求都能在 plan 中找到对应任务
- [ ] 无占位符：所有任务都有具体代码和具体命令
- [ ] 类型一致性：迁移后的函数签名与原文件保持一致
- [ ] TDD：每个新服务先写测试再实现
- [ ] 向后兼容：旧的 import 路径通过 re-export 桩继续工作
- [ ] 提交粒度：每个逻辑单元独立提交

---

**Plan 完成。执行选项：**

**1. Subagent-Driven（推荐）** — 每个 Task 由独立 subagent 执行，Task 间有审核节点，快速迭代

**2. Inline Execution** — 在当前 session 使用 executing-plans 执行，批量执行 + 检查点

选择哪个方式？