import asyncio
import shutil
import socket
import time
from datetime import datetime
from pathlib import Path
from loguru import logger
from sqlalchemy import select
from app.services.nas_syncer import NasSyncer
from app.services.ws_manager import ws_manager
from app.services.dlna_service import DLNAController
from app.database import AsyncSessionLocal
from app.models.recording import Recording
from app.models.camera import Camera
from app.models.dlna_device import DLNADevice
from app.config import get_settings


class RecordingDomainService:
    def __init__(self, nas_syncer: NasSyncer):
        self._nas_syncer = nas_syncer
        self._ws_manager = ws_manager

    async def should_continue_recording(self, camera_mac: str) -> bool:
        async with AsyncSessionLocal() as db:
            cam = (await db.execute(select(Camera).where(Camera.device_mac == camera_mac))).scalar_one_or_none()
            return cam.is_recording if cam else False

    async def on_recording_complete(self, task):
        """Handle recording completion: sync to NAS, update DB, trigger DLNA cast."""
        loop = asyncio.get_running_loop()
        try:
            dest = await loop.run_in_executor(
                None, lambda: self._nas_syncer.sync_file(task.output_path, task.camera_mac)
            )
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

            await db.commit()

            if cam and cam.auto_cast_dlna:
                dlna_dev = (await db.execute(
                    select(DLNADevice).where(DLNADevice.udn == cam.auto_cast_dlna)
                )).scalar_one_or_none()
                if dlna_dev and dlna_dev.av_transport_url:
                    await self._cast_recording(dlna_dev.av_transport_url, dest_str, task.camera_mac)

        logger.info(f"录制完成 [{task.camera_mac}] id={task.recording_id} 时长={duration}s")
        await self._ws_manager.broadcast("recording_completed", {
            "camera_mac": task.camera_mac,
            "recording_id": task.recording_id
        })

    async def _probe_duration(self, path: Path) -> int | None:
        """Probe actual media duration via ffprobe. Returns None if unreachable or 0."""
        import subprocess
        try:
            result = subprocess.run(
                ["ffprobe", "-v", "quiet", "-print_format", "json",
                 "-show_format", str(path)],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                import json
                d = json.loads(result.stdout)
                dur = float(d["format"].get("duration", 0))
                return int(dur) if dur > 0 else None
        except Exception:
            pass
        return None

    async def on_recording_failed(self, task, retcode: int, stderr: str):
        """Handle recording failure: probe actual duration, treat as completed if >= 30s."""
        actual_duration = None
        dest_str = str(task.output_path)

        # Probe actual media duration from file
        if task.output_path.exists():
            loop = asyncio.get_running_loop()
            try:
                actual_duration = await loop.run_in_executor(
                    None, lambda: self._probe_duration(task.output_path)
                )
            except Exception:
                pass

        async with AsyncSessionLocal() as db:
            if task.recording_id:
                result = await db.execute(select(Recording).where(Recording.id == task.recording_id))
                rec = result.scalar_one_or_none()
                if rec:
                    # If we got ≥30s of actual media, treat as completed (stream was healthy before failure)
                    if actual_duration is not None and actual_duration >= 30:
                        ended_at = datetime.now()
                        rec.status = "completed"
                        rec.ended_at = ended_at
                        rec.duration = actual_duration
                        # Sync to NAS
                        try:
                            sync_dest = await loop.run_in_executor(
                                None, lambda: self._nas_syncer.sync_file(task.output_path, task.camera_mac)
                            )
                            rec.file_path = str(sync_dest)
                            rec.file_size = sync_dest.stat().st_size if sync_dest.exists() else None
                        except Exception as e:
                            logger.error(f"NAS同步失败 [{task.camera_mac}]: {e}")
                            rec.file_path = dest_str
                            rec.file_size = task.output_path.stat().st_size if task.output_path.exists() else None
                        logger.info(f"录制异常终止 [{task.camera_mac}] id={task.recording_id}，实际时长={actual_duration}s，标记为completed")
                    else:
                        rec.status = "failed"
                        rec.error_msg = (stderr or f"退出码 {retcode}")[:500]
                        rec.ended_at = datetime.now()
                        rec.file_path = dest_str
                        rec.file_size = task.output_path.stat().st_size if task.output_path.exists() else None
                        logger.warning(f"录制失败 [{task.camera_mac}] id={task.recording_id}，实际时长={actual_duration}s < 30s，标记为failed")

            cam = (await db.execute(select(Camera).where(Camera.device_mac == task.camera_mac))).scalar_one_or_none()
            if cam:
                cam.is_recording = False

            await db.commit()

        await self._ws_manager.broadcast("recording_completed" if actual_duration and actual_duration >= 30 else "recording_failed", {
            "camera_mac": task.camera_mac,
            "recording_id": task.recording_id
        })

    async def _cast_recording(self, av_transport_url: str, file_path: str, camera_mac: str):
        """Copy recording to dlna-media directory and cast to target DLNA device."""
        src = Path(file_path)
        if not src.exists():
            logger.warning(f"[A4] 投屏跳过，文件不存在: {file_path}")
            return

        media_dir = Path("data/dlna_media")
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
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
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