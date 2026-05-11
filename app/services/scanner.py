import asyncio
import ipaddress
import re
import socket
import struct
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from loguru import logger

# Dedicated executor for blocking I/O (hostname resolution + ping).
# 128 workers = 64 semaphore × 2 concurrent blocking ops per device, no queuing.
_IO_EXECUTOR = ThreadPoolExecutor(max_workers=128, thread_name_prefix="scanner_io")

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


def _detect_prefix_length(local_ip: str) -> int:
    """Detect the real prefix length for the interface that holds local_ip."""
    # Method 1: scapy routing table (already a dependency, most reliable)
    if _SCAPY_AVAILABLE:
        try:
            from scapy.all import conf
            # routes: (net_int, mask_int, gw, iface, src_ip, metric)
            for entry in conf.route.routes:
                net_int, mask_int, _gw, _iface, src, _metric = entry
                if src == local_ip and mask_int not in (0xFFFFFFFF, 0x00000000):
                    netmask_str = socket.inet_ntoa(struct.pack(">I", mask_int))
                    return ipaddress.IPv4Network(f"0.0.0.0/{netmask_str}").prefixlen
        except Exception:
            pass

    # Method 2: platform commands
    try:
        if sys.platform == "win32":
            raw = subprocess.check_output(["ipconfig"], timeout=5)
            out = raw.decode("gbk", errors="replace")
            lines = out.splitlines()
            for i, line in enumerate(lines):
                if local_ip in line:
                    # Subnet mask appears near the IP line
                    for near in lines[max(0, i - 3): i + 4]:
                        m = re.search(r"\b(255\.\d+\.\d+\.\d+)\b", near)
                        if m:
                            return ipaddress.IPv4Network(f"0.0.0.0/{m.group(1)}").prefixlen
        else:
            out = subprocess.check_output(["ip", "addr"], text=True, timeout=5)
            for line in out.splitlines():
                m = re.search(rf"\b{re.escape(local_ip)}/(\d+)\b", line)
                if m:
                    return int(m.group(1))
    except Exception:
        pass

    return 24  # safe fallback


