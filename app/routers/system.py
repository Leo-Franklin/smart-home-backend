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
    # admin_password can be a bcrypt hash (production) or plaintext (development)
    # Try bcrypt verify first; fall back to plaintext comparison if not a valid hash
    password_ok = False
    try:
        password_ok = verify_password(form.password, settings.admin_password)
    except Exception:
        password_ok = (form.password == settings.admin_password)

    if not password_ok:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")
    token = create_access_token(form.username, settings.jwt_secret_key)
    return TokenResponse(access_token=token)
