import asyncio
import sys
from contextlib import asynccontextmanager
from datetime import datetime
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from sqlalchemy import select
from app.config import get_settings
from app.database import init_db, AsyncSessionLocal
from app.models.camera import Camera
from app.models.recording import Recording
from app.services.scheduler_service import scheduler_service
from app.services.recorder import Recorder, RecordingTask
from app.services.nas_syncer import NasSyncer
from app.services.ws_manager import ws_manager
from app.routers import system, devices, cameras, recordings, schedules, ws

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
    logger.info(f"录制完成 [{task.camera_mac}] id={task.recording_id} 时长={duration}s")
    await ws_manager.broadcast("recording_completed", {"camera_mac": task.camera_mac, "recording_id": task.recording_id})


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
    recorder.set_callbacks(on_complete=_on_recording_complete, on_failed=_on_recording_failed)
    await recorder.start_monitor()
    app.state.recorder = recorder
    app.state.nas_syncer = nas_syncer
    yield
    await recorder.stop_monitor()
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
    allow_origins=["*"] if settings.debug else ["http://localhost:5173"],
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
app.include_router(ws.router)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"未处理异常: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"error": {"code": "INTERNAL_ERROR", "message": "服务器内部错误", "detail": str(exc)}},
    )
