import asyncio
import shutil
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse, urlunparse
from fastapi import APIRouter, HTTPException, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from app.deps import DBDep, CurrentUser, StreamUser, RecorderDep, NasSyncerDep
from app.models.camera import Camera
from app.models.recording import Recording
from app.schemas.camera import CameraCreate, CameraUpdate, CameraOut
from app.services.onvif_client import OnvifClient
from app.config import get_settings
from app.services.ws_manager import ws_manager
from loguru import logger

router = APIRouter(prefix="/cameras", tags=["cameras"])

_live_procs: dict[str, asyncio.subprocess.Process] = {}
_HLS_BASE = Path("data/hls")


@router.get("", response_model=list[CameraOut])
async def list_cameras(db: DBDep, _: CurrentUser):
    result = await db.execute(select(Camera))
    return result.scalars().all()


@router.post("", response_model=CameraOut, status_code=status.HTTP_201_CREATED)
async def create_camera(body: CameraCreate, db: DBDep, _: CurrentUser):
    camera = Camera(**body.model_dump())
    db.add(camera)
    await db.commit()
    await db.refresh(camera)
    return camera


@router.get("/{mac}", response_model=CameraOut)
async def get_camera(mac: str, db: DBDep, _: CurrentUser):
    result = await db.execute(select(Camera).where(Camera.device_mac == mac))
    camera = result.scalar_one_or_none()
    if not camera:
        raise HTTPException(status_code=404, detail="摄像头未配置")
    return camera


@router.put("/{mac}", response_model=CameraOut)
async def update_camera(mac: str, body: CameraUpdate, db: DBDep, _: CurrentUser):
    result = await db.execute(select(Camera).where(Camera.device_mac == mac))
    camera = result.scalar_one_or_none()
    if not camera:
        raise HTTPException(status_code=404, detail="摄像头未配置")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(camera, field, value)
    await db.commit()
    await db.refresh(camera)
    return camera


@router.delete("/{mac}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_camera(mac: str, db: DBDep, _: CurrentUser):
    result = await db.execute(select(Camera).where(Camera.device_mac == mac))
    camera = result.scalar_one_or_none()
    if not camera:
        raise HTTPException(status_code=404, detail="摄像头未配置")
    await db.delete(camera)
    await db.commit()


@router.post("/{mac}/probe")
async def probe_camera(mac: str, db: DBDep, _: CurrentUser):
    result = await db.execute(select(Camera).where(Camera.device_mac == mac))
    camera = result.scalar_one_or_none()
    if not camera:
        raise HTTPException(status_code=404, detail="摄像头未配置")
    if not camera.onvif_host:
        raise HTTPException(status_code=422, detail="摄像头 onvif_host 未设置，请先通过 PUT 接口更新 IP 地址")
    client = OnvifClient(camera.onvif_host, camera.onvif_port,
                         camera.onvif_user or "", camera.onvif_password or "")
    try:
        info = await client.get_device_info()
        profiles = await client.get_profiles()

        # 为每个 profile 获取 RTSP URI
        for p in profiles:
            try:
                p["rtsp_url"] = await client.get_stream_uri(p["index"])
            except Exception:
                p["rtsp_url"] = None

        # 自动将第一个有效 RTSP 地址写入摄像头配置
        auto_url = next((p["rtsp_url"] for p in profiles if p["rtsp_url"]), None)
        if auto_url and not camera.rtsp_url:
            camera.rtsp_url = auto_url
            await db.commit()

        return {"device_info": info, "profiles": profiles, "auto_set_rtsp_url": auto_url}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"ONVIF 通信异常: {e}")


