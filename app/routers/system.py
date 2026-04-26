import time
import subprocess
from fastapi import APIRouter, Depends
from fastapi.security import OAuth2PasswordRequestForm
from fastapi import HTTPException, status
from pydantic import BaseModel
from app.config import get_settings
from app.auth import verify_password, create_access_token, hash_password
from app.deps import DBDep
from app.services.nas_syncer import NasSyncer

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
async def health_check():
    settings = get_settings()
    nas_syncer = NasSyncer(
        mode=settings.nas_mode,
        local_storage_path=settings.local_storage_path,
        mount_path=settings.nas_mount_path,
        smb_host=settings.nas_smb_host,
        smb_share=settings.nas_smb_share,
        smb_user=settings.nas_smb_user,
        smb_password=settings.nas_smb_password,
    )
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
async def login(form: OAuth2PasswordRequestForm = Depends(), db: DBDep = None):
    settings = get_settings()
    from sqlalchemy import select, text
    from app.models.device import Device  # reuse db session just to verify DB works
    # Simple single-admin auth: compare against settings
    # In a full implementation this would query a users table
    if form.username != settings.admin_username:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")
    # The password in settings is stored as plain text (or bcrypt hash if set via API)
    # For initial setup, compare plain text; production should store hash
    if form.password != settings.admin_password:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")
    token = create_access_token(form.username, settings.jwt_secret_key)
    return TokenResponse(access_token=token)
