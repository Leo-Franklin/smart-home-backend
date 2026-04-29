from datetime import datetime, timedelta
from unittest.mock import MagicMock
from app.routers.devices import _find_unknown_devices


def _make_device(last_seen_seconds_ago: int | None = None):
    """Helper: create a mock Device with last_seen."""
    device = MagicMock()
    if last_seen_seconds_ago is None:
        device.last_seen = None
    else:
        device.last_seen = datetime.now() - timedelta(seconds=last_seen_seconds_ago)
    return device


def test_new_mac_not_in_member_devices_is_unknown():
    enriched = [{"mac": "AA:BB:CC:DD:EE:FF", "ip": "192.168.1.100", "vendor": "Apple", "hostname": None}]
    existing_map = {}      # device not in DB yet → truly new
    bound_macs = set()
    now = datetime.now()

    result = _find_unknown_devices(enriched, existing_map, bound_macs, now)

    assert len(result) == 1
    assert result[0]["mac"] == "AA:BB:CC:DD:EE:FF"


def test_bound_mac_is_not_unknown():
    enriched = [{"mac": "AA:BB:CC:DD:EE:FF", "ip": "192.168.1.100", "vendor": "Apple", "hostname": None}]
    existing_map = {}
    bound_macs = {"AA:BB:CC:DD:EE:FF"}
    now = datetime.now()

    result = _find_unknown_devices(enriched, existing_map, bound_macs, now)

    assert result == []


def test_recently_seen_unknown_mac_is_suppressed():
    """A device seen 2 hours ago should NOT trigger another alert."""
    mac = "BB:CC:DD:EE:FF:00"
    enriched = [{"mac": mac, "ip": "192.168.1.101", "vendor": "Unknown", "hostname": None}]
    existing_map = {mac: _make_device(last_seen_seconds_ago=7200)}  # 2 hours ago
    bound_macs = set()
    now = datetime.now()

    result = _find_unknown_devices(enriched, existing_map, bound_macs, now, staleness_hours=24)

    assert result == []


def test_stale_unknown_mac_triggers_alert():
    """A device last seen 25 hours ago should trigger an alert again."""
    mac = "CC:DD:EE:FF:00:11"
    enriched = [{"mac": mac, "ip": "192.168.1.102", "vendor": "Unknown", "hostname": None}]
    existing_map = {mac: _make_device(last_seen_seconds_ago=25 * 3600)}  # 25 hours ago
    bound_macs = set()
    now = datetime.now()

    result = _find_unknown_devices(enriched, existing_map, bound_macs, now, staleness_hours=24)

    assert len(result) == 1
