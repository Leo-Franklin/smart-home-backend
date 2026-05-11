import pytest
from app.services.scanner import Scanner


class TestGuessDeviceType:

    def test_tplink_camera_by_hostname(self):
        """TP-Link摄像头 hostname 含 cam/ipc，vendor 为 tp-link，应返回 camera 而非 router"""
        result = Scanner.guess_device_type(
            vendor="TP-LINK TECHNOLOGIES",
            open_ports=[80, 554],
            hostname="TP-Link_Camera_C100"
        )
        assert result == "camera"

    def test_tplink_camera_no_hostname(self):
        """TP-Link 摄像头没有 hostname 但开放 554 端口，应返回 camera"""
        result = Scanner.guess_device_type(
            vendor="TP-LINK TECHNOLOGIES",
            open_ports=[554],
            hostname=None
        )
        assert result == "camera"

    def test_printer_by_port(self):
        """631=IPP, 9100=Raw Print, 515=LPD，应返回 printer"""
        assert Scanner.guess_device_type("Unknown", [631]) == "printer"
        assert Scanner.guess_device_type("Unknown", [9100]) == "printer"
        assert Scanner.guess_device_type("Unknown", [515]) == "printer"

    def test_new_camera_ports(self):
        """新增端口 8080/8443/8554/5000 应识别为 camera"""
        assert Scanner.guess_device_type("Unknown", [8080]) == "camera"
        assert Scanner.guess_device_type("Unknown", [8443]) == "camera"
        assert Scanner.guess_device_type("Unknown", [8554]) == "camera"
        assert Scanner.guess_device_type("Unknown", [5000]) == "camera"

    def test_tplink_router_without_camera_keywords(self):
        """TP-Link 无摄像头关键词时应返回 router"""
        result = Scanner.guess_device_type(
            vendor="TP-LINK TECHNOLOGIES",
            open_ports=[],
            hostname="TP-Link_Router"
        )
        assert result == "router"

    def test_honor_phone(self):
        """荣耀手机 vendor 匹配 honor 应返回 phone"""
        assert Scanner.guess_device_type("HONOR", []) == "phone"
        assert Scanner.guess_device_type("HONOR Technology", []) == "phone"
        assert Scanner.guess_device_type("honor", []) == "phone"

    def test_tuya_iot(self):
        """Tuya 设备应识别为 iot"""
        assert Scanner.guess_device_type("Tuya", []) == "iot"
        assert Scanner.guess_device_type("tuya", []) == "iot"

    def test_broadlink_iot(self):
        """Broadlink 空调伴侣应识别为 iot"""
        assert Scanner.guess_device_type("BroadLink", []) == "iot"
        assert Scanner.guess_device_type("broadlink", []) == "iot"

    def test_yeelight_iot(self):
        assert Scanner.guess_device_type("Yeelight", []) == "iot"
        assert Scanner.guess_device_type("yeelight", []) == "iot"

    def test_aqara_iot(self):
        assert Scanner.guess_device_type("Aqara", []) == "iot"

    def test_sonoff_iot(self):
        assert Scanner.guess_device_type("Sonoff", []) == "iot"

    def test_switchbot_iot(self):
        assert Scanner.guess_device_type("SwitchBot", []) == "iot"
        assert Scanner.guess_device_type("switchbot", []) == "iot"

    def test_ezviz_camera(self):
        """萤石摄像头应识别为 camera"""
        assert Scanner.guess_device_type("EZVIZ", []) == "camera"
        assert Scanner.guess_device_type("ezviz", []) == "camera"
        assert Scanner.guess_device_type("萤石", []) == "camera"