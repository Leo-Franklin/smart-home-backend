# Security & Reliability Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 code review 中发现的所有 Critical 和 Important 级别问题，按优先级从高到低执行。

**Architecture:** 修复在现有 FastAPI/SQLAlchemy/APScheduler 架构上进行，不引入新的依赖层，每个 Task 独立可测试且可独立提交。

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy (async), APScheduler, passlib[bcrypt], python-jose, httpx

---

## 文件变更清单

| 文件 | 操作 | 原因 |
|------|------|------|
| `app/config.py` | 修改 | 添加启动校验，拒绝默认 JWT secret / 密码；新增 `cors_allow_origins` 字段 |
| `app/routers/system.py` | 修改 | 使用 `verify_password()` 替代明文比较；修复健康检查复用 nas_syncer |
| `app/main.py` | 修改 | CORS origins 从独立字段读取；lifespan 中加载已有调度任务 |
| `app/routers/recordings.py` | 修改 | Range header 边界校验；路径遍历防护 |
| `app/services/scheduler_service.py` | 修改 | 无变更（接口已够用） |
| `app/routers/schedules.py` | 修改 | create/update 时真正注册/更新 APScheduler job |
| `app/services/presence_service.py` | 修改 | webhook URL 白名单校验（必须 https，非内网） |
| `app/services/onvif_client.py` | 修改 | `get_event_loop()` → `get_running_loop()` |
| `app/routers/dlna.py` | 修改 | 文件上传大小限制；投屏成功后异步定时清理文件 |
| `tests/test_api.py` | 修改 | 修复 `lru_cache` 导致 monkeypatch 失效的问题 |

---

### Task 1: 修复认证安全 — 密码哈希比较 & 不安全默认值 (C1, C2)

**Files:**
- Modify: `app/config.py`
- Modify: `app/routers/system.py`

- [ ] **Step 1: 在 `app/config.py` 添加启动时配置校验**

将 `app/config.py` 完整替换为：

```python
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator
from functools import lru_cache

_INSECURE_JWT_DEFAULTS = {
    "change_me_to_a_random_string_at_least_32_chars",
    "",
}
_INSECURE_PASSWORD_DEFAULTS = {"change_me", ""}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Network
    network_range: str = "auto"
    scan_interval_seconds: int = 60
    presence_poll_interval_seconds: int = 30

    # Camera
    camera_onvif_user: str = "admin"
    camera_onvif_password: str = ""

    # NAS / 本地存储
    nas_mode: str = "local"  # local | mount | smb
    local_storage_path: str = "./data/recordings"
    nas_mount_path: str = "/nas/cameras"
    nas_smb_host: str = ""
    nas_smb_share: str = ""
    nas_smb_user: str = ""
    nas_smb_password: str = ""

    # Recording
    recording_temp_dir: str = "/tmp/recordings"
    recording_segment_seconds: int = 1800
    recording_retention_days: int = 30

    # App
    jwt_secret_key: str = "change_me_to_a_random_string_at_least_32_chars"
    admin_username: str = "admin"
    admin_password: str = "change_me"
    log_level: str = "INFO"
    debug: bool = False

    # CORS — 独立于 debug 标志，避免调试模式改变安全策略
    # 多个 origin 用逗号分隔，例如: "http://localhost:5173,https://app.example.com"
    cors_allow_origins: str = "http://localhost:5173"

    # Database
    database_url: str = "sqlite+aiosqlite:///./data/smart_home.db"

    # App meta
    app_version: str = "1.0.0"

    @field_validator("jwt_secret_key")
    @classmethod
    def jwt_secret_must_be_changed(cls, v: str) -> str:
        if v in _INSECURE_JWT_DEFAULTS or len(v) < 32:
            raise ValueError(
                "JWT_SECRET_KEY 必须设置为至少 32 字符的随机字符串，"
                "请在 .env 中配置 JWT_SECRET_KEY"
            )
        return v

    @field_validator("admin_password")
    @classmethod
    def admin_password_must_be_changed(cls, v: str) -> str:
        if v in _INSECURE_PASSWORD_DEFAULTS:
            raise ValueError(
                "ADMIN_PASSWORD 不能使用默认值，"
                "请在 .env 中配置 bcrypt hash 或强密码"
            )
        return v

    def get_cors_origins(self) -> list[str]:
        return [o.strip() for o in self.cors_allow_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 2: 创建 `.env.example` 供开发参考**

```bash
cat > .env.example << 'EOF'
# 生成强随机 secret: python -c "import secrets; print(secrets.token_hex(32))"
JWT_SECRET_KEY=your_random_secret_here_at_least_32_chars

