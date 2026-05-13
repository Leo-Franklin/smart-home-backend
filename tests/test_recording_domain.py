import pytest
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# Test RecordingDomainService.on_recording_complete
@pytest.mark.asyncio
async def test_on_recording_complete_updates_recording_and_camera():
    """Recording completion should update DB status, sync to NAS, and broadcast WS."""
    from app.domain.services.recording_domain import RecordingDomainService

    # Setup mock task
    task = MagicMock()
    task.camera_mac = "AA:BB:CC:DD:EE:FF"
    task.output_path = Path("/tmp/test.mp4")
    task.started_at = datetime.now()
    task.recording_id = 1

    # Setup mock NAS syncer - sync function since it's called via run_in_executor
    mock_dest = MagicMock()
    mock_dest.exists.return_value = True
    mock_dest.stat.return_value.st_size = 1024

    mock_nas_syncer = MagicMock()
    mock_nas_syncer.sync_file = MagicMock(return_value=mock_dest)

    # Setup mock DB session
    mock_rec = MagicMock()
    mock_rec.status = "recording"
    mock_cam = MagicMock()
    mock_cam.is_recording = True
    mock_cam.device_mac = "AA:BB:CC:DD:EE:FF"
    mock_cam.auto_cast_dlna = None

    mock_result = MagicMock()
    mock_result.scalar_one_or_none = MagicMock(side_effect=[mock_rec, mock_cam])

    mock_db = MagicMock()
    mock_db.execute = AsyncMock(return_value=mock_result)
    mock_db.commit = AsyncMock()

    # Mock AsyncSessionLocal to return an async context manager
    mock_session_context = MagicMock()
    mock_session_context.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_context.__aexit__ = AsyncMock(return_value=None)
    AsyncSessionLocal = MagicMock(return_value=mock_session_context)

    svc = RecordingDomainService(nas_syncer=mock_nas_syncer)
    svc._ws_manager = MagicMock()
    svc._ws_manager.broadcast = AsyncMock()

    with patch("app.domain.services.recording_domain.AsyncSessionLocal", AsyncSessionLocal):
        await svc.on_recording_complete(task)

    mock_db.commit.assert_called()
    assert mock_rec.status == "completed"
    assert mock_cam.is_recording == False

@pytest.mark.asyncio
async def test_on_recording_complete_triggers_dlna_cast():
    """When camera has auto_cast_dlna set, DLNA cast should be triggered."""
    from app.domain.services.recording_domain import RecordingDomainService

    task = MagicMock()
    task.camera_mac = "AA:BB:CC:DD:EE:FF"
    task.output_path = Path("/tmp/test.mp4")
    task.started_at = datetime.now()
    task.recording_id = 1

    mock_dest = MagicMock()
    mock_dest.exists.return_value = True
    mock_dest.stat.return_value.st_size = 1024

    mock_nas_syncer = MagicMock()
    mock_nas_syncer.sync_file = MagicMock(return_value=mock_dest)

    mock_dlna_dev = MagicMock()
    mock_dlna_dev.av_transport_url = "http://192.168.1.100:8080/av_transport"

    mock_rec = MagicMock()
    mock_cam = MagicMock()
    mock_cam.is_recording = True
    mock_cam.auto_cast_dlna = "uuid:dlna-device-1"

    mock_result = MagicMock()
    mock_result.scalar_one_or_none = MagicMock(side_effect=[mock_rec, mock_cam, mock_dlna_dev])

    mock_db = MagicMock()
    mock_db.execute = AsyncMock(return_value=mock_result)
    mock_db.commit = AsyncMock()

    mock_session_context = MagicMock()
    mock_session_context.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_context.__aexit__ = AsyncMock(return_value=None)
    AsyncSessionLocal = MagicMock(return_value=mock_session_context)

    svc = RecordingDomainService(nas_syncer=mock_nas_syncer)
    svc._ws_manager = MagicMock()
    svc._ws_manager.broadcast = AsyncMock()
    svc._cast_recording = AsyncMock()

    with patch("app.domain.services.recording_domain.AsyncSessionLocal", AsyncSessionLocal):
        await svc.on_recording_complete(task)

    svc._cast_recording.assert_called_once()

@pytest.mark.asyncio
async def test_on_recording_failed_updates_recording():
    """Recording failure should mark status as failed and broadcast."""
    from app.domain.services.recording_domain import RecordingDomainService

    task = MagicMock()
    task.camera_mac = "AA:BB:CC:DD:EE:FF"
    task.recording_id = 1

    mock_rec = MagicMock()
    mock_cam = MagicMock()
    mock_cam.is_recording = True

    mock_result = MagicMock()
    mock_result.scalar_one_or_none = MagicMock(side_effect=[mock_rec, mock_cam])

    mock_db = MagicMock()
    mock_db.execute = AsyncMock(return_value=mock_result)
    mock_db.commit = AsyncMock()

    mock_session_context = MagicMock()
    mock_session_context.__aenter__ = AsyncMock(return_value=mock_db)
    mock_session_context.__aexit__ = AsyncMock(return_value=None)
    AsyncSessionLocal = MagicMock(return_value=mock_session_context)

    svc = RecordingDomainService(nas_syncer=MagicMock())
    svc._ws_manager = MagicMock()
    svc._ws_manager.broadcast = AsyncMock()

    with patch("app.domain.services.recording_domain.AsyncSessionLocal", AsyncSessionLocal):
        await svc.on_recording_failed(task, retcode=1, stderr="test error")

    assert mock_rec.status == "failed"
    assert "test error" in mock_rec.error_msg
    mock_db.commit.assert_called()