import asyncio
import subprocess
from datetime import datetime
from urllib.parse import urlparse, urlunparse
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
                await self._check_all()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"CameraHealthChecker 轮询异常: {e}")
            await asyncio.sleep(self._interval)

    async def _check_all(self):
        # Short-lived read session — released before spawning concurrent probe tasks
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Camera).where(Camera.rtsp_url.isnot(None)))
            cameras = result.scalars().all()
            snapshots = [
                (cam.device_mac, cam.rtsp_url, cam.onvif_user, cam.onvif_password, cam.is_online)
                for cam in cameras
            ]
        await asyncio.gather(
            *[
                self._check_camera(device_mac, rtsp_url, onvif_user, onvif_password, was_online)
                for device_mac, rtsp_url, onvif_user, onvif_password, was_online in snapshots
            ],
            return_exceptions=True,
        )

    async def _check_camera(self, device_mac: str, rtsp_url: str, onvif_user: str | None, onvif_password: str | None, was_online: bool):
        probe_url = self._build_rtsp_url(rtsp_url, onvif_user, onvif_password)
        is_now_online = await self._probe_rtsp(probe_url)
        async with AsyncSessionLocal() as db:
            cam = (await db.execute(
                select(Camera).where(Camera.device_mac == device_mac)
            )).scalar_one_or_none()
            if cam is None:
                return
            cam.last_probe_at = datetime.now()
            cam.is_online = is_now_online
            await db.commit()

        if was_online and not is_now_online:
            await ws_manager.broadcast("camera_offline", {"mac": device_mac})
            logger.warning(f"[CameraHealth] 摄像头掉线: {device_mac}")
        elif not was_online and is_now_online:
            await ws_manager.broadcast("camera_online", {"mac": device_mac})
            logger.info(f"[CameraHealth] 摄像头恢复: {device_mac}")

    @staticmethod
    def _build_rtsp_url(rtsp_url: str, user: str | None, password: str | None) -> str:
        if not (user or password):
            return rtsp_url
        parsed = urlparse(rtsp_url)
        netloc = f"{user or ''}:{password or ''}@{parsed.hostname or ''}"
        if parsed.port:
            netloc += f":{parsed.port}"
        return urlunparse(parsed._replace(netloc=netloc))

    async def _probe_rtsp(self, rtsp_url: str) -> bool:
        # Use run_in_executor + subprocess.run to avoid asyncio.create_subprocess_exec,
        # which requires ProactorEventLoop on Windows (unavailable under uvicorn SelectorEventLoop).
        loop = asyncio.get_running_loop()
        try:
            result = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    lambda: subprocess.run(
                        [
                            "ffprobe", "-v", "quiet",
                            "-show_entries", "format=duration",
                            "-i", rtsp_url,
                        ],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=5,
                    ),
                ),
                timeout=6,
            )
            return result.returncode == 0
        except Exception:
            return False
