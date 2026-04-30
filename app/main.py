import asyncio
import sys
import socket as _socket
from contextlib import asynccontextmanager
from datetime import datetime
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from loguru import logger
from sqlalchemy import select
from app.config import get_settings
from app.database import init_db, AsyncSessionLocal
from app.models.camera import Camera
from app.models.recording import Recording
from app.models.dlna_device import DLNADevice
from app.services.scheduler_service import scheduler_service
from app.services.recorder import Recorder, RecordingTask
from app.services.nas_syncer import NasSyncer
from app.services.ws_manager import ws_manager
from app.services.presence_service import presence_service
from app.services.dlna_service import DLNAController
from app.routers import system, devices, cameras, recordings, schedules, ws
from app.routers import members, dlna
from app.routers import analytics
from app.services.camera_health import CameraHealthChecker

settings = get_settings()

# ── Loguru 配置 ──────────────────────────────────────────────────────
logger.remove()
logger.add(sys.stderr, level=settings.log_level, colorize=True,
           format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}")
logger.add("data/app.log", level=settings.log_level, rotation="10 MB", retention="7 days",
           encoding="utf-8")

# ── 全局服务实例 ────────────────────────────────────────────────────
recorder = Recorder(settings.recording_temp_dir)
nas_syncer = NasSyncer(
    mode=settings.nas_mode,
    local_storage_path=settings.local_storage_path,
    mount_path=settings.nas_mount_path,
    smb_host=settings.nas_smb_host,
    smb_share=settings.nas_smb_share,
    smb_user=settings.nas_smb_user,
    smb_password=settings.nas_smb_password,
)


async def _on_recording_complete(task: RecordingTask):
    loop = asyncio.get_running_loop()
    try:
        dest = await loop.run_in_executor(None, lambda: nas_syncer.sync_file(task.output_path, task.camera_mac))
        file_size = dest.stat().st_size if dest.exists() else None
        dest_str = str(dest)
    except Exception as e:
        logger.error(f"NAS同步失败 [{task.camera_mac}]: {e}")
        dest_str = str(task.output_path)
        file_size = task.output_path.stat().st_size if task.output_path.exists() else None

    ended_at = datetime.now()
    duration = int((ended_at - task.started_at).total_seconds())

    async with AsyncSessionLocal() as db:
        if task.recording_id:
            result = await db.execute(select(Recording).where(Recording.id == task.recording_id))
            rec = result.scalar_one_or_none()
            if rec:
                rec.status = "completed"
                rec.file_path = dest_str
                rec.file_size = file_size
                rec.ended_at = ended_at
                rec.duration = duration

        cam = (await db.execute(select(Camera).where(Camera.device_mac == task.camera_mac))).scalar_one_or_none()
        if cam:
            cam.is_recording = False

        await db.commit()

        # A4: auto DLNA cast
        if cam and cam.auto_cast_dlna:
            dlna_dev = (await db.execute(
                select(DLNADevice).where(DLNADevice.udn == cam.auto_cast_dlna)
            )).scalar_one_or_none()
            if dlna_dev and dlna_dev.av_transport_url:
                await _cast_recording(dlna_dev.av_transport_url, dest_str, task.camera_mac)
    logger.info(f"录制完成 [{task.camera_mac}] id={task.recording_id} 时长={duration}s")
    await ws_manager.broadcast("recording_completed", {"camera_mac": task.camera_mac, "recording_id": task.recording_id})


async def _cast_recording(av_transport_url: str, file_path: str, camera_mac: str):
    """A4: copy recording to dlna-media directory and cast to target DLNA device."""
    import shutil
    import time
    from pathlib import Path as _P

    src = _P(file_path)
    if not src.exists():
        logger.warning(f"[A4] 投屏跳过，文件不存在: {file_path}")
        return

    media_dir = _P("data/dlna_media")
    media_dir.mkdir(parents=True, exist_ok=True)
    fname = f"auto_{int(time.time())}_{src.name}"
    dest = media_dir / fname

    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, lambda: shutil.copy2(src, dest))
    except Exception as e:
        logger.error(f"[A4] 复制录制文件到 dlna_media 失败: {e}")
        return

    try:
        with _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
    except Exception:
        local_ip = "127.0.0.1"

    port = get_settings().server_port
    media_url = f"http://{local_ip}:{port}/dlna-media/{fname}"

    try:
        ctrl = DLNAController(av_transport_url)
        await ctrl.set_uri(media_url)
        await ctrl.play()
        logger.info(f"[A4] 自动投屏成功: {camera_mac} → {media_url}")
    except Exception as e:
        logger.error(f"[A4] 自动投屏失败: {e}")
        return

    async def _cleanup():
        await asyncio.sleep(3600)
        dest.unlink(missing_ok=True)
        logger.info(f"[A4] DLNA 临时文件已清理: {fname}")

    asyncio.create_task(_cleanup())


