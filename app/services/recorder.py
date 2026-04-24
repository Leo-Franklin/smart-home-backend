import asyncio
import subprocess
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass
from loguru import logger


@dataclass
class RecordingTask:
    camera_mac: str
    process: subprocess.Popen
    output_path: Path
    started_at: datetime
    segment_seconds: int
    rtsp_url: str


class Recorder:
    def __init__(self, temp_dir: str):
        self.temp_dir = Path(temp_dir)
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self.active: dict[str, RecordingTask] = {}
        self._monitor_task: asyncio.Task | None = None
        self._on_complete_cb = None
        self._on_failed_cb = None

    def set_callbacks(self, on_complete=None, on_failed=None):
        self._on_complete_cb = on_complete
        self._on_failed_cb = on_failed

    async def start_monitor(self):
        self._monitor_task = asyncio.create_task(self._monitor_loop())
        logger.info("RecordingMonitor 已启动")

    async def stop_monitor(self):
        if self._monitor_task:
            self._monitor_task.cancel()
            logger.info("RecordingMonitor 已停止")

    async def start_recording(self, camera_mac: str, rtsp_url: str, segment_seconds: int = 1800) -> str:
        if camera_mac in self.active:
            raise RuntimeError(f"摄像头 {camera_mac} 已在录制中")

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_mac = camera_mac.replace(":", "")
        output_path = self.temp_dir / f"{safe_mac}_{ts}.mp4"

        cmd = [
            "ffmpeg", "-y",
            "-rtsp_transport", "tcp",
            "-i", rtsp_url,
            "-c", "copy",
            "-t", str(segment_seconds),
            "-movflags", "+faststart",
            str(output_path),
        ]

        logger.info(f"启动录制: {camera_mac} → {output_path}")
        loop = asyncio.get_event_loop()
        proc = await loop.run_in_executor(
            None,
            lambda: subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE),
        )

        self.active[camera_mac] = RecordingTask(
            camera_mac=camera_mac,
            process=proc,
            output_path=output_path,
            started_at=datetime.now(),
            segment_seconds=segment_seconds,
            rtsp_url=rtsp_url,
        )
        return str(output_path)

    async def stop_recording(self, camera_mac: str) -> Path | None:
        task = self.active.pop(camera_mac, None)
        if not task:
            return None
        logger.info(f"停止录制: {camera_mac}")
        try:
            task.process.send_signal(2)  # SIGINT → FFmpeg 正常写文件尾
        except Exception:
            pass
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: task.process.wait(timeout=10))
        if task.process.poll() is None:
            task.process.kill()
        if task.output_path.exists() and task.output_path.stat().st_size > 0:
            return task.output_path
        return None

    async def _monitor_loop(self):
        while True:
            await asyncio.sleep(10)
            finished = [
                (mac, task.process.poll(), task)
                for mac, task in list(self.active.items())
                if task.process.poll() is not None
            ]
            for mac, retcode, task in finished:
                self.active.pop(mac, None)
                if retcode == 0:
                    logger.info(f"录制正常完成: {mac}")
                    if self._on_complete_cb:
                        await self._on_complete_cb(task)
                else:
                    stderr = task.process.stderr.read().decode(errors="replace")[-500:]
                    logger.error(f"录制异常退出: {mac}, code={retcode}, stderr={stderr}")
                    if self._on_failed_cb:
                        await self._on_failed_cb(task, retcode, stderr)