def detect_local_network() -> str:
    """Use the default-route interface IP and its actual subnet mask to derive the network."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            local_ip = s.getsockname()[0]
        prefix_len = _detect_prefix_length(local_ip)
        network = ipaddress.ip_network(f"{local_ip}/{prefix_len}", strict=False)
        logger.info(f"自动检测网段: {network} (本机 IP: {local_ip}, 掩码 /{prefix_len})")
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
        seen: dict[str, dict] = {}  # mac -> entry

        logger.info(f"开始网络扫描: {self.network}")

        if _SCAPY_AVAILABLE:
            # Primary path: ARP broadcast — O(3s) regardless of subnet size, no subprocess spam
            try:
                for d in await loop.run_in_executor(None, self._arp_scan_sync):
                    seen[d["mac"]] = d
                logger.info(f"Scapy ARP broadcast 发现 {len(seen)} 台设备")
            except Exception as e:
                logger.warning(f"Scapy ARP 失败，回退 ping sweep: {e}")

        if not seen:
            # Fallback: ping sweep to populate ARP cache, then read it
            await loop.run_in_executor(None, self._ping_sweep_sync)

        # Always supplement from OS ARP cache (catches hosts that replied to ping but not ARP broadcast)
        for d in await loop.run_in_executor(None, self._arp_table_scan_sync):
            seen.setdefault(d["mac"], d)
        logger.debug(f"ARP 缓存补充后共 {len(seen)} 台设备")

        # Local machine never appears in its own ARP table — add it explicitly
        local_entry = await loop.run_in_executor(None, self._get_local_machine_entry)
        if local_entry:
            seen.setdefault(local_entry["mac"], local_entry)

        net = ipaddress.ip_network(self.network, strict=False)
        result = [d for d in seen.values() if ipaddress.ip_address(d["ip"]) in net]
        logger.info(f"网络扫描完成，发现 {len(result)} 台设备")
        return result

    def _arp_scan_sync(self) -> list[dict]:
        pkt = Ether(dst="ff:ff:ff:ff:ff:ff") / ARP(pdst=self.network)
        answered, _ = srp(pkt, timeout=2, verbose=0)
        return [{"ip": rcv.psrc, "mac": rcv.hwsrc.upper()} for _, rcv in answered]

    def _ping_sweep_sync(self) -> None:
        """Ping all subnet hosts to populate the OS ARP cache. Batched for large subnets."""
        net = ipaddress.ip_network(self.network, strict=False)
        hosts = list(net.hosts())
        # Safety cap: skip subnets larger than /21 (>2046 hosts)
        if len(hosts) > 2046:
            logger.warning(f"网段 {net} 超过 2046 个主机，跳过 ping sweep")
            return
        if sys.platform == "win32":
            ping_args = lambda ip: ["ping", "-n", "1", "-w", "500", str(ip)]
        else:
            ping_args = lambda ip: ["ping", "-c", "1", "-W", "1", str(ip)]

        # Batch into groups of 128 to avoid overwhelming the OS with too many processes
        batch_size = 128
        for i in range(0, len(hosts), batch_size):
            batch = hosts[i: i + batch_size]
            procs = [
                subprocess.Popen(ping_args(ip), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                for ip in batch
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
            if mac in ("FF:FF:FF:FF:FF:FF",) or mac.startswith("01:"):
                continue
            results.append({"ip": ip_match.group(1), "mac": mac})
        return results

    def _get_local_machine_entry(self) -> dict | None:
        """Return this machine's own IP+MAC — it never appears in its own ARP table."""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("8.8.8.8", 80))
                local_ip = s.getsockname()[0]
            mac = self._get_local_mac(local_ip)
            if mac:
                return {"ip": local_ip, "mac": mac, "is_local": True}
        except Exception:
            pass
        return None

    def _get_local_mac(self, local_ip: str) -> str | None:
        """Get the MAC of the interface that holds local_ip."""
        if _SCAPY_AVAILABLE:
            try:
                from scapy.all import conf, get_if_hwaddr
                for iface_name, iface in conf.ifaces.items():
                    if getattr(iface, "ip", None) == local_ip:
                        mac = get_if_hwaddr(iface_name)
                        if mac and mac != "00:00:00:00:00:00":
                            return mac.upper()
            except Exception:
                pass

        try:
            if sys.platform == "win32":
                # ipconfig /all pairs IP and MAC in the same interface block
                raw = subprocess.check_output(["ipconfig", "/all"], timeout=5)
                out = raw.decode("gbk", errors="replace")
                blocks = re.split(r"\n(?=\S)", out)  # split on non-indented lines
                for block in blocks:
                    if local_ip in block:
                        m = re.search(r"([0-9A-Fa-f]{2}[-][0-9A-Fa-f]{2}[-][0-9A-Fa-f]{2}[-][0-9A-Fa-f]{2}[-][0-9A-Fa-f]{2}[-][0-9A-Fa-f]{2})", block)
                        if m:
                            return m.group(1).replace("-", ":").upper()
            else:
                out = subprocess.check_output(["ip", "link"], text=True, timeout=5)
                # Pair link/ether entries with interface names, then match via 'ip addr'
                addr_out = subprocess.check_output(["ip", "addr"], text=True, timeout=5)
                iface_match = re.search(rf"(\w+).*\n.*{re.escape(local_ip)}", addr_out)
                if iface_match:
                    iface = iface_match.group(1)
                    mac_match = re.search(rf"{re.escape(iface)}.*\n.*link/ether\s+([0-9a-f:]+)", out)
                    if mac_match:
                        return mac_match.group(1).upper()
        except Exception:
            pass
        return None

    async def resolve_hostname(self, ip: str) -> str | None:
        return await asyncio.get_running_loop().run_in_executor(_IO_EXECUTOR, self._resolve_hostname_sync, ip)

    def _resolve_hostname_sync(self, ip: str) -> str | None:
        try:
            old_timeout = socket.getdefaulttimeout()
            socket.setdefaulttimeout(1.0)
            try:
                hostname, _, _ = socket.gethostbyaddr(ip)
                return hostname
            finally:
                socket.setdefaulttimeout(old_timeout)
        except Exception:
            return None

    async def measure_latency(self, ip: str) -> float | None:
        return await asyncio.get_running_loop().run_in_executor(_IO_EXECUTOR, self._measure_latency_sync, ip)

    def _measure_latency_sync(self, ip: str) -> float | None:
        try:
            if sys.platform == "win32":
                cmd = ["ping", "-n", "1", "-w", "300", str(ip)]
                pattern = r"(?:平均|Average)\s*[=<]\s*(\d+)\s*ms"
            else:
                cmd = ["ping", "-c", "1", "-W", "1", str(ip)]
                pattern = r"time=(\d+\.?\d*) ms"
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=1)
            m = re.search(pattern, result.stdout, re.IGNORECASE)
            if m:
                return float(m.group(1))
        except Exception:
            pass
        return None

    async def lookup_vendor(self, mac: str) -> str:
        if self._mac_lookup is None:
            return "Unknown"
        try:
            return await self._mac_lookup.lookup(mac)
        except Exception:
            return "Unknown"

    # Camera-relevant ports: RTSP(554), ONVIF-standard(2020), Hikvision/Dahua HTTP(80,8080),
    # Dahua ONVIF alt(8000), HTTPS(443/8443)
    _PROBE_PORTS = [554, 2020, 8000, 8080, 8443, 8554, 5000, 80, 443]

    async def probe_ports_async(self, ip: str, timeout: float = 0.8) -> list[int]:
        """Fast async socket-based port probe. No subprocess overhead."""
        async def _check(port: int) -> int | None:
            try:
                _, writer = await asyncio.wait_for(
                    asyncio.open_connection(ip, port), timeout=timeout
                )
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass
                return port
            except Exception:
                return None

        results = await asyncio.gather(*[_check(p) for p in self._PROBE_PORTS])
        return [p for p in results if p is not None]

    async def probe_ports(self, ip: str) -> list[int]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._probe_ports_sync, ip)

    def _probe_ports_sync(self, ip: str) -> list[int]:
        try:
            import nmap
            nm = nmap.PortScanner()
            nm.scan(ip, "80,443,554,2020,8000,8080,8443", arguments="-T4 --open")
            ports: list[int] = []
            if ip in nm.all_hosts():
                for proto in nm[ip].all_protocols():
                    ports.extend(nm[ip][proto].keys())
            return ports
        except Exception as e:
            logger.debug(f"nmap 探测失败 {ip}: {e}")
            return []

    @staticmethod
    def guess_device_type(vendor: str, open_ports: list[int], hostname: str | None = None) -> str:
        """Infer device type from vendor OUI name, open ports, and hostname."""
        # --- Port-based detection (highest priority) ---
        # 554=RTSP, 2020=ONVIF-standard, 8000=Dahua/Hikvision ONVIF alt
        # 8080=Hikvision/Dahua HTTP, 8443=HTTPS alt, 8554=RTSP alt, 5000=ONVIF
        if 554 in open_ports or 2020 in open_ports or 8000 in open_ports or 8080 in open_ports or 8443 in open_ports or 8554 in open_ports or 5000 in open_ports:
            return "camera"
        if 631 in open_ports or 9100 in open_ports or 515 in open_ports:
            return "printer"

        v = vendor.lower()
        h = (hostname or "").lower()

        # --- Hostname-based heuristics ---
        if h:
            if any(kw in h for kw in ("iphone", "ipad", "android", "galaxy", "redmi", "pixel")):
                return "phone"
            if any(kw in h for kw in ("macbook", "imac", "desktop", "laptop", "pc-", "workstation")):
                return "computer"
            if any(kw in h for kw in ("printer", "canon", "epson", "brother")):
                return "printer"
            if any(kw in h for kw in ("-tv", "smarttv", "lgwebos", "tizen", "roku", "fire-tv", "appletv", "apple-tv")):
                return "tv"
            if any(kw in h for kw in ("echo", "home-mini", "nest-", "homepod", "xiaoai")):
                return "smart_speaker"
            if any(kw in h for kw in ("switch", "playstation", "xbox", "ps5", "ps4")):
                return "game_console"
            if any(kw in h for kw in ("ipad", "tab-", "tablet", "galaxy-tab")):
                return "tablet"
            if any(kw in h for kw in ("cam", "ipc", "nvr", "dvr")):
                return "camera"

        # --- Vendor-based classification ---
        # Routers / Network equipment
        if any(kw in v for kw in (
            "tp-link", "tplink", "tp link", "netgear", "d-link", "dlink",
            "cisco", "linksys", "ubiquiti", "mikrotik", "zyxel", "tenda",
            "ruijie", "h3c", "huawei technologies", "aruba", "juniper",
            "netcore", "mercury", "fast(迅捷)", "fast ", "comfast", "wavlink",
            "eero", "synology", "qnap", "buffalo",
        )):
            # Distinguish NAS from routers
            if any(kw in v for kw in ("synology", "qnap", "buffalo")):
                return "nas"
            # TP-Link + camera hostname keywords → camera
            if any(kw in v for kw in ("tp-link", "tplink", "tp link")) and hostname:
                h = hostname.lower()
                if any(kw in h for kw in ("cam", "ipc", "nvr", "dvr", "camera")):
                    return "camera"
            return "router"

        # Phones / Tablets
        if any(kw in v for kw in (
            "apple", "samsung", "xiaomi", "huawei", "honor", "oppo", "vivo",
            "oneplus", "realme", "motorola", "nokia", "sony mobile",
            "google", "zte", "meizu", "transsion", "tecno", "infinix",
            "nothing", "fairphone",
        )):
            return "phone"

        # Computers
        if any(kw in v for kw in (
            "intel", "realtek", "dell", "lenovo", "hewlett", "hp inc",
            "acer", "msi", "gigabyte", "asustek", "microsoft",
            "razer", "framework", "system76", "mini pc",
            "vmware", "parallels", "virtualbox",
        )):
            return "computer"

        # Smart TVs / Streaming devices
        if any(kw in v for kw in (
            "lg electronics", "tcl", "hisense", "skyworth", "changhong",
            "konka", "haier", "sharp", "philips", "panasonic",
            "roku", "amazon technologies", "chromecast",
            "vizio", "toshiba", "funai",
        )):
            return "tv"

        # Smart speakers / Voice assistants
        if any(kw in v for kw in (
            "sonos", "harman", "bose", "bang & olufsen", "amazon.com",
            "google llc", "apple inc", "baidu", "alibaba",
        )):
            # Apple/Google/Amazon are ambiguous; use hostname to disambiguate
            if any(kw in h for kw in ("echo", "home", "nest", "homepod", "xiaoai", "tmall")):
                return "smart_speaker"
            # For Apple without hostname clue, default to phone
            if "apple" in v:
                return "phone"
            return "smart_speaker"

        # Printers / Scanners
        if any(kw in v for kw in (
            "canon", "epson", "brother", "ricoh", "xerox", "kyocera",
            "lexmark", "konica", "sharp manufacturing",
        )):
            return "printer"

        # Cameras / Security
        if any(kw in v for kw in (
            "hikvision", "dahua", "axis", "reolink", "amcrest",
            "wyze", "ring", "arlo", "eufy", "imou", "uniview",
            "tiandy", "kedacom", "sunell", "yushi",
        )):
            return "camera"

        # IoT / Smart home devices
        if any(kw in v for kw in (
            "espressif", "tuya", "shenzhen", "hangzhou", "yeelight",
            "aqara", "broadlink", "orvibo", "sonoff", "tasmota",
            "switchbot", "ikea of sweden", "signify", "philips hue",
            "lifx", "wemo", "meross", "gosund", "zigbee", "smartthings",
            "nest", "ecobee", "honeywell", "midea", "gree", "aux",
            "roborock", "dreame", "ecovacs", "irobot", "tineco",
        )):
            return "iot"

        # Game consoles
        if any(kw in v for kw in (
            "nintendo", "sony interactive", "microsoft xbox", "valve",
            "steam",
        )):
            return "game_console"

        # Wearables
        if any(kw in v for kw in (
            "fitbit", "garmin", "amazfit", "zepp", "whoop",
        )):
            return "wearable"

        return "unknown"
