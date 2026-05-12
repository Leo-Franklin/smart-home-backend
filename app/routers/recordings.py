import math
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select, func
from pathlib import Path
from typing import Annotated
from app.deps import DBDep, CurrentUser, StreamUser
from app.models.recording import Recording
from app.schemas.recording import RecordingOut
from app.schemas import PagedResponse
from app.config import get_settings

router = APIRouter(prefix="/recordings", tags=["recordings"])


def _compute_recording_extra(file_path: str, settings) -> tuple[str, str | None, str]:
    """返回 (storage_type, nas_access_url, file_name)"""
    import os

    file_name = os.path.basename(file_path)
    local_storage = str(Path(settings.local_storage_path).resolve())
    nas_mount = settings.nas_mount_path.rstrip("/")

    if settings.nas_mode == "local":
        storage_type = "local"
        nas_access_url = None
    elif settings.nas_mode == "mount":
        if file_path.startswith(nas_mount) or nas_mount in file_path:
            storage_type = "nas"
            nas_access_url = None  # 挂载路径，前端无法直接打开
        else:
            storage_type = "local"
            nas_access_url = None
    elif settings.nas_mode == "smb":
        # UNC 路径：\\host\share\... → smb://host/share/...
        if file_path.startswith("\\\\"):
            storage_type = "nas"
            parts = file_path[2:].split("\\", 2)
            if len(parts) >= 2:
                host, share = parts[0], parts[1]
                rest = parts[2] if len(parts) > 2 else ""
                nas_access_url = f"smb://{host}/{share}/{rest}".rstrip("/")
            else:
                nas_access_url = None
        elif file_path.startswith(nas_mount):
            storage_type = "nas"
            # 挂载模式下，尝试从 smb_host/share 构造 URL
            if settings.nas_smb_host and settings.nas_smb_share:
                rel = file_path[len(nas_mount):].lstrip("/")
                nas_access_url = f"smb://{settings.nas_smb_host}/{settings.nas_smb_share}/{rel}".rstrip("/")
            else:
                nas_access_url = None
        else:
            storage_type = "local"
            nas_access_url = None
    else:
        storage_type = "local"
        nas_access_url = None

    return storage_type, nas_access_url, file_name