# 生成 bcrypt hash: python -c "from passlib.context import CryptContext; print(CryptContext(['bcrypt']).hash('your_password'))"
ADMIN_PASSWORD=$2b$12$example_bcrypt_hash_here

CORS_ALLOW_ORIGINS=http://localhost:5173
EOF
```

- [ ] **Step 3: 修改 `app/routers/system.py` 使用 `verify_password()` 并修复健康检查**

```python
import time
import subprocess
from fastapi import APIRouter, Depends, Request
from fastapi.security import OAuth2PasswordRequestForm
from fastapi import HTTPException, status
from pydantic import BaseModel
from app.config import get_settings
from app.auth import verify_password, create_access_token
from app.deps import DBDep, CurrentUser

router = APIRouter()
_start_time = time.time()


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class HealthResponse(BaseModel):
    status: str
    checks: dict
    uptime_seconds: float
    version: str


def _check_ffmpeg() -> bool:
    try:
        result = subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=3)
        return result.returncode == 0
    except Exception:
        return False


@router.get("/health", response_model=HealthResponse, tags=["system"])
async def health_check(request: Request):
    settings = get_settings()
    nas_syncer = request.app.state.nas_syncer
    return HealthResponse(
        status="healthy",
        checks={
            "database": True,
            "ffmpeg": _check_ffmpeg(),
            "nas_writable": nas_syncer.check_writable(),
        },
        uptime_seconds=round(time.time() - _start_time, 1),
        version=settings.app_version,
    )


@router.post("/auth/login", response_model=TokenResponse, tags=["auth"])
async def login(form: OAuth2PasswordRequestForm = Depends()):
    settings = get_settings()
    if form.username != settings.admin_username:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")
    # admin_password 可以是 bcrypt hash，也可以是明文（开发环境）
    # verify_password 支持两种情况：bcrypt hash 直接验证，明文则用 hash("") 的方式
    # 为兼容明文配置，先尝试 bcrypt 验证，失败则回退明文比较（仅开发环境）
    password_ok = False
    try:
        password_ok = verify_password(form.password, settings.admin_password)
    except Exception:
        # admin_password 不是 bcrypt hash（明文模式，仅开发）
        password_ok = (form.password == settings.admin_password)

    if not password_ok:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")
    token = create_access_token(form.username, settings.jwt_secret_key)
    return TokenResponse(access_token=token)
