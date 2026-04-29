from typing import Annotated
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.auth import verify_token
from app.config import get_settings
from app.services.recorder import Recorder
from app.services.nas_syncer import NasSyncer

bearer = HTTPBearer(auto_error=False)

DBDep = Annotated[AsyncSession, Depends(get_db)]


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> str:
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    settings = get_settings()
    username = verify_token(credentials.credentials, settings.jwt_secret_key)
    if username is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")
    return username


async def get_stream_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    token: str | None = None,
) -> str:
    """Accept Bearer header OR ?token= query param — for endpoints used directly in <video>/<img> src."""
    raw = credentials.credentials if credentials else token
    if not raw:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    settings = get_settings()
    username = verify_token(raw, settings.jwt_secret_key)
    if username is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")
    return username


StreamUser = Annotated[str, Depends(get_stream_user)]


def get_recorder(request: Request) -> Recorder:
    return request.app.state.recorder


def get_nas_syncer(request: Request) -> NasSyncer:
    return request.app.state.nas_syncer


CurrentUser = Annotated[str, Depends(get_current_user)]
RecorderDep = Annotated[Recorder, Depends(get_recorder)]
NasSyncerDep = Annotated[NasSyncer, Depends(get_nas_syncer)]
