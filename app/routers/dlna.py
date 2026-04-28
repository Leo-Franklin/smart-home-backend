import asyncio
import hashlib
import socket
import time
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, Request, UploadFile, status
from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.deps import CurrentUser, DBDep
from app.models.dlna_device import DLNADevice
from app.schemas.dlna import CastRequest, DLNADeviceOut, TransportInfoOut
from app.services.dlna_service import DLNAController, fetch_device_info, ssdp_search
from app.services.ws_manager import ws_manager
from loguru import logger

router = APIRouter(prefix="/dlna", tags=["dlna"])

MEDIA_DIR = Path("data/dlna_media")
MEDIA_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_MEDIA_SUFFIXES = {".mp4", ".mkv", ".avi", ".mov", ".ts", ".mp3", ".m4a", ".flac", ".wav", ".m3u8"}

MAX_UPLOAD_BYTES = 500 * 1024 * 1024  # 500 MB
MEDIA_TTL_SECONDS = 3600  # 1 hour


def _local_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"


# ── Discovery ──────────────────────────────────────────────────────────────────

@router.post("/discover", status_code=status.HTTP_202_ACCEPTED)
async def discover_devices(background_tasks: BackgroundTasks, _: CurrentUser):
    """Trigger SSDP scan for DLNA MediaRenderer devices on the local network."""
    background_tasks.add_task(_run_discover)
    return {"message": "DLNA 发现已启动，结果通过 WebSocket 推送"}


async def _run_discover():
    await ws_manager.broadcast("dlna_discover_started", {})
    try:
        locations = await ssdp_search(timeout=5.0)
        logger.info(f"SSDP 发现 {len(locations)} 个响应，开始解析设备描述")

        infos = await asyncio.gather(*(fetch_device_info(loc) for loc in locations))
        found = [d for d in infos if d is not None]

        new_count = 0
        async with AsyncSessionLocal() as db:
            now = datetime.now()
            for info in found:
                existing = (await db.execute(
                    select(DLNADevice).where(DLNADevice.udn == info["udn"])
                )).scalar_one_or_none()

                if existing:
                    for k, v in info.items():
                        setattr(existing, k, v)
                    existing.is_online = True
                    existing.last_seen = now
                else:
                    new_count += 1
                    db.add(DLNADevice(**info, is_online=True, last_seen=now))

            await db.commit()

        await ws_manager.broadcast("dlna_discover_completed", {"found": len(found), "new": new_count})
        logger.info(f"DLNA 发现完成: 找到 {len(found)} 台 MediaRenderer, 新增 {new_count} 台")
    except Exception as e:
        logger.error(f"DLNA 发现失败: {e}")
        await ws_manager.broadcast("dlna_discover_completed", {"error": str(e)})


# ── Device list ────────────────────────────────────────────────────────────────

@router.get("", response_model=list[DLNADeviceOut])
async def list_dlna_devices(db: DBDep, _: CurrentUser):
    result = await db.execute(select(DLNADevice).order_by(DLNADevice.id))
    return result.scalars().all()


# ── Cast ───────────────────────────────────────────────────────────────────────

@router.post("/cast")
async def cast_url(body: CastRequest, db: DBDep, _: CurrentUser):
    """Push an external media URL directly to a DLNA device."""
    device = await _require_renderer(body.device_id, db)
    try:
        ctrl = DLNAController(device.av_transport_url)
        await ctrl.set_uri(body.media_url)
        await ctrl.play()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"投屏失败: {e}")

    await ws_manager.broadcast("dlna_cast_started", {
        "device_id": device.id,
        "friendly_name": device.friendly_name,
        "media_url": body.media_url,
    })
    return {"message": "投屏成功", "device": device.friendly_name}


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

    suffix = Path(file.filename or "media").suffix.lower() or ".mp4"
    if suffix not in ALLOWED_MEDIA_SUFFIXES:
        raise HTTPException(
            status_code=415,
            detail=f"不支持的文件格式: {suffix}，允许格式: {', '.join(sorted(ALLOWED_MEDIA_SUFFIXES))}",
        )
    fname = f"{int(time.time())}_{hashlib.md5((file.filename or 'media').encode()).hexdigest()[:8]}{suffix}"
    dest = MEDIA_DIR / fname

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
                    raise HTTPException(
                        status_code=413,
                        detail=f"文件超过 {MAX_UPLOAD_BYTES // 1024 // 1024} MB 限制",
                    )
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

    background_tasks.add_task(_cleanup_media_file, dest, MEDIA_TTL_SECONDS)

    await ws_manager.broadcast("dlna_cast_started", {
        "device_id": device.id,
        "friendly_name": device.friendly_name,
        "media_url": media_url,
    })
    return {"message": "文件投屏成功", "media_url": media_url, "device": device.friendly_name}


async def _cleanup_media_file(path: Path, delay_seconds: int):
    await asyncio.sleep(delay_seconds)
    path.unlink(missing_ok=True)
    logger.info(f"DLNA 临时文件已清理: {path.name}")


# ── Playback control ───────────────────────────────────────────────────────────

@router.post("/{device_id}/play", status_code=status.HTTP_204_NO_CONTENT)
async def play(device_id: int, db: DBDep, _: CurrentUser):
    device = await _require_renderer(device_id, db)
    try:
        await DLNAController(device.av_transport_url).play()
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.post("/{device_id}/pause", status_code=status.HTTP_204_NO_CONTENT)
async def pause(device_id: int, db: DBDep, _: CurrentUser):
    device = await _require_renderer(device_id, db)
    try:
        await DLNAController(device.av_transport_url).pause()
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.post("/{device_id}/stop", status_code=status.HTTP_204_NO_CONTENT)
async def stop(device_id: int, db: DBDep, _: CurrentUser):
    device = await _require_renderer(device_id, db)
    try:
        await DLNAController(device.av_transport_url).stop()
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/{device_id}/status", response_model=TransportInfoOut)
async def get_status(device_id: int, db: DBDep, _: CurrentUser):
    device = await _require_renderer(device_id, db)
    try:
        return await DLNAController(device.av_transport_url).get_transport_info()
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _require_renderer(device_id: int, db) -> DLNADevice:
    result = await db.execute(select(DLNADevice).where(DLNADevice.id == device_id))
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="DLNA 设备不存在")
    if not device.av_transport_url:
        raise HTTPException(status_code=422, detail="该设备不支持 AVTransport，无法投屏")
    return device
