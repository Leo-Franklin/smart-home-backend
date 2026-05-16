import os, sys, threading
from contextlib import asynccontextmanager
from datetime import datetime
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from loguru import logger
from pathlib import Path as _Path
from sqlalchemy import select
from urllib.parse import urlparse, urlunparse
from app.config import get_settings, is_packaged
from app.database import init_db, AsyncSessionLocal
from app.domain.models.camera import Camera
from app.domain.models.recording import Recording
from app.domain.models.schedule import Schedule as ScheduleModel
from app.domain.services.scheduler_service import scheduler_service
from app.domain.services.recorder import Recorder
from app.domain.services.nas_syncer import NasSyncer
from app.domain.services.presence_service import presence_service
from app.domain.services.camera_health import CameraHealthChecker
from app.domain.services.dlna_service import DLNAController
from app.domain.services.recording_domain import RecordingDomainService
from app.domain.services.presence_domain import PresenceDomainService
from app.api import devices, cameras, recordings, schedules, members, dlna, analytics, system, user, ws

settings = get_settings()
if is_packaged():
    _exe_dir = _Path(sys.executable).parent
    os.environ["PATH"] = os.pathsep.join([str(_exe_dir / "nmap"), str(_exe_dir / "ffmpeg")]) + os.pathsep + os.environ.get("PATH", "")

logger.remove()
logger.add(sys.stderr, level=settings.log_level, colorize=True, format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}")
logger.add("data/app.log", level=settings.log_level, rotation="10 MB", retention="7 days", encoding="utf-8")

recorder = Recorder(settings.recording_temp_dir)
nas_syncer = NasSyncer(mode=settings.nas_mode, local_storage_path=settings.local_storage_path, mount_path=settings.nas_mount_path, smb_host=settings.nas_smb_host, smb_share=settings.nas_smb_share, smb_user=settings.nas_smb_user, smb_password=settings.nas_smb_password)
recording_domain = RecordingDomainService(nas_syncer=nas_syncer)
presence_domain = PresenceDomainService(recorder=recorder, nas_syncer=nas_syncer)
recorder.set_callbacks(
    on_complete=lambda t: recording_domain.on_recording_complete(t),
    on_failed=lambda t, rc, err: recording_domain.on_recording_failed(t, rc, err),
    should_continue=lambda mac: recording_domain.should_continue_recording(mac),
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("智能家居后端启动中...")
    await init_db()
    async with AsyncSessionLocal() as db:
        stuck_recs = (await db.execute(select(Recording).where(Recording.status == "recording"))).scalars().all()
        stuck_cams = (await db.execute(select(Camera).where(Camera.is_recording == True))).scalars().all()
        for rec in stuck_recs: rec.status = "failed"; rec.error_msg = "服务重启，录制中断"; rec.ended_at = datetime.now()
        for cam in stuck_cams: cam.is_recording = False
        if stuck_recs or stuck_cams: await db.commit(); logger.warning(f"启动清理: 重置 {len(stuck_recs)} 条孤立录制记录, {len(stuck_cams)} 台摄像头状态")
    _shutdown_event = None
    if is_packaged():
        from app.desktop import run_tray_icon, open_browser
        _shutdown_event = threading.Event()
        threading.Thread(target=run_tray_icon, args=(_shutdown_event,), daemon=True).start()
        open_browser()
    scheduler_service.start()
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(ScheduleModel).where(ScheduleModel.enabled == True))
        enabled_schedules = [{"id": s.id, "cron_expr": s.cron_expr, "camera_mac": s.camera_mac, "segment_duration": s.segment_duration} for s in result.scalars().all()]
    for sched in enabled_schedules:
        async def _trigger(mac, sd=sched['segment_duration']):
            rec_id = None
            async with AsyncSessionLocal() as _db:
                cam = (await _db.execute(select(Camera).where(Camera.device_mac == mac))).scalar_one_or_none()
                if not cam or not cam.rtsp_url: logger.warning(f"调度录制: 摄像头 {mac} 不存在或无 RTSP URL"); return
                if cam.is_recording: logger.info(f"调度录制: {mac} 已在录制中，跳过"); return
                rtsp_url = cam.rtsp_url
                if cam.onvif_user or cam.onvif_password:
                    parsed = urlparse(rtsp_url); netloc = f"{cam.onvif_user or ''}:{cam.onvif_password or ''}@{parsed.hostname or ''}"
                    if parsed.port: netloc += f":{parsed.port}"
                    rtsp_url = urlunparse(parsed._replace(netloc=netloc))
                rec = Recording(camera_mac=mac, file_path="(pending)", started_at=datetime.now(), status="recording")
                _db.add(rec); cam.is_recording = True
                await _db.commit(); await _db.refresh(rec); rec_id = rec.id
            try: await recorder.start_recording(mac, rtsp_url, sd)
            except Exception as e:
                logger.error(f"调度录制启动失败 {mac}: {e}")
                async with AsyncSessionLocal() as _db:
                    rec_db = (await _db.execute(select(Recording).where(Recording.id == rec_id))).scalar_one_or_none()
                    if rec_db: rec_db.status = "failed"; rec_db.error_msg = str(e)
                    cam_db = (await _db.execute(select(Camera).where(Camera.device_mac == mac))).scalar_one_or_none()
                    if cam_db: cam_db.is_recording = False
                    await _db.commit()
                return
            if mac in recorder.active: recorder.active[mac].recording_id = rec_id
        try: scheduler_service.add_recording_job(job_id=f"schedule_{sched['id']}", cron_expr=sched['cron_expr'], camera_mac=sched['camera_mac'], callback=_trigger)
        except Exception as e: logger.warning(f"恢复调度任务 schedule_{sched['id']} 失败: {e}")
    logger.info(f"已从数据库恢复 {len(enabled_schedules)} 个调度任务")
    camera_health_checker = CameraHealthChecker(settings.camera_health_interval_seconds)
    await recorder.start_monitor()
    presence_service._poll_interval = settings.presence_poll_interval_seconds
    await presence_service.start(auto_start_cb=presence_domain.auto_start_recording, auto_stop_cb=presence_domain.auto_stop_recording)
    await camera_health_checker.start()
    app.state.camera_health_checker = camera_health_checker
    app.state.recorder = recorder
    app.state.nas_syncer = nas_syncer
    app.state.presence_service = presence_service
    yield
    if _shutdown_event is not None: _shutdown_event.set()
    await camera_health_checker.stop(); await recorder.stop_monitor(); await presence_service.stop(); scheduler_service.shutdown(); logger.info("智能家居后端已停止")


