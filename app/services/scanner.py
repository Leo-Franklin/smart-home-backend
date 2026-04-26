import asyncio
import ipaddress
import re
import socket
import subprocess
import sys
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


def detect_local_network() -> str:
    """Use the default-route interface IP to derive the /24 network."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
        network = ipaddress.ip_network(f"{local_ip}/24", strict=False)
        logger.info(f"自动检测网段: {network} (本机 IP: {local_ip})")
        return str(network)
    except Exception as e:
        logger.warning(f"网段自动检测失败，回退到 192.168.1.0/24: {e}")
        return "192.168.1.0/24"


class Scanner:
    def __init__(self, network: str):
        self.network = detect_local_network() if network.strip().lower() == "auto" else network
        self._mac_lookup = AsyncMacLookup() if _MAC_LOOKUP_AVAILABLE else None

    async def arp_scan(self) -> list[dict]:
        loop = asyncio.get_running_loop()
        seen: dict[str, dict] = {}  # mac -> entry, dedup key

        # Primary path: ping-sweep to populate OS ARP cache, then read it.
        # This works without Npcap or admin rights and is the most reliable on Windows.
        logger.info(f"开始网络扫描: {self.network}")
        await loop.run_in_executor(None, self._ping_sweep_sync)
        for d in await loop.run_in_executor(None, self._arp_table_scan_sync):
            seen[d["mac"]] = d
        logger.info(f"arp -a 发现 {len(seen)} 台设备")

        # Supplementary: Scapy ARP broadcast catches devices that block ICMP ping.
        if _SCAPY_AVAILABLE:
            try:
                for d in await loop.run_in_executor(None, self._arp_scan_sync):
                    seen.setdefault(d["mac"], d)
                logger.debug(f"Scapy 补充后共 {len(seen)} 台设备")
            except Exception as e:
                logger.debug(f"Scapy ARP 扫描失败（已有 arp -a 结果）: {e}")

        net = ipaddress.ip_network(self.network, strict=False)
        result = [d for d in seen.values() if ipaddress.ip_address(d["ip"]) in net]
        logger.info(f"网络扫描完成，发现 {len(result)} 台设备")
        return result

    def _arp_scan_sync(self) -> list[dict]:
        pkt = Ether(dst="ff:ff:ff:ff:ff:ff") / ARP(pdst=self.network)
        answered, _ = srp(pkt, timeout=3, verbose=0)
        return [{"ip": rcv.psrc, "mac": rcv.hwsrc.upper()} for _, rcv in answered]

    def _ping_sweep_sync(self) -> None:
        """Send parallel pings to all subnet hosts to populate the OS ARP cache."""
        net = ipaddress.ip_network(self.network, strict=False)
        if net.num_addresses > 256:
            return
        if sys.platform == "win32":
            # -n 1: one packet  -w 1000: 1s timeout
            ping_args = lambda ip: ["ping", "-n", "1", "-w", "1000", str(ip)]
        else:
            # -c 1: one packet  -W 1: 1s timeout
            ping_args = lambda ip: ["ping", "-c", "1", "-W", "1", str(ip)]
        procs = [
            subprocess.Popen(ping_args(ip), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            for ip in net.hosts()
        ]
        for p in procs:
            p.wait()

    def _arp_table_scan_sync(self) -> list[dict]:
        """Parse the OS ARP cache via `arp -a`."""
        try:
            out = subprocess.check_output(["arp", "-a"], text=True, timeout=10)
        except Exception as e:
            logger.warning(f"arp -a 失败: {e}")
            return []
        results: list[dict] = []
        # Windows: "  192.168.5.1    2c-6d-c1-9c-e3-7a    动态"
        # Linux:   "? (192.168.5.1) at 2c:6d:c1:9c:e3:7a [ether] on eth0"
        for line in out.splitlines():
            ip_match = re.search(r"(\d{1,3}(?:\.\d{1,3}){3})", line)
            mac_match = re.search(r"([0-9a-fA-F]{2}[-:][0-9a-fA-F]{2}[-:][0-9a-fA-F]{2}[-:][0-9a-fA-F]{2}[-:][0-9a-fA-F]{2}[-:][0-9a-fA-F]{2})", line)
            if not ip_match or not mac_match:
                continue
            mac = mac_match.group(1).replace("-", ":").upper()
            # Skip broadcast/multicast MACs
            if mac in ("FF:FF:FF:FF:FF:FF",) or mac.startswith("01:"):
                continue
            results.append({"ip": ip_match.group(1), "mac": mac})
        return results

    async def lookup_vendor(self, mac: str) -> str:
        if self._mac_lookup is None:
            return "Unknown"
        try:
            return await self._mac_lookup.lookup(mac)
        except Exception:
            return "Unknown"

    async def probe_ports(self, ip: str) -> list[int]:
        loop = asyncio.get_running_loop()
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