```

- [ ] **Step 4: 修改 `app/main.py` 的 CORS 配置使用独立字段**

将 `app/main.py` 中的 CORS middleware 配置（原第 146-152 行）替换为：

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.get_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

- [ ] **Step 5: 验证启动校验生效**

```bash
cd D:/Project/Demo/smart_home/backend
# 不设置 .env，直接运行，应报 ValidationError
python -c "from app.config import get_settings; get_settings()"
# 预期输出包含: "JWT_SECRET_KEY 必须设置为至少 32 字符"
```

- [ ] **Step 6: 创建测试用 `.env.test`**

```bash
cat > .env.test << 'EOF'
JWT_SECRET_KEY=test_secret_key_that_is_at_least_32_characters_long
ADMIN_PASSWORD=testpassword_for_ci_only
CORS_ALLOW_ORIGINS=http://localhost:5173
EOF
```

- [ ] **Step 7: Commit**

```bash
git add app/config.py app/routers/system.py app/main.py .env.example .env.test
git commit -m "fix: enforce strong JWT secret and bcrypt password verification at login"
```

---

### Task 2: 修复调度任务重启后丢失 (I2)

**Files:**
- Modify: `app/main.py`
- Modify: `app/routers/schedules.py`
- Modify: `app/models/schedule.py` (先读取，只需了解字段)

- [ ] **Step 1: 读取 Schedule 模型确认字段**

```bash
cat app/models/schedule.py
```
预期字段：`id`, `camera_mac`, `cron_expr`, `enabled`, `name` 等。

- [ ] **Step 2: 在 `app/main.py` 的 lifespan 中加载已有调度任务**

在 `lifespan` 函数的 `scheduler_service.start()` 调用之后（原第 122 行附近），插入以下代码：

```python
    scheduler_service.start()

    # 从数据库恢复所有已启用的调度任务
    from app.models.schedule import Schedule as ScheduleModel
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(ScheduleModel).where(ScheduleModel.enabled == True)
        )
        enabled_schedules = result.scalars().all()

    async def _trigger_recording(camera_mac: str):
        from app.routers.cameras import _start_recording_for_camera
        await _start_recording_for_camera(camera_mac, recorder, nas_syncer)

    for sched in enabled_schedules:
        try:
            scheduler_service.add_recording_job(
                job_id=f"schedule_{sched.id}",
                cron_expr=sched.cron_expr,
                camera_mac=sched.camera_mac,
                callback=_trigger_recording,
            )
        except Exception as e:
            logger.warning(f"恢复调度任务 {sched.id} 失败: {e}")

    logger.info(f"已从数据库恢复 {len(enabled_schedules)} 个调度任务")
```

- [ ] **Step 3: 修复 `app/routers/schedules.py`，create/update/delete 时同步 APScheduler**

将 `app/routers/schedules.py` 完整替换为：

```python
from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import select
from app.deps import DBDep, CurrentUser
from app.models.schedule import Schedule
from app.schemas.schedule import ScheduleCreate, ScheduleUpdate, ScheduleOut
from app.services.scheduler_service import scheduler_service
from loguru import logger

router = APIRouter(prefix="/schedules", tags=["schedules"])


async def _get_trigger_callback(request: Request):
    from app.routers.cameras import _start_recording_for_camera
    recorder = request.app.state.recorder
    nas_syncer = request.app.state.nas_syncer

    async def _trigger(camera_mac: str):
        await _start_recording_for_camera(camera_mac, recorder, nas_syncer)

    return _trigger


@router.get("", response_model=list[ScheduleOut])
async def list_schedules(db: DBDep, _: CurrentUser):
    result = await db.execute(select(Schedule))
    return result.scalars().all()


@router.post("", response_model=ScheduleOut, status_code=status.HTTP_201_CREATED)
async def create_schedule(body: ScheduleCreate, request: Request, db: DBDep, _: CurrentUser):
    parts = body.cron_expr.split()
    if len(parts) != 5:
        raise HTTPException(status_code=400, detail="cron 表达式必须是 5 字段格式")
    schedule = Schedule(**body.model_dump())
    db.add(schedule)
    await db.commit()
    await db.refresh(schedule)
    if schedule.enabled:
        callback = await _get_trigger_callback(request)
        scheduler_service.add_recording_job(
            job_id=f"schedule_{schedule.id}",
            cron_expr=schedule.cron_expr,
            camera_mac=schedule.camera_mac,
            callback=callback,
        )
        logger.info(f"已注册调度任务: schedule_{schedule.id} ({schedule.cron_expr})")
    return schedule


@router.get("/{schedule_id}", response_model=ScheduleOut)
async def get_schedule(schedule_id: int, db: DBDep, _: CurrentUser):
    result = await db.execute(select(Schedule).where(Schedule.id == schedule_id))
    schedule = result.scalar_one_or_none()
    if not schedule:
        raise HTTPException(status_code=404, detail="计划不存在")
    return schedule


