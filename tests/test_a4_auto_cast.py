import pytest
import socket
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from dataclasses import dataclass
from datetime import datetime


@dataclass
class FakeTask:
    camera_mac: str
    output_path: Path
    started_at: datetime
    recording_id: int | None = None


def _make_cast_context(auto_cast_dlna: str | None, av_transport_url: str | None = "http://tv/avt"):
    """Return mocks for DB session that simulates the _on_recording_complete DB queries."""
    recording = MagicMock()
    recording.id = 1
    recording.status = None
    recording.file_path = None
    recording.file_size = None
    recording.ended_at = None
    recording.duration = None

    camera = MagicMock()
    camera.device_mac = "AA:BB:CC:DD:EE:FF"
    camera.is_recording = True
    camera.auto_cast_dlna = auto_cast_dlna

    dlna_device = MagicMock()
    dlna_device.av_transport_url = av_transport_url

    return recording, camera, dlna_device


@pytest.mark.asyncio
async def test_auto_cast_dlna_calls_controller_when_configured(tmp_path):
    """When camera.auto_cast_dlna is set, DLNAController.set_uri + play are called."""
    from app.domain.services.recording_domain import RecordingDomainService

    output_file = tmp_path / "rec.mp4"
    output_file.write_bytes(b"0" * 1024 * 20)  # 20 KB fake video

    task = FakeTask(
        camera_mac="AA:BB:CC:DD:EE:FF",
        output_path=output_file,
        started_at=datetime(2026, 4, 29, 10, 0, 0),
        recording_id=1,
    )

    recording, camera, dlna_device = _make_cast_context(auto_cast_dlna="uuid:some-udn-123")

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)

    execute_results = [
        MagicMock(**{"scalar_one_or_none.return_value": recording}),
        MagicMock(**{"scalar_one_or_none.return_value": camera}),
        MagicMock(**{"scalar_one_or_none.return_value": dlna_device}),
    ]
    mock_session.execute = AsyncMock(side_effect=execute_results)
    mock_session.commit = AsyncMock()

    mock_ctrl = AsyncMock()
    mock_nas_syncer = MagicMock()
    mock_nas_syncer.sync_file.return_value = output_file
    mock_ws_manager = MagicMock()
    mock_ws_manager.broadcast = AsyncMock()

    with (
        patch("app.domain.services.recording_domain.AsyncSessionLocal", return_value=mock_session),
        patch("app.domain.services.recording_domain.DLNAController", return_value=mock_ctrl),
        patch("app.domain.services.recording_domain.ws_manager", mock_ws_manager),
    ):
        svc = RecordingDomainService(nas_syncer=mock_nas_syncer)
        await svc.on_recording_complete(task)

    mock_ctrl.set_uri.assert_called_once()
    mock_ctrl.play.assert_called_once()


@pytest.mark.asyncio
async def test_no_auto_cast_when_field_is_none(tmp_path):
    """When auto_cast_dlna is None, DLNAController is not called."""
    from app.domain.services.recording_domain import RecordingDomainService

    output_file = tmp_path / "rec.mp4"
    output_file.write_bytes(b"0" * 1024 * 20)

    task = FakeTask(
        camera_mac="BB:CC:DD:EE:FF:00",
        output_path=output_file,
        started_at=datetime(2026, 4, 29, 10, 0, 0),
        recording_id=2,
    )

    recording, camera, _ = _make_cast_context(auto_cast_dlna=None)

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)

    execute_results = [
        MagicMock(**{"scalar_one_or_none.return_value": recording}),
        MagicMock(**{"scalar_one_or_none.return_value": camera}),
    ]
    mock_session.execute = AsyncMock(side_effect=execute_results)
    mock_session.commit = AsyncMock()

    mock_nas_syncer = MagicMock()
    mock_ws_manager = MagicMock()
    mock_ws_manager.broadcast = AsyncMock()

    with (
        patch("app.domain.services.recording_domain.AsyncSessionLocal", return_value=mock_session),
        patch("app.domain.services.recording_domain.DLNAController") as mock_ctrl_cls,
        patch("app.domain.services.recording_domain.ws_manager", mock_ws_manager),
    ):
        svc = RecordingDomainService(nas_syncer=mock_nas_syncer)
        await svc.on_recording_complete(task)

    mock_ctrl_cls.assert_not_called()
