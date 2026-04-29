import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime


@pytest.mark.asyncio
async def test_arrived_triggers_auto_start_recording():
    """When a member arrives and has auto_record_cameras, auto_start_cb is called."""
    from app.services.presence_service import PresenceService

    auto_start_cb = AsyncMock()
    auto_stop_cb = AsyncMock()
    svc = PresenceService(poll_interval=30)
    await svc.start(auto_start_cb=auto_start_cb, auto_stop_cb=auto_stop_cb)
    svc._task.cancel()  # don't actually run the loop

    member = MagicMock()
    member.id = 1
    member.name = "Alice"
    member.is_home = False
    member.webhook_url = None
    member.auto_record_cameras = ["AA:BB:CC:DD:EE:FF"]

    session = AsyncMock()
    session.add = MagicMock()
    session.commit = AsyncMock()

    svc._initialized = True  # skip first-run baseline

    with patch.object(svc, "_send_webhook", new_callable=AsyncMock):
        await svc._fire_event(session, member, is_home=True, triggered_mac="AA:BB:CC:DD:EE:FF")

    # Give create_task callbacks time to be scheduled
    await asyncio.sleep(0)

    auto_start_cb.assert_called_once_with("AA:BB:CC:DD:EE:FF")
    auto_stop_cb.assert_not_called()


@pytest.mark.asyncio
async def test_left_triggers_auto_stop_when_no_other_home_member():
    """When a member leaves and no other member is home with same camera, auto_stop_cb fires."""
    from app.services.presence_service import PresenceService

    auto_start_cb = AsyncMock()
    auto_stop_cb = AsyncMock()
    svc = PresenceService(poll_interval=30)
    await svc.start(auto_start_cb=auto_start_cb, auto_stop_cb=auto_stop_cb)
    svc._task.cancel()

    member = MagicMock()
    member.id = 1
    member.name = "Alice"
    member.is_home = True
    member.webhook_url = None
    member.auto_record_cameras = ["AA:BB:CC:DD:EE:FF"]

    # No other members home
    session = AsyncMock()
    session.add = MagicMock()
    session.commit = AsyncMock()

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = []  # no other home members
    session.execute = AsyncMock(return_value=mock_result)

    svc._initialized = True

    with patch.object(svc, "_send_webhook", new_callable=AsyncMock):
        await svc._fire_event(session, member, is_home=False, triggered_mac="AA:BB:CC:DD:EE:FF")

    await asyncio.sleep(0)

    auto_stop_cb.assert_called_once_with("AA:BB:CC:DD:EE:FF")
    auto_start_cb.assert_not_called()


@pytest.mark.asyncio
async def test_no_auto_record_cameras_no_callback():
    """Members without auto_record_cameras do not trigger any recording callback."""
    from app.services.presence_service import PresenceService

    auto_start_cb = AsyncMock()
    auto_stop_cb = AsyncMock()
    svc = PresenceService(poll_interval=30)
    await svc.start(auto_start_cb=auto_start_cb, auto_stop_cb=auto_stop_cb)
    svc._task.cancel()

    member = MagicMock()
    member.id = 2
    member.name = "Bob"
    member.is_home = False
    member.webhook_url = None
    member.auto_record_cameras = []  # empty list

    session = AsyncMock()
    session.add = MagicMock()
    session.commit = AsyncMock()

    svc._initialized = True

    with patch.object(svc, "_send_webhook", new_callable=AsyncMock):
        await svc._fire_event(session, member, is_home=True, triggered_mac=None)

    await asyncio.sleep(0)

    auto_start_cb.assert_not_called()
    auto_stop_cb.assert_not_called()