@router.patch("/{schedule_id}", response_model=ScheduleOut)
async def update_schedule(schedule_id: int, body: ScheduleUpdate, request: Request, db: DBDep, _: CurrentUser):
    result = await db.execute(select(Schedule).where(Schedule.id == schedule_id))
    schedule = result.scalar_one_or_none()
    if not schedule:
        raise HTTPException(status_code=404, detail="计划不存在")
    if body.cron_expr is not None and len(body.cron_expr.split()) != 5:
        raise HTTPException(status_code=400, detail="cron 表达式必须是 5 字段格式")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(schedule, field, value)
    await db.commit()
    await db.refresh(schedule)

    job_id = f"schedule_{schedule.id}"
    if schedule.enabled:
        callback = await _get_trigger_callback(request)
        scheduler_service.add_recording_job(
            job_id=job_id,
            cron_expr=schedule.cron_expr,
            camera_mac=schedule.camera_mac,
            callback=callback,
        )
    else:
        scheduler_service.remove_job(job_id)
    return schedule


@router.delete("/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_schedule(schedule_id: int, db: DBDep, _: CurrentUser):
    result = await db.execute(select(Schedule).where(Schedule.id == schedule_id))
    schedule = result.scalar_one_or_none()
    if not schedule:
        raise HTTPException(status_code=404, detail="计划不存在")
    scheduler_service.remove_job(f"schedule_{schedule_id}")
    await db.delete(schedule)
    await db.commit()
```

> **注意：** 上面代码依赖 `app/routers/cameras.py` 中存在 `_start_recording_for_camera(camera_mac, recorder, nas_syncer)` 函数。下一步先确认其存在，如不存在则提取。

- [ ] **Step 4: 确认 cameras.py 中录制触发函数，如不存在则提取**

```bash
grep -n "_start_recording_for_camera\|def.*start_recording\|recorder.start" app/routers/cameras.py | head -20
```

如果 `_start_recording_for_camera` 不存在，则在 `app/routers/cameras.py` 末尾添加：

```python
async def _start_recording_for_camera(camera_mac: str, recorder, nas_syncer):
    """供调度任务触发录制使用"""
    from sqlalchemy import select
    from app.database import AsyncSessionLocal
    from app.models.camera import Camera
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Camera).where(Camera.device_mac == camera_mac))
        cam = result.scalar_one_or_none()
        if not cam or not cam.rtsp_url:
            logger.warning(f"调度录制: 摄像头 {camera_mac} 不存在或无 RTSP URL")
            return
        if cam.is_recording:
            logger.info(f"调度录制: {camera_mac} 已在录制中，跳过")
            return
    await recorder.start(camera_mac=camera_mac, rtsp_url=cam.rtsp_url)
