import asyncio
from datetime import datetime
from pathlib import Path
from loguru import logger
from sqlalchemy import select
from app.services.recorder import Recorder, RecordingTask
from app.services.nas_syncer import NasSyncer
from app.services.ws_manager import ws_manager
from app.database import AsyncSessionLocal
from app.models.camera import Camera as CameraModel
from app.models.recording import Recording as RecordingModel
from app.config import get_settings


class PresenceDomainService:
    def __init__(self, recorder: Recorder, nas_syncer: NasSyncer):
        self._recorder = recorder
        self._nas_syncer = nas_syncer
        self._ws_manager = ws_manager

    async def auto_start_recording(self, camera_mac: str) -> None:
        """Start recording when presence is detected."""
        async with AsyncSessionLocal() as db:
            cam = (await db.execute(
                select(CameraModel).where(CameraModel.device_mac == camera_mac)
            )).scalar_one_or_none()
            if not cam or not cam.rtsp_url or cam.is_recording:
                return
            rtsp_url = cam.rtsp_url
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
            await self._recorder.start_recording(camera_mac, rtsp_url, get_settings().recording_segment_seconds)
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

        if camera_mac in self._recorder.active:
            self._recorder.active[camera_mac].recording_id = rec_id
        else:
            logger.warning(f"[A1] 录制任务已结束，无法设置 recording_id {rec_id}: {camera_mac}")

    async def auto_stop_recording(self, camera_mac: str) -> None:
        """Stop recording when presence is lost."""
        output_path = await self._recorder.stop_recording(camera_mac)
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
                            None, lambda: self._nas_syncer.sync_file(output_path, camera_mac)
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

        await self._ws_manager.broadcast("recording_completed", {"camera_mac": camera_mac})
        logger.info(f"[A1] 自动停止录制完成: {camera_mac}")