import math
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select, func
from pathlib import Path
from app.deps import DBDep, CurrentUser
from app.models.recording import Recording
from app.schemas.recording import RecordingOut
from app.schemas import PagedResponse

router = APIRouter(prefix="/recordings", tags=["recordings"])


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

    return PagedResponse(
        items=items, total=total, page=page, page_size=page_size,
        pages=math.ceil(total / page_size) if total else 0,
    )


@router.get("/{recording_id}", response_model=RecordingOut)
async def get_recording(recording_id: int, db: DBDep, _: CurrentUser):
    result = await db.execute(select(Recording).where(Recording.id == recording_id))
    recording = result.scalar_one_or_none()
    if not recording:
        raise HTTPException(status_code=404, detail="录像不存在")
    return recording


@router.get("/{recording_id}/stream")
async def stream_recording(recording_id: int, request: Request, db: DBDep, _: CurrentUser):
    result = await db.execute(select(Recording).where(Recording.id == recording_id))
    recording = result.scalar_one_or_none()
    if not recording:
        raise HTTPException(status_code=404, detail="录像不存在")

    file_path = Path(recording.file_path)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="文件不存在")

    file_size = file_path.stat().st_size
    range_header = request.headers.get("range")

    if range_header:
        range_spec = range_header.replace("bytes=", "")
        parts = range_spec.split("-")
        start = int(parts[0])
        end = int(parts[1]) if parts[1] else file_size - 1
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


@router.delete("/{recording_id}", status_code=204)
async def delete_recording(recording_id: int, db: DBDep, _: CurrentUser):
    result = await db.execute(select(Recording).where(Recording.id == recording_id))
    recording = result.scalar_one_or_none()
    if not recording:
        raise HTTPException(status_code=404, detail="录像不存在")
    file_path = Path(recording.file_path)
    if file_path.exists():
        file_path.unlink()
    await db.delete(recording)
    await db.commit()