@router.post("/{mac}/record/start", status_code=status.HTTP_202_ACCEPTED)
async def start_recording(mac: str, db: DBDep, _: CurrentUser, recorder: RecorderDep):
    result = await db.execute(select(Camera).where(Camera.device_mac == mac))
    camera = result.scalar_one_or_none()
    if not camera:
        raise HTTPException(status_code=404, detail="摄像头未配置")
    if camera.is_recording:
        raise HTTPException(status_code=409, detail="该摄像头已在录制中")
    if not camera.rtsp_url:
        raise HTTPException(status_code=422, detail="摄像头 rtsp_url 未设置，请先通过 PUT 接口配置 RTSP 地址")

    settings = get_settings()

    rtsp_url = camera.rtsp_url
    if camera.onvif_user or camera.onvif_password:
        parsed = urlparse(rtsp_url)
        user = camera.onvif_user or ""
        pwd = camera.onvif_password or ""
        host = parsed.hostname or ""
        netloc = f"{user}:{pwd}@{host}"
        if parsed.port:
            netloc += f":{parsed.port}"
        rtsp_url = urlunparse(parsed._replace(netloc=netloc))

    rec = Recording(
        camera_mac=mac,
        file_path="(pending)",
        started_at=datetime.now(),
        status="recording",
    )
    db.add(rec)
    camera.is_recording = True
    await db.commit()
    await db.refresh(rec)

    try:
        await recorder.start_recording(mac, rtsp_url, settings.recording_segment_seconds)
    except Exception as e:
        camera.is_recording = False
        rec.status = "failed"
        rec.error_msg = str(e)
        await db.commit()
        raise HTTPException(status_code=500, detail=f"启动录制失败: {e}")

    if mac in recorder.active:
        recorder.active[mac].recording_id = rec.id

    return {"message": "录制已启动", "recording_id": rec.id}


@router.post("/{mac}/record/stop", status_code=status.HTTP_202_ACCEPTED)
async def stop_recording(mac: str, db: DBDep, _: CurrentUser, recorder: RecorderDep, nas_syncer: NasSyncerDep):
    result = await db.execute(select(Camera).where(Camera.device_mac == mac))
    camera = result.scalar_one_or_none()
    if not camera:
        raise HTTPException(status_code=404, detail="摄像头未配置")
    if not camera.is_recording:
        raise HTTPException(status_code=409, detail="该摄像头未在录制")

    task = recorder.active.get(mac)
    recording_id = task.recording_id if task else None
    started_at = task.started_at if task else None

    # 服务重启后内存中没有 task，兜底从数据库查最近一条卡住的记录
    if recording_id is None:
        orphan_result = await db.execute(
            select(Recording)
            .where(Recording.camera_mac == mac, Recording.status == "recording")
            .order_by(Recording.started_at.desc())
            .limit(1)
        )
        orphan = orphan_result.scalar_one_or_none()
        if orphan:
            recording_id = orphan.id
            started_at = orphan.started_at

    try:
        output_path = await recorder.stop_recording(mac)
    except Exception as e:
        logger.error(f"停止录制异常: {e}")
        output_path = None

    camera.is_recording = False
    ended_at = datetime.now()

    if recording_id:
        rec_result = await db.execute(select(Recording).where(Recording.id == recording_id))
        rec = rec_result.scalar_one_or_none()
        if rec:
            if output_path and output_path.exists():
                try:
                    loop = asyncio.get_running_loop()
                    dest = await loop.run_in_executor(None, lambda: nas_syncer.sync_file(output_path, mac))
                    rec.file_path = str(dest)
                    rec.file_size = dest.stat().st_size if dest.exists() else None
                except Exception as e:
                    logger.error(f"手动停止后NAS同步失败: {e}")
                    rec.file_path = str(output_path)
                    rec.file_size = output_path.stat().st_size if output_path.exists() else None
                rec.status = "completed"
            else:
                rec.status = "failed"
                rec.error_msg = "录制文件不存在或过小，请检查摄像头RTSP连接是否正常"
            rec.ended_at = ended_at
            if started_at:
                rec.duration = int((ended_at - started_at).total_seconds())

    await db.commit()
    await ws_manager.broadcast("recording_completed", {"camera_mac": mac, "recording_id": recording_id})
    return {"message": "录制已停止"}


# ── MJPEG live stream ─────────────────────────────────────────

def _rtsp_with_creds(camera: Camera) -> str:
    """Embed ONVIF credentials into the RTSP URL if present."""
    url = camera.rtsp_url
    if camera.onvif_user or camera.onvif_password:
        parsed = urlparse(url)
        netloc = f"{camera.onvif_user or ''}:{camera.onvif_password or ''}@{parsed.hostname or ''}"
        if parsed.port:
            netloc += f":{parsed.port}"
        url = urlunparse(parsed._replace(netloc=netloc))
    return url


