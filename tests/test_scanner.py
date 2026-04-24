import pytest
from app.services.scanner import Scanner


def test_guess_device_type_camera():
    assert Scanner.guess_device_type("TP-LINK", [554]) == "camera"


def test_guess_device_type_phone():
    assert Scanner.guess_device_type("Apple", []) == "phone"


def test_guess_device_type_unknown():
    assert Scanner.guess_device_type("SomeUnknown", []) == "unknown"
