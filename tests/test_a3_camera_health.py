import asyncio
import pytest
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_probe_rtsp_success():
    from app.services.camera_health import CameraHealthChecker

    checker = CameraHealthChecker(interval=60)

    class FakeProc:
        returncode = 0
        async def wait(self):
            return 0

    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=FakeProc()):
        result = await checker._probe_rtsp("rtsp://192.168.1.100:554/stream")

    assert result is True


@pytest.mark.asyncio
async def test_probe_rtsp_nonzero_exit_returns_false():
    from app.services.camera_health import CameraHealthChecker

    checker = CameraHealthChecker(interval=60)

    class FakeProc:
        returncode = 1
        async def wait(self):
            return 1

    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=FakeProc()):
        result = await checker._probe_rtsp("rtsp://192.168.1.100:554/stream")

    assert result is False


@pytest.mark.asyncio
async def test_probe_rtsp_timeout_returns_false():
    from app.services.camera_health import CameraHealthChecker

    checker = CameraHealthChecker(interval=60)

    class SlowProc:
        returncode = None
        async def wait(self):
            await asyncio.sleep(100)

    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=SlowProc()):
        result = await checker._probe_rtsp("rtsp://192.168.1.100:554/stream")

    assert result is False


@pytest.mark.asyncio
async def test_probe_rtsp_exception_returns_false():
    from app.services.camera_health import CameraHealthChecker

    checker = CameraHealthChecker(interval=60)

    with patch("asyncio.create_subprocess_exec", side_effect=OSError("ffprobe not found")):
        result = await checker._probe_rtsp("rtsp://192.168.1.100:554/stream")

    assert result is False