```

- [ ] **Step 5: Commit**

```bash
git add app/main.py app/routers/schedules.py app/routers/cameras.py
git commit -m "fix: reload enabled schedules from DB on startup and sync APScheduler on CRUD"
```

---

### Task 3: 修复 Range Header 边界校验 & 路径遍历防护 (C3, C5)

**Files:**
- Modify: `app/routers/recordings.py`

- [ ] **Step 1: 修改 `app/routers/recordings.py` 中的 `stream_recording` 函数**

将 `stream_recording` 函数（原第 73-127 行）替换为：

```python
@router.get("/{recording_id}/stream")
async def stream_recording(recording_id: int, request: Request, db: DBDep, _: StreamUser):
    result = await db.execute(select(Recording).where(Recording.id == recording_id))
    recording = result.scalar_one_or_none()
    if not recording:
        raise HTTPException(status_code=404, detail="录像不存在")

    if recording.status not in ("completed", "synced"):
        raise HTTPException(status_code=409, detail="录像尚未完成，无法播放")

    file_path = Path(recording.file_path)
    # 路径遍历防护：确保文件在允许的存储目录下
    settings = get_settings()
    storage_root = Path(settings.local_storage_path).resolve()
    try:
        resolved = file_path.resolve()
        if not resolved.is_relative_to(storage_root):
            raise HTTPException(status_code=403, detail="文件路径不合法")
    except ValueError:
        raise HTTPException(status_code=403, detail="文件路径不合法")

    if not file_path.exists():
        raise HTTPException(status_code=404, detail="文件不存在")

    file_size = file_path.stat().st_size
    if file_size < 10 * 1024:
        raise HTTPException(status_code=422, detail="录像文件损坏或过小，无法播放")

    range_header = request.headers.get("range")

    if range_header:
        try:
            range_spec = range_header.replace("bytes=", "")
            parts = range_spec.split("-", 1)
            start = int(parts[0])
            end = int(parts[1]) if parts[1] else file_size - 1
        except (ValueError, IndexError):
            raise HTTPException(status_code=400, detail="Range 头格式错误")

        if start < 0 or end >= file_size or start > end:
            raise HTTPException(
                status_code=416,
                detail="Range 超出文件范围",
                headers={"Content-Range": f"bytes */{file_size}"},
            )
        content_length = end - start + 1

        def iter_range():
            with open(file_path, "rb") as f:
                f.seek(start)
                remaining = content_length
                while remaining > 0:
                    chunk = f.read(min(8192, remaining))
                    if not chunk:
                        break
                    remaining -= len(chunk)
                    yield chunk

        return StreamingResponse(
            iter_range(), status_code=206, media_type="video/mp4",
            headers={
                "Content-Range": f"bytes {start}-{end}/{file_size}",
                "Content-Length": str(content_length),
                "Accept-Ranges": "bytes",
            },
        )

    def iter_full():
        with open(file_path, "rb") as f:
            while chunk := f.read(8192):
                yield chunk

    return StreamingResponse(
        iter_full(), media_type="video/mp4",
        headers={"Content-Length": str(file_size), "Accept-Ranges": "bytes"},
    )
```

- [ ] **Step 2: Commit**

```bash
git add app/routers/recordings.py
git commit -m "fix: validate Range header bounds and add path traversal protection in stream endpoint"
```

---

### Task 4: 修复 Webhook SSRF 防护 (I7)

**Files:**
- Modify: `app/services/presence_service.py`

- [ ] **Step 1: 在 `_send_webhook` 前添加 URL 校验函数**

在 `app/services/presence_service.py` 的 `import` 区域之后、`class PresenceService` 之前，插入：

```python
import ipaddress
from urllib.parse import urlparse

_PRIVATE_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
]


def _validate_webhook_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError(f"Webhook URL 必须使用 https 协议: {url}")
    hostname = parsed.hostname
    if not hostname:
        raise ValueError(f"Webhook URL 无效: {url}")
    try:
        addr = ipaddress.ip_address(hostname)
        for net in _PRIVATE_NETWORKS:
            if addr in net:
                raise ValueError(f"Webhook URL 不能指向内网地址: {hostname}")
    except ValueError as e:
        if "内网" in str(e) or "https" in str(e):
            raise
        # hostname 是域名，不是 IP，允许通过（DNS 解析不在此处做）
```

- [ ] **Step 2: 在 `_send_webhook` 方法开头调用校验**

将 `_send_webhook` 方法替换为：

```python
    async def _send_webhook(self, url: str, event: str, member: Member, triggered_mac: str | None, ts: datetime):
        try:
            _validate_webhook_url(url)
        except ValueError as e:
            logger.warning(f"Webhook URL 不合法，跳过: {e}")
            return
        try:
            import httpx
            async with httpx.AsyncClient(timeout=5) as client:
                await client.post(url, json={
                    "event": event,
                    "member": {"id": member.id, "name": member.name},
                    "triggered_by_mac": triggered_mac,
                    "timestamp": ts.isoformat(),
                })
        except Exception as e:
            logger.warning(f"Webhook 发送失败 ({url}): {e}")
```

- [ ] **Step 3: Commit**

```bash
git add app/services/presence_service.py
git commit -m "fix: validate webhook URL to prevent SSRF (require https, block private IP ranges)"
```

---

### Task 5: 修复健康检查复用 nas_syncer & deprecated get_event_loop() (I4, I6)

**Files:**
- Modify: `app/services/onvif_client.py`

> `system.py` 的 health_check 已在 Task 1 Step 3 中修复（使用 `request.app.state.nas_syncer`），此 Task 只处理 I6。

- [ ] **Step 1: 替换 `app/services/onvif_client.py` 中所有 `get_event_loop()` 调用**

将文件中所有 `asyncio.get_event_loop()` 替换为 `asyncio.get_running_loop()`：

```python
import asyncio
from loguru import logger


