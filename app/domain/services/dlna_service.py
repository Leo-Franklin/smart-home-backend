import asyncio
import re
import socket
import time
import xml.etree.ElementTree as ET
from urllib.parse import urljoin, urlparse

import httpx
from loguru import logger

SSDP_ADDR = "239.255.255.250"
SSDP_PORT = 1900
AV_TRANSPORT_SERVICE = "urn:schemas-upnp-org:service:AVTransport:1"
RENDERING_CONTROL_SERVICE = "urn:schemas-upnp-org:service:RenderingControl:1"


def _build_soap(service_type: str, action: str, args: dict[str, str]) -> str:
    args_xml = "".join(f"<{k}>{v}</{k}>" for k, v in args.items())
    return (
        '<?xml version="1.0"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"'
        ' s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
        "<s:Body>"
        f'<u:{action} xmlns:u="{service_type}">'
        f"{args_xml}"
        f"</u:{action}>"
        "</s:Body>"
        "</s:Envelope>"
    )


def _ssdp_search_sync(timeout: float = 5.0) -> list[str]:
    """Blocking SSDP M-SEARCH. Returns unique Location URLs of UPnP devices."""
    m_search = (
        "M-SEARCH * HTTP/1.1\r\n"
        f"HOST: {SSDP_ADDR}:{SSDP_PORT}\r\n"
        'MAN: "ssdp:discover"\r\n'
        "MX: 3\r\n"
        "ST: ssdp:all\r\n"
        "\r\n"
    )
    locations: list[str] = []
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 4)
        sock.sendto(m_search.encode(), (SSDP_ADDR, SSDP_PORT))
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            sock.settimeout(remaining)
            try:
                data, _ = sock.recvfrom(65535)
            except socket.timeout:
                break
            response = data.decode(errors="ignore")
            for line in response.splitlines():
                if line.upper().startswith("LOCATION:"):
                    loc = line.split(":", 1)[1].strip()
                    if loc not in locations:
                        locations.append(loc)
    except Exception as e:
        logger.warning(f"SSDP 搜索异常: {e}")
    finally:
        sock.close()
    return locations


async def ssdp_search(timeout: float = 5.0) -> list[str]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _ssdp_search_sync, timeout)


async def fetch_device_info(location_url: str) -> dict | None:
    """Fetch UPnP device description XML and extract service control URLs.

    Only returns devices that expose an AVTransport service (i.e. can play media).
    """
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(location_url)
            resp.raise_for_status()
    except Exception as e:
        logger.debug(f"获取设备描述失败 {location_url}: {e}")
        return None

    try:
        # Strip namespace declarations so ElementTree can use plain tag names
        xml_text = re.sub(r'\s+xmlns(?::[^=]+)?="[^"]+"', "", resp.text)
        root = ET.fromstring(xml_text)
        device = root.find(".//device")
        if device is None:
            return None

        parsed = urlparse(location_url)
        base_url = f"{parsed.scheme}://{parsed.netloc}"

        info: dict = {
            "udn": (device.findtext("UDN") or "").strip(),
            "friendly_name": device.findtext("friendlyName"),
            "device_type": device.findtext("deviceType"),
            "manufacturer": device.findtext("manufacturer"),
            "model_name": device.findtext("modelName"),
            "ip": parsed.hostname,
            "location_url": location_url,
            "av_transport_url": None,
            "rendering_control_url": None,
        }

        if not info["udn"]:
            return None

        for service in root.findall(".//service"):
            stype = service.findtext("serviceType") or ""
            ctrl = service.findtext("controlURL") or ""
            if AV_TRANSPORT_SERVICE in stype:
                info["av_transport_url"] = urljoin(base_url, ctrl)
            elif RENDERING_CONTROL_SERVICE in stype:
                info["rendering_control_url"] = urljoin(base_url, ctrl)

        if not info["av_transport_url"]:
            return None  # Not a media renderer

        return info
    except Exception as e:
        logger.debug(f"解析设备描述 XML 失败 {location_url}: {e}")
        return None


class DLNAController:
    """Thin UPnP AVTransport SOAP controller for a single MediaRenderer."""

    def __init__(self, av_transport_url: str):
        self.url = av_transport_url

    async def _soap(self, action: str, args: dict[str, str]) -> str:
        body = _build_soap(AV_TRANSPORT_SERVICE, action, args)
        headers = {
            "Content-Type": 'text/xml; charset="utf-8"',
            "SOAPAction": f'"urn:schemas-upnp-org:service:AVTransport:1#{action}"',
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(self.url, content=body.encode(), headers=headers)
            resp.raise_for_status()
            return resp.text

    async def set_uri(self, uri: str, metadata: str = "") -> None:
        await self._soap("SetAVTransportURI", {
            "InstanceID": "0",
            "CurrentURI": uri,
            "CurrentURIMetaData": metadata,
        })

    async def play(self, speed: str = "1") -> None:
        await self._soap("Play", {"InstanceID": "0", "Speed": speed})

    async def pause(self) -> None:
        await self._soap("Pause", {"InstanceID": "0"})

    async def stop(self) -> None:
        await self._soap("Stop", {"InstanceID": "0"})

    async def get_transport_info(self) -> dict:
        try:
            xml_text = await self._soap("GetTransportInfo", {"InstanceID": "0"})
            xml_text = re.sub(r'\s+xmlns(?::[^=]+)?="[^"]+"', "", xml_text)
            root = ET.fromstring(xml_text)
            return {
                "current_transport_state": root.findtext(".//CurrentTransportState") or "UNKNOWN",
                "current_transport_status": root.findtext(".//CurrentTransportStatus") or "UNKNOWN",
                "current_speed": root.findtext(".//CurrentSpeed") or "0",
            }
        except Exception:
            return {
                "current_transport_state": "UNKNOWN",
                "current_transport_status": "UNKNOWN",
                "current_speed": "0",
            }
