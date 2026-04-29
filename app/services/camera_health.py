import asyncio
from datetime import datetime
from loguru import logger
from sqlalchemy import select
from app.database import AsyncSessionLocal
from app.models.camera import Camera
from app.services.ws_manager import ws_manager


class CameraHealthChecker:
    def __init__(self, interval: int = 60):
        self._interval = interval
        self._task: asyncio.Task | None = None

    async def start(self):
        self._task = asyncio.create_task(self._loop())
        logger.info(f"CameraHealthChecker 已启动，间隔 {self._interval}s")

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("CameraHealthChecker 已停止")

    async def _loop(self):
        while True:
            try:
                async with AsyncSessionLocal() as db:
                    await self._check_all(db)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"CameraHealthChecker 轮询异常: {e}")
            await asyncio.sleep(self._interval)

    async def _check_all(self, db):
        result = await db.execute(select(Camera).where(Camera.rtsp_url.isnot(None)))
        cameras = result.scalars().all()
        await asyncio.gather(
            *[self._check_camera(db, cam) for cam in cameras],
            return_exceptions=True,
        )

    async def _check_camera(self, db, camera: Camera):
        was_online = camera.is_online
        is_now_online = await self._probe_rtsp(camera.rtsp_url)
        camera.last_probe_at = datetime.now()
        camera.is_online = is_now_online
        await db.commit()

        if was_online and not is_now_online:
            await ws_manager.broadcast("camera_offline", {"mac": camera.device_mac})
            logger.warning(f"[CameraHealth] 摄像头掉线: {camera.device_mac}")
        elif not was_online and is_now_online:
            await ws_manager.broadcast("camera_online", {"mac": camera.device_mac})
            logger.info(f"[CameraHealth] 摄像头恢复: {camera.device_mac}")

    async def _probe_rtsp(self, rtsp_url: str) -> bool:
        try:
            proc = await asyncio.create_subprocess_exec(
                "ffprobe", "-v", "quiet",
                "-show_entries", "format=duration",
                "-i", rtsp_url,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=5)
            return proc.returncode == 0
        except (asyncio.TimeoutError, Exception):
            return False