async def _on_recording_failed(task: RecordingTask, retcode: int, stderr: str):
    async with AsyncSessionLocal() as db:
        if task.recording_id:
            result = await db.execute(select(Recording).where(Recording.id == task.recording_id))
            rec = result.scalar_one_or_none()
            if rec:
                rec.status = "failed"
                rec.error_msg = (stderr or f"退出码 {retcode}")[:500]
                rec.ended_at = datetime.now()

        cam = (await db.execute(select(Camera).where(Camera.device_mac == task.camera_mac))).scalar_one_or_none()
        if cam:
            cam.is_recording = False

        await db.commit()
    logger.error(f"录制失败 [{task.camera_mac}] id={task.recording_id} code={retcode}")
    await ws_manager.broadcast("recording_failed", {"camera_mac": task.camera_mac, "recording_id": task.recording_id})


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("智能家居后端启动中...")
    await init_db()
    logger.info("数据库初始化完成")

    # 清理上次服务中断遗留的孤立录制记录
    async with AsyncSessionLocal() as db:
        stuck_recs = (await db.execute(
            select(Recording).where(Recording.status == "recording")
        )).scalars().all()
        stuck_cams = (await db.execute(
            select(Camera).where(Camera.is_recording == True)
        )).scalars().all()
        for rec in stuck_recs:
            rec.status = "failed"
            rec.error_msg = "服务重启，录制中断"
            rec.ended_at = datetime.now()
        for cam in stuck_cams:
            cam.is_recording = False
        if stuck_recs or stuck_cams:
            await db.commit()
            logger.warning(f"启动清理: 重置 {len(stuck_recs)} 条孤立录制记录, {len(stuck_cams)} 台摄像头状态")

    scheduler_service.start()

    # Restore enabled schedules from database
    from app.models.schedule import Schedule as ScheduleModel
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(ScheduleModel).where(ScheduleModel.enabled == True)
        )
        enabled_schedules = [
            {"id": s.id, "cron_expr": s.cron_expr, "camera_mac": s.camera_mac}
            for s in result.scalars().all()
        ]

    async def _trigger_scheduled_recording(camera_mac: str):
        from sqlalchemy import select as _select
        from app.models.camera import Camera as CameraModel
        rtsp_url = None
        async with AsyncSessionLocal() as _db:
            cam_result = await _db.execute(
                _select(CameraModel).where(CameraModel.device_mac == camera_mac)
            )
            cam = cam_result.scalar_one_or_none()
            if not cam or not cam.rtsp_url:
                logger.warning(f"调度录制: 摄像头 {camera_mac} 不存在或无 RTSP URL")
                return
            if cam.is_recording:
                logger.info(f"调度录制: {camera_mac} 已在录制中，跳过")
                return
            rtsp_url = cam.rtsp_url  # capture value inside session
        if rtsp_url:
            await recorder.start_recording(camera_mac=camera_mac, rtsp_url=rtsp_url)

    for sched in enabled_schedules:
        try:
            scheduler_service.add_recording_job(
                job_id=f"schedule_{sched['id']}",
                cron_expr=sched['cron_expr'],
                camera_mac=sched['camera_mac'],
                callback=_trigger_scheduled_recording,
            )
        except Exception as e:
            logger.warning(f"恢复调度任务 schedule_{sched['id']} 失败: {e}")

    logger.info(f"已从数据库恢复 {len(enabled_schedules)} 个调度任务")

    camera_health_checker = CameraHealthChecker(settings.camera_health_interval_seconds)

    async def _auto_start_recording(camera_mac: str):
        from app.models.camera import Camera as CameraModel
        from app.models.recording import Recording as RecordingModel
        async with AsyncSessionLocal() as db:
            cam = (await db.execute(
                select(CameraModel).where(CameraModel.device_mac == camera_mac)
            )).scalar_one_or_none()
            if not cam or not cam.rtsp_url or cam.is_recording:
                return
            rtsp_url = cam.rtsp_url  # capture before session closes
            rec = RecordingModel(
                camera_mac=camera_mac,
                file_path="(pending)",
                started_at=datetime.now(),
                status="recording",
            )
            db.add(rec)
            cam.is_recording = True
            await db.commit()
            await db.refresh(rec)
            rec_id = rec.id

        try:
            await recorder.start_recording(camera_mac, rtsp_url, settings.recording_segment_seconds)
        except Exception as e:
            logger.error(f"[A1] 自动录制启动失败 {camera_mac}: {e}")
            async with AsyncSessionLocal() as db:
                rec_db = (await db.execute(
                    select(RecordingModel).where(RecordingModel.id == rec_id)
                )).scalar_one_or_none()
                if rec_db:
                    rec_db.status = "failed"
                    rec_db.error_msg = str(e)
                cam_db = (await db.execute(
                    select(CameraModel).where(CameraModel.device_mac == camera_mac)
                )).scalar_one_or_none()
                if cam_db:
                    cam_db.is_recording = False
                await db.commit()
            return

        if camera_mac in recorder.active:
            recorder.active[camera_mac].recording_id = rec_id
        else:
            logger.warning(f"[A1] 录制任务已结束，无法设置 recording_id {rec_id}: {camera_mac}")

    async def _auto_stop_recording(camera_mac: str):
        from app.models.camera import Camera as CameraModel
        from app.models.recording import Recording as RecordingModel
        output_path = await recorder.stop_recording(camera_mac)
        ended_at = datetime.now()

        async with AsyncSessionLocal() as db:
            cam = (await db.execute(
                select(CameraModel).where(CameraModel.device_mac == camera_mac)
            )).scalar_one_or_none()
            if cam:
                cam.is_recording = False

            rec = (await db.execute(
                select(RecordingModel)
                .where(RecordingModel.camera_mac == camera_mac, RecordingModel.status == "recording")
                .order_by(RecordingModel.started_at.desc())
                .limit(1)
            )).scalar_one_or_none()

            if rec:
                if output_path and output_path.exists():
                    loop = asyncio.get_running_loop()
                    try:
                        dest = await loop.run_in_executor(
                            None, lambda: nas_syncer.sync_file(output_path, camera_mac)
                        )
                        rec.file_path = str(dest)
                        rec.file_size = dest.stat().st_size if dest.exists() else None
                    except Exception as e:
                        logger.error(f"[A1] 停止录制 NAS 同步失败 {camera_mac}: {e}")
                        rec.file_path = str(output_path)
                        rec.file_size = output_path.stat().st_size if output_path.exists() else None
                    rec.status = "completed"
                else:
                    rec.status = "failed"
                    rec.error_msg = "presence-triggered stop: no valid output"
                rec.ended_at = ended_at

            await db.commit()

        await ws_manager.broadcast("recording_completed", {"camera_mac": camera_mac})
        logger.info(f"[A1] 自动停止录制完成: {camera_mac}")

    recorder.set_callbacks(on_complete=_on_recording_complete, on_failed=_on_recording_failed)
    await recorder.start_monitor()
    presence_service._poll_interval = settings.presence_poll_interval_seconds
    await presence_service.start(
        auto_start_cb=_auto_start_recording,
        auto_stop_cb=_auto_stop_recording,
    )
    await camera_health_checker.start()
    app.state.camera_health_checker = camera_health_checker
    app.state.recorder = recorder
    app.state.nas_syncer = nas_syncer
    app.state.presence_service = presence_service
    yield
    await camera_health_checker.stop()
    await recorder.stop_monitor()
    await presence_service.stop()
    scheduler_service.shutdown()
    logger.info("智能家居后端已停止")


app = FastAPI(
    title="智能家居管理 API",
    version=settings.app_version,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.get_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── 路由注册 ────────────────────────────────────────────────────────
API_PREFIX = "/api/v1"
app.include_router(system.router, prefix=API_PREFIX)
app.include_router(devices.router, prefix=API_PREFIX)
app.include_router(cameras.router, prefix=API_PREFIX)
app.include_router(recordings.router, prefix=API_PREFIX)
app.include_router(schedules.router, prefix=API_PREFIX)
app.include_router(members.router, prefix=API_PREFIX)
app.include_router(dlna.router, prefix=API_PREFIX)
app.include_router(analytics.router, prefix=API_PREFIX)
app.include_router(ws.router)

# Serve uploaded DLNA media files so TVs can pull them over LAN
from pathlib import Path as _Path
_Path("data/dlna_media").mkdir(parents=True, exist_ok=True)
app.mount("/dlna-media", StaticFiles(directory="data/dlna_media"), name="dlna-media")


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"未处理异常: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"error": {"code": "INTERNAL_ERROR", "message": "服务器内部错误", "detail": str(exc)}},
    )


def _dev():
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