app = FastAPI(title="智能家居管理 API", version=settings.app_version, docs_url="/api/docs", redoc_url="/api/redoc", openapi_url="/api/openapi.json", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=settings.get_cors_origins(), allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
P = "/api/v1"
app.include_router(system.router, prefix=P); app.include_router(devices.router, prefix=P); app.include_router(cameras.router, prefix=P); app.include_router(recordings.router, prefix=P); app.include_router(schedules.router, prefix=P); app.include_router(members.router, prefix=P); app.include_router(user.router, prefix=P); app.include_router(dlna.router, prefix=P); app.include_router(analytics.router, prefix=P); app.include_router(ws.router)
_Path("data/dlna_media").mkdir(parents=True, exist_ok=True)
app.mount("/dlna-media", StaticFiles(directory="data/dlna_media"), name="dlna-media")
_HLS_BASE = _Path("data/hls"); _HLS_BASE.mkdir(parents=True, exist_ok=True)

@app.get("/hls/{path:path}", include_in_schema=False)
async def serve_hls_file(path: str):
    parts = path.split("/", 1)
    if len(parts) != 2: raise HTTPException(status_code=404, detail="HLS file not found")
    mac, filename = parts
    file_path = _HLS_BASE / mac.replace(":", "-") / filename
    if not file_path.exists(): raise HTTPException(status_code=404, detail="HLS file not found")
    return FileResponse(str(file_path), media_type="application/vnd.apple.mpegurl" if filename.endswith(".m3u8") else "video/MP2T")

if is_packaged():
    _frontend_dir = _Path(getattr(sys, "_MEIPASS", ".")) / "frontend"
    if _frontend_dir.exists(): app.mount("/", StaticFiles(directory=str(_frontend_dir), html=True), name="frontend")

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException): return JSONResponse(status_code=exc.status_code, content={"error": {"code": str(exc.status_code), "message": exc.detail}})
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception): logger.error(f"未处理异常 [{type(exc).__qualname__}]: {exc!r}", exc_info=True); return JSONResponse(status_code=500, content={"error": {"code": "INTERNAL_ERROR", "message": "服务器内部错误", "detail": str(exc)}})

def _dev():
    import uvicorn; uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000)