@router.get("", response_model=PagedResponse[RecordingOut])
async def list_recordings(
    db: DBDep,
    _: CurrentUser,
    camera_mac: str | None = None,
    date: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    q = select(Recording)
    if camera_mac:
        q = q.where(Recording.camera_mac == camera_mac)
    if date:
        q = q.where(func.date(Recording.started_at) == date)

    total_result = await db.execute(select(func.count()).select_from(q.subquery()))
    total = total_result.scalar_one()
    q = q.order_by(Recording.started_at.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(q)
    items = result.scalars().all()
    settings = get_settings()

    records = []
    for r in items:
        storage_type, nas_access_url, file_name = _compute_recording_extra(r.file_path, settings)
        records.append(RecordingOut(
            id=r.id,
            camera_mac=r.camera_mac,
            file_path=r.file_path,
            file_size=r.file_size,
            duration=r.duration,
            started_at=r.started_at,
            ended_at=r.ended_at,
            status=r.status,
            error_msg=r.error_msg,
            created_at=r.created_at,
            storage_type=storage_type,
            nas_access_url=nas_access_url,
            file_name=file_name,
        ))

    return PagedResponse(
        items=records, total=total, page=page, page_size=page_size,
        pages=math.ceil(total / page_size) if total else 0,
    )


@router.get("/stats")
async def get_recording_stats(
    db: DBDep,
    _: CurrentUser,
    range: str = Query("7d", pattern=r"^\d+d$"),
):
    days = int(range[:-1])
    since = datetime.now(timezone.utc) - timedelta(days=days)

    total_result = await db.execute(
        select(func.count(), func.sum(Recording.duration), func.sum(Recording.file_size))
        .where(Recording.started_at >= since)
    )
    count, total_duration, total_size = total_result.one()

    return {
        "range": range,
        "count": count or 0,
        "total_duration": total_duration or 0,
        "total_size": total_size or 0,
    }


@router.get("/{recording_id}", response_model=RecordingOut)
async def get_recording(recording_id: int, db: DBDep, _: CurrentUser):
    result = await db.execute(select(Recording).where(Recording.id == recording_id))
    recording = result.scalar_one_or_none()
    if not recording:
        raise HTTPException(status_code=404, detail="录像不存在")
    settings = get_settings()
    storage_type, nas_access_url, file_name = _compute_recording_extra(recording.file_path, settings)
    return RecordingOut(
        id=recording.id,
        camera_mac=recording.camera_mac,
        file_path=recording.file_path,
        file_size=recording.file_size,
        duration=recording.duration,
        started_at=recording.started_at,
        ended_at=recording.ended_at,
        status=recording.status,
        error_msg=recording.error_msg,
        created_at=recording.created_at,
        storage_type=storage_type,
        nas_access_url=nas_access_url,
        file_name=file_name,
    )


@router.get("/{recording_id}/stream")
async def stream_recording(recording_id: int, request: Request, db: DBDep, _: StreamUser):
    result = await db.execute(select(Recording).where(Recording.id == recording_id))
    recording = result.scalar_one_or_none()
    if not recording:
        raise HTTPException(status_code=404, detail="录像不存在")

    if recording.status not in ("completed", "synced"):
        raise HTTPException(status_code=409, detail="录像尚未完成，无法播放")

    file_path = Path(recording.file_path)
    settings = get_settings()
    storage_root = Path(settings.local_storage_path).resolve()
    # Reject relative paths — they resolve relative to CWD which may differ from storage_root
    if not file_path.is_absolute():
        raise HTTPException(status_code=403, detail="文件路径不合法")
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
            range_spec = range_header[len("bytes="):]  # safer strip than .replace()
            parts = range_spec.split("-", 1)
            if parts[0] == "":
                # Suffix range: bytes=-N means last N bytes
                suffix_len = int(parts[1])
                start = max(0, file_size - suffix_len)
                end = file_size - 1
            else:
                start = int(parts[0])
                end = int(parts[1]) if parts[1] else file_size - 1
        except (ValueError, IndexError):
            raise HTTPException(status_code=400, detail="Range 头格式错误")

        if start < 0 or end >= file_size or start > end:
            raise HTTPException(
                status_code=416,
                detail="Range 超出文件范围",
                headers={"Content-Range": f"bytes */{file_size}", "Accept-Ranges": "bytes"},
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


@router.get("/{recording_id}/download")
async def download_recording(recording_id: int, db: DBDep, _: StreamUser):
    result = await db.execute(select(Recording).where(Recording.id == recording_id))
    recording = result.scalar_one_or_none()
    if not recording:
        raise HTTPException(status_code=404, detail="录像不存在")

    if recording.status not in ("completed", "synced"):
        raise HTTPException(status_code=409, detail="录像尚未完成，无法下载")

    file_path = Path(recording.file_path)
    settings = get_settings()
    storage_root = Path(settings.local_storage_path).resolve()
    if not file_path.is_absolute():
        raise HTTPException(status_code=403, detail="文件路径不合法")
    try:
        resolved = file_path.resolve()
        if not resolved.is_relative_to(storage_root):
            raise HTTPException(status_code=403, detail="文件路径不合法")
    except ValueError:
        raise HTTPException(status_code=403, detail="文件路径不合法")

    if not file_path.exists():
        raise HTTPException(status_code=404, detail="文件不存在")

    file_size = file_path.stat().st_size
    filename = file_path.name

    def iter_full():
        with open(file_path, "rb") as f:
            while chunk := f.read(65536):
                yield chunk

    return StreamingResponse(
        iter_full(), media_type="video/mp4",
        headers={
            "Content-Length": str(file_size),
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


@router.delete("/{recording_id}", status_code=204)
async def delete_recording(recording_id: int, db: DBDep, _: CurrentUser):
    result = await db.execute(select(Recording).where(Recording.id == recording_id))
    recording = result.scalar_one_or_none()
    if not recording:
        raise HTTPException(status_code=404, detail="录像不存在")
    file_path = Path(recording.file_path)
    if file_path.exists():
        try:
            file_path.unlink()
        except OSError as e:
            raise HTTPException(status_code=409, detail=f"文件正在使用中，请先关闭播放器再删除：{e.strerror}")
    await db.delete(recording)
    await db.commit()
