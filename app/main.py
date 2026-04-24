import sys
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from app.config import get_settings
from app.database import init_db
from app.services.scheduler_service import scheduler_service
from app.services.recorder import Recorder
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("智能家居后端启动中...")
    await init_db()
    logger.info("数据库初始化完成")
    scheduler_service.start()
    await recorder.start_monitor()
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
