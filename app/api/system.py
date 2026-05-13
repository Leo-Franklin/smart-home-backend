import time
import asyncio
import subprocess
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, Request
from fastapi.security import OAuth2PasswordRequestForm
from fastapi import HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select, func, not_, exists
from app.config import get_settings
from app.auth import verify_password, create_access_token
from app.deps import DBDep, CurrentUser
from app.models.member import Member, MemberDevice
from app.models.camera import Camera
from app.models.device import Device
from app.models.recording import Recording

router = APIRouter()
_start_time = time.time()
_ffmpeg_available: bool | None = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class HealthResponse(BaseModel):
    status: str
    checks: dict
    uptime_seconds: float
    version: str


def _check_ffmpeg() -> bool:
    global _ffmpeg_available
    if _ffmpeg_available is None:
        try:
            result = subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=3)
            _ffmpeg_available = result.returncode == 0
        except Exception:
            _ffmpeg_available = False
    return _ffmpeg_available


@router.get("/health", response_model=HealthResponse, tags=["system"])
async def health_check(request: Request):
    import asyncio
    from fastapi.responses import JSONResponse
    settings = get_settings()
    nas_syncer = request.app.state.nas_syncer
    loop = asyncio.get_running_loop()
    ffmpeg_ok, nas_ok = await asyncio.gather(
        loop.run_in_executor(None, _check_ffmpeg),
        loop.run_in_executor(None, nas_syncer.check_writable),
    )
    checks = {
        "database": True,
        "ffmpeg": ffmpeg_ok,
        "nas_writable": nas_ok,
    }
    all_ok = all(checks.values())
    response_data = HealthResponse(
        status="healthy" if all_ok else "degraded",
        checks=checks,
        uptime_seconds=round(time.time() - _start_time, 1),
        version=settings.app_version,
    )
    status_code = 200 if all_ok else 503
    return JSONResponse(content=response_data.model_dump(), status_code=status_code)


@router.post("/auth/login", response_model=TokenResponse, tags=["auth"])
async def login(form: OAuth2PasswordRequestForm = Depends()):
    settings = get_settings()
    if form.username != settings.admin_username:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")
    # admin_password can be a bcrypt hash (production) or plaintext (development)
    # Detect hash type explicitly rather than relying on exception handling
    stored = settings.admin_password
    if stored.startswith("$2b$") or stored.startswith("$2a$"):
        password_ok = verify_password(form.password, stored)
    else:
        # plaintext password (development only) — use constant-time comparison
        import hmac
        password_ok = hmac.compare_digest(form.password, stored)

    if not password_ok:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")
    token = create_access_token(form.username, settings.jwt_secret_key)
    return TokenResponse(access_token=token)


@router.get("/dashboard", tags=["system"])
async def dashboard(db: DBDep, _: CurrentUser):
    today_start = datetime.now(timezone.utc).replace(
        tzinfo=None, hour=0, minute=0, second=0, microsecond=0
    )

    async def scalar(stmt):
        result = await db.execute(stmt)
        return result.scalar_one()

    (
        members_home,
        members_total,
        cameras_recording,
        cameras_online,
        cameras_total,
        devices_online,
        devices_total,
        recordings_today_count,
        recordings_today_duration,
        unknown_devices_today,
    ) = await asyncio.gather(
        scalar(select(func.count()).select_from(Member).where(Member.is_home == True)),
        scalar(select(func.count()).select_from(Member)),
        scalar(select(func.count()).select_from(Camera).where(Camera.is_recording == True)),
        scalar(select(func.count()).select_from(Camera).where(Camera.is_online == True)),
        scalar(select(func.count()).select_from(Camera)),
        scalar(select(func.count()).select_from(Device).where(Device.is_online == True)),
        scalar(select(func.count()).select_from(Device)),
        scalar(
            select(func.count()).select_from(Recording)
            .where(Recording.started_at >= today_start)
        ),
        scalar(
            select(func.coalesce(func.sum(Recording.duration), 0))
            .where(Recording.started_at >= today_start)
            .where(Recording.status.in_(["completed", "synced"]))
        ),
        scalar(
            select(func.count()).select_from(Device)
            .where(Device.created_at >= today_start)
            .where(
                not_(exists(select(MemberDevice.mac).where(MemberDevice.mac == Device.mac)))
            )
        ),
    )

    return {
        "members_home": members_home,
        "members_total": members_total,
        "cameras_recording": cameras_recording,
        "cameras_online": cameras_online,
        "cameras_total": cameras_total,
        "devices_online": devices_online,
        "devices_total": devices_total,
        "recordings_today_count": recordings_today_count,
        "recordings_today_duration_seconds": recordings_today_duration,
        "unknown_devices_today": unknown_devices_today,
    }