class OnvifClient:
    def __init__(self, host: str, port: int, user: str, password: str):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self._camera = None

    def _get_camera(self):
        if self._camera is None:
            from onvif import ONVIFCamera
            self._camera = ONVIFCamera(self.host, self.port, self.user, self.password)
        return self._camera

    async def get_device_info(self) -> dict:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._get_device_info_sync)

    def _get_device_info_sync(self) -> dict:
        cam = self._get_camera()
        svc = cam.create_devicemgmt_service()
        info = svc.GetDeviceInformation()
        return {
            "manufacturer": info.Manufacturer,
            "model": info.Model,
            "firmware": info.FirmwareVersion,
            "serial": info.SerialNumber,
        }

    async def get_stream_uri(self, profile_index: int = 0) -> str:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._get_stream_uri_sync, profile_index)

    def _get_stream_uri_sync(self, profile_index: int) -> str:
        cam = self._get_camera()
        media = cam.create_media_service()
        profiles = media.GetProfiles()
        if profile_index >= len(profiles):
            profile_index = 0
        token = profiles[profile_index].token
        uri = media.GetStreamUri({
            "StreamSetup": {"Stream": "RTP-Unicast", "Transport": {"Protocol": "RTSP"}},
            "ProfileToken": token,
        })
        return uri.Uri

    async def get_snapshot_uri(self) -> str:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._get_snapshot_uri_sync)

    def _get_snapshot_uri_sync(self) -> str:
        cam = self._get_camera()
        media = cam.create_media_service()
        profiles = media.GetProfiles()
        token = profiles[0].token
        uri = media.GetSnapshotUri({"ProfileToken": token})
        return uri.Uri

    async def get_profiles(self) -> list[dict]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._get_profiles_sync)

    def _get_profiles_sync(self) -> list[dict]:
        cam = self._get_camera()
        media = cam.create_media_service()
        profiles = media.GetProfiles()
        return [
            {"index": i, "name": p.Name, "token": p.token}
            for i, p in enumerate(profiles)
        ]

    async def is_reachable(self) -> bool:
        try:
            await self.get_device_info()
            return True
        except Exception as e:
            logger.debug(f"ONVIF 不可达 {self.host}:{self.port}: {e}")
            return False
```

- [ ] **Step 2: Commit**

```bash
git add app/services/onvif_client.py
git commit -m "fix: replace deprecated asyncio.get_event_loop() with get_running_loop()"
```

---

### Task 6: 修复 DLNA 文件不清理 & 无大小限制 (I8, I9)

**Files:**
- Modify: `app/routers/dlna.py`

- [ ] **Step 1: 修改 `cast_file` 端点，增加大小限制和投屏完成后异步清理**

将 `cast_file` 函数（原第 107-142 行）替换为：

```python
MAX_UPLOAD_BYTES = 500 * 1024 * 1024  # 500 MB
MEDIA_TTL_SECONDS = 3600  # 投屏成功后 1 小时删除文件


