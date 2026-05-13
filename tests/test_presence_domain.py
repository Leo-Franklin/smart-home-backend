import pytest
import asyncio
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_auto_start_recording_creates_recording_and_starts_recorder():
    """auto_start_recording should create DB record and call recorder.start_recording."""
    from app.domain.services.presence_domain import PresenceDomainService

    mock_recorder = MagicMock()
    mock_recorder.active = {}
    mock_recorder.start_recording = AsyncMock()
    # Simulate that start_recording adds the task to active dict
    mock_recorder.start_recording.side_effect = lambda mac, url, seg: mock_recorder.active.update({mac: MagicMock(recording_id=42)})
    mock_nas_syncer = MagicMock()

    svc = PresenceDomainService(recorder=mock_recorder, nas_syncer=mock_nas_syncer)

    mock_cam = MagicMock()
    mock_cam.device_mac = "AA:BB:CC:DD:EE:FF"
    mock_cam.rtsp_url = "rtsp://192.168.1.100:554/stream"
    mock_cam.onvif_user = "admin"
    mock_cam.onvif_password = "password"
    mock_cam.is_recording = False

    mock_rec = MagicMock()
    mock_rec.id = 42

    mock_result = MagicMock()
    mock_result.scalar_one_or_none = MagicMock(side_effect=[mock_cam, mock_rec])

    mock_db = MagicMock()
    mock_db.execute = AsyncMock(return_value=mock_result)
    mock_db.add = MagicMock()
    mock_db.commit = AsyncMock()

    async def mock_refresh(obj):
        obj.id = 42
    mock_db.refresh = mock_refresh

    async def mock_aenter(self):
        return mock_db
    mock_db.__aenter__ = mock_aenter
    mock_db.__aexit__ = AsyncMock(return_value=None)

    with patch("app.domain.services.presence_domain.AsyncSessionLocal", return_value=mock_db):
        await svc.auto_start_recording("AA:BB:CC:DD:EE:FF")

    mock_recorder.start_recording.assert_called_once()
    assert mock_recorder.active["AA:BB:CC:DD:EE:FF"].recording_id == 42


@pytest.mark.asyncio
async def test_auto_stop_recording_stops_and_syncs():
    """auto_stop_recording should stop recorder, sync file, update DB."""
    from app.domain.services.presence_domain import PresenceDomainService

    output_path = MagicMock()
    output_path.exists.return_value = True
    output_path.stat.return_value.st_size = 2048
    mock_recorder = MagicMock()
    mock_recorder.stop_recording = AsyncMock(return_value=output_path)

    mock_dest = MagicMock()
    mock_dest.exists.return_value = True
    mock_dest.stat.return_value.st_size = 2048

    mock_nas_syncer = MagicMock()
    mock_nas_syncer.sync_file = MagicMock(return_value=mock_dest)

    svc = PresenceDomainService(recorder=mock_recorder, nas_syncer=mock_nas_syncer)
    svc._ws_manager = MagicMock()
    svc._ws_manager.broadcast = AsyncMock()

    mock_cam = MagicMock()
    mock_cam.is_recording = True

    mock_rec = MagicMock()
    mock_rec.status = "recording"
    mock_rec.camera_mac = "AA:BB:CC:DD:EE:FF"

    mock_result = MagicMock()
    mock_result.scalar_one_or_none = MagicMock(side_effect=[mock_cam, mock_rec])

    mock_db = MagicMock()
    mock_db.execute = AsyncMock(return_value=mock_result)
    mock_db.commit = AsyncMock()

    async def mock_aenter(self):
        return mock_db
    mock_db.__aenter__ = mock_aenter
    mock_db.__aexit__ = AsyncMock(return_value=None)

    with patch("app.domain.services.presence_domain.AsyncSessionLocal", return_value=mock_db):
        await svc.auto_stop_recording("AA:BB:CC:DD:EE:FF")

    mock_recorder.stop_recording.assert_called_once_with("AA:BB:CC:DD:EE:FF")
    assert mock_rec.status == "completed"
    mock_db.commit.assert_called()