async def _mjpeg_generate(rtsp_url: str):
    """Async generator: reads RTSP via FFmpeg and yields multipart/x-mixed-replace frames."""
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y",
        "-rtsp_transport", "tcp",
        "-i", rtsp_url,
        "-f", "mjpeg",
        "-q:v", "5",
        "-vf", "fps=10",
        "pipe:1",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        buf = b""
        SOI, EOI = b"\xff\xd8", b"\xff\xd9"
        while True:
            chunk = await proc.stdout.read(32768)
            if not chunk:
                break
            buf += chunk
            while True:
                start = buf.find(SOI)
                if start < 0:
                    buf = b""
                    break
                end = buf.find(EOI, start + 2)
                if end < 0:
                    buf = buf[start:]
                    break
                frame = buf[start: end + 2]
                buf = buf[end + 2:]
                yield (
                    b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"
                    + frame
                    + b"\r\n"
                )
    finally:
        if proc.returncode is None:
            proc.kill()
        await proc.wait()


@router.get("/{mac}/stream/mjpeg")
async def stream_mjpeg(mac: str, db: DBDep, _: StreamUser):
    result = await db.execute(select(Camera).where(Camera.device_mac == mac))
    camera = result.scalar_one_or_none()
    if not camera:
        raise HTTPException(status_code=404, detail="摄像头未配置")
    if not camera.rtsp_url:
        raise HTTPException(status_code=422, detail="摄像头 rtsp_url 未设置，请先通过 ONVIF 探测配置 RTSP 地址")
    return StreamingResponse(
        _mjpeg_generate(_rtsp_with_creds(camera)),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


# ── Snapshot ──────────────────────────────────────────────────

@router.get("/{mac}/snapshot")
async def snapshot_camera(mac: str, db: DBDep, _: CurrentUser):
    result = await db.execute(select(Camera).where(Camera.device_mac == mac))
    camera = result.scalar_one_or_none()
    if not camera:
        raise HTTPException(status_code=404, detail="摄像头未配置")
    if not camera.rtsp_url:
        raise HTTPException(status_code=422, detail="摄像头 rtsp_url 未设置，请先通过 ONVIF 探测配置 RTSP 地址")
    rtsp_url = _rtsp_with_creds(camera)
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y",
            "-rtsp_transport", "tcp",
            "-i", rtsp_url,
            "-vframes", "1",
            "-f", "image2",
            "pipe:1",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
    except asyncio.TimeoutError:
        proc.kill()
        raise HTTPException(status_code=408, detail="截图超时，摄像头可能无信号")
    if not stdout:
        raise HTTPException(status_code=500, detail="截图失败，摄像头可能无信号或连接异常")
    return Response(content=stdout, media_type="image/jpeg")


# ── HLS live stream ───────────────────────────────────────────

@router.post("/{mac}/live/start", status_code=status.HTTP_202_ACCEPTED)
async def start_live(mac: str, db: DBDep, _: CurrentUser):
    result = await db.execute(select(Camera).where(Camera.device_mac == mac))
    camera = result.scalar_one_or_none()
    if not camera:
        raise HTTPException(status_code=404, detail="摄像头未配置")
    if not camera.rtsp_url:
        raise HTTPException(status_code=422, detail="摄像头 rtsp_url 未设置，请先通过 ONVIF 探测配置 RTSP 地址")
    if mac in _live_procs and _live_procs[mac].returncode is None:
        return {"message": "直播已在运行"}
    rtsp_url = _rtsp_with_creds(camera)
    output_dir = _HLS_BASE / mac
    output_dir.mkdir(parents=True, exist_ok=True)
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y",
        "-rtsp_transport", "tcp",
        "-i", rtsp_url,
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-tune", "zerolatency",
        "-c:a", "aac",
        "-f", "hls",
        "-hls_time", "2",
        "-hls_list_size", "5",
        "-hls_flags", "delete_segments",
        str(output_dir / "index.m3u8"),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    _live_procs[mac] = proc
    await asyncio.sleep(2)
    if proc.returncode is not None:
        _live_procs.pop(mac, None)
        raise HTTPException(status_code=500, detail="HLS 直播启动失败，请检查摄像头 RTSP 连接")
    return {"message": "HLS 直播已启动"}


@router.delete("/{mac}/live/stop", status_code=status.HTTP_202_ACCEPTED)
async def stop_live(mac: str, _: CurrentUser):
    proc = _live_procs.pop(mac, None)
    if proc and proc.returncode is None:
        proc.kill()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            pass
    output_dir = _HLS_BASE / mac
    if output_dir.exists():
        shutil.rmtree(output_dir, ignore_errors=True)
    return {"message": "HLS 直播已停止"}