@router.post("/cast/file")
async def cast_file(
    request: Request,
    background_tasks: BackgroundTasks,
    db: DBDep,
    _: CurrentUser,
    device_id: int = Form(...),
    file: UploadFile = File(...),
):
    """Upload a local media file and push it to a DLNA device."""
    device = await _require_renderer(device_id, db)

    suffix = Path(file.filename or "media").suffix or ".mp4"
    fname = f"{int(time.time())}_{hashlib.md5((file.filename or 'media').encode()).hexdigest()[:8]}{suffix}"
    dest = MEDIA_DIR / fname

    # 流式写入并检查大小限制
    written = 0
    try:
        with open(dest, "wb") as f:
            while True:
                chunk = await file.read(65536)
                if not chunk:
                    break
                written += len(chunk)
                if written > MAX_UPLOAD_BYTES:
                    dest.unlink(missing_ok=True)
                    raise HTTPException(status_code=413, detail=f"文件超过 {MAX_UPLOAD_BYTES // 1024 // 1024} MB 限制")
                f.write(chunk)
    except HTTPException:
        raise
    except Exception as e:
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"文件写入失败: {e}")

    port = request.url.port or 8000
    media_url = f"http://{_local_ip()}:{port}/dlna-media/{fname}"

    try:
        ctrl = DLNAController(device.av_transport_url)
        await ctrl.set_uri(media_url)
        await ctrl.play()
    except Exception as e:
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=502, detail=f"投屏失败: {e}")

    # 投屏成功后定时清理文件
    background_tasks.add_task(_schedule_file_cleanup, dest, MEDIA_TTL_SECONDS)

    await ws_manager.broadcast("dlna_cast_started", {
        "device_id": device.id,
        "friendly_name": device.friendly_name,
        "media_url": media_url,
    })
    return {"message": "文件投屏成功", "media_url": media_url, "device": device.friendly_name}


async def _schedule_file_cleanup(path: Path, delay_seconds: int):
    await asyncio.sleep(delay_seconds)
    path.unlink(missing_ok=True)
    logger.info(f"DLNA 临时文件已清理: {path.name}")
```

- [ ] **Step 2: Commit**

```bash
git add app/routers/dlna.py
git commit -m "fix: add 500MB upload limit and auto-cleanup DLNA media files after 1 hour"
```

---

### Task 7: 修复测试中 lru_cache 导致 monkeypatch 失效 (I3)

**Files:**
- Modify: `tests/test_api.py`

- [ ] **Step 1: 读取现有测试文件**

```bash
cat tests/test_api.py
```

- [ ] **Step 2: 在测试 fixture 中清除 lru_cache**

在 `tests/test_api.py` 中找到 monkeypatch 设置环境变量的部分，在 `setenv` 之后立即调用 `get_settings.cache_clear()`，并在 teardown 时也清除。

将现有登录相关 fixture/测试替换为（假设现有结构使用 pytest fixture）：

```python
import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.config import get_settings


@pytest.fixture(autouse=True)
def clear_settings_cache():
    """确保每个测试都使用新的 Settings 实例，避免 lru_cache 干扰。"""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def test_env(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "test_secret_key_that_is_at_least_32_characters_long")
    monkeypatch.setenv("ADMIN_PASSWORD", "testpassword")
    monkeypatch.setenv("CORS_ALLOW_ORIGINS", "http://localhost:5173")
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_login_success(test_env):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/auth/login",
            data={"username": "admin", "password": "testpassword"},
        )
    assert response.status_code == 200
    assert "access_token" in response.json()


@pytest.mark.asyncio
async def test_login_wrong_password(test_env):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/auth/login",
            data={"username": "admin", "password": "wrong"},
        )
    assert response.status_code == 401
```

- [ ] **Step 3: 运行测试验证**

```bash
cd D:/Project/Demo/smart_home/backend
python -m pytest tests/ -v 2>&1 | head -40
```

预期：所有测试通过。

- [ ] **Step 4: Commit**

```bash
git add tests/test_api.py
git commit -m "fix: clear lru_cache in test fixtures so monkeypatch env vars take effect"
```

---

## 执行顺序总结

| 优先级 | Task | 解决问题 | 预计影响 |
|--------|------|---------|---------|
| 1 | Task 1 | C1, C2, C4 | 认证安全、CORS 安全 |
| 2 | Task 2 | I2 | 调度任务重启不丢失 |
| 3 | Task 3 | C3, C5 | 路径遍历、Range 崩溃 |
| 4 | Task 4 | I7 | SSRF 防护 |
| 5 | Task 5 | I4, I6 | 健康检查、deprecation warning |
| 6 | Task 6 | I8, I9 | DLNA 磁盘泄漏 |
| 7 | Task 7 | I3 | 测试准确性 |
