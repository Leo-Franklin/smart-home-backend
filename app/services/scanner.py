import asyncio
from loguru import logger

try:
    from scapy.all import ARP, Ether, srp
    _SCAPY_AVAILABLE = True
except ImportError:
    _SCAPY_AVAILABLE = False

try:
    from mac_vendor_lookup import AsyncMacLookup
    _MAC_LOOKUP_AVAILABLE = True
except ImportError:
    _MAC_LOOKUP_AVAILABLE = False


class Scanner:
    def __init__(self, network: str):
        self.network = network
        self._mac_lookup = AsyncMacLookup() if _MAC_LOOKUP_AVAILABLE else None

    async def arp_scan(self) -> list[dict]:
        if not _SCAPY_AVAILABLE:
            logger.warning("scapy 不可用，跳过 ARP 扫描")
            return []
        logger.info(f"开始 ARP 扫描: {self.network}")
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, self._arp_scan_sync)
        logger.info(f"ARP 扫描完成，发现 {len(result)} 台设备")
        return result

    def _arp_scan_sync(self) -> list[dict]:
        pkt = Ether(dst="ff:ff:ff:ff:ff:ff") / ARP(pdst=self.network)
        answered, _ = srp(pkt, timeout=3, verbose=0)
        return [{"ip": rcv.psrc, "mac": rcv.hwsrc.upper()} for _, rcv in answered]

    async def lookup_vendor(self, mac: str) -> str:
        if self._mac_lookup is None:
            return "Unknown"
        try:
            return await self._mac_lookup.lookup(mac)
        except Exception:
            return "Unknown"

    async def probe_ports(self, ip: str) -> list[int]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._probe_ports_sync, ip)

    def _probe_ports_sync(self, ip: str) -> list[int]:
        try:
            import nmap
            nm = nmap.PortScanner()
            nm.scan(ip, "80,443,554,2020,8080,8443", arguments="-T4 --open")
            ports: list[int] = []
            if ip in nm.all_hosts():
                for proto in nm[ip].all_protocols():
                    ports.extend(nm[ip][proto].keys())
            return ports
        except Exception as e:
            logger.debug(f"nmap 探测失败 {ip}: {e}")
            return []

    @staticmethod
    def guess_device_type(vendor: str, open_ports: list[int]) -> str:
        if 554 in open_ports or 2020 in open_ports:
            return "camera"
        v = vendor.lower()
        if any(kw in v for kw in ("apple", "samsung", "xiaomi", "huawei", "oppo", "vivo")):
            return "phone"
        if any(kw in v for kw in ("intel", "realtek", "dell", "lenovo", "hp ", "asus")):
            return "computer"
        return "unknown"
