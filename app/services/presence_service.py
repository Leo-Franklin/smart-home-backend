import asyncio
import ipaddress
import sys
from datetime import datetime
from urllib.parse import urlparse
import httpx
from loguru import logger
from sqlalchemy import select
from app.database import AsyncSessionLocal
from app.models.member import Member, MemberDevice, PresenceLog
from app.models.device import Device
from app.services.ws_manager import ws_manager


_PRIVATE_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
]


def _validate_webhook_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError(f"Webhook URL 必须使用 https 协议: {url}")
    hostname = parsed.hostname
    if not hostname:
        raise ValueError(f"Webhook URL 无效: {url}")
    try:
        addr = ipaddress.ip_address(hostname)
    except ValueError:
        # hostname is a domain name, not an IP literal — allow it
        # Note: DNS rebinding attacks are a known limitation; IP literals only are checked here
        return
    for net in _PRIVATE_NETWORKS:
        if addr in net:
            raise ValueError(f"Webhook URL 不能指向内网地址: {hostname}")


class PresenceService:
    def __init__(self, poll_interval: int = 30):
        self._poll_interval = poll_interval
        self._task: asyncio.Task | None = None
        self._initialized = False
        self._auto_start_cb = None   # async (camera_mac: str) -> None
        self._auto_stop_cb = None    # async (camera_mac: str) -> None

    async def start(self, auto_start_cb=None, auto_stop_cb=None):
        self._auto_start_cb = auto_start_cb
        self._auto_stop_cb = auto_stop_cb
        self._task = asyncio.create_task(self._loop())
        logger.info(f"PresenceService 已启动，轮询间隔 {self._poll_interval}s")

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("PresenceService 已停止")

    async def _loop(self):
        while True:
            try:
                await self._check_all_members()
                self._initialized = True
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"PresenceService 轮询异常: {e}")
            await asyncio.sleep(self._poll_interval)

    async def _check_all_members(self):
        # Short-lived read session — released before spawning concurrent ping tasks
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(Member))
            members = result.scalars().all()
            snapshots = [{"id": m.id, "name": m.name, "is_home": m.is_home} for m in members]
        await asyncio.gather(
            *[self._check_member(snap) for snap in snapshots],
            return_exceptions=True,
        )

    async def _check_member(self, snap: dict):
        try:
            member_id = snap["id"]

            # Read: fetch device IPs — session released before any network I/O
            async with AsyncSessionLocal() as session:
                bound = (await session.execute(
                    select(MemberDevice).where(MemberDevice.member_id == member_id)
                )).scalars().all()
                macs = [d.mac for d in bound]
                device_data: list[tuple[str, str]] = []
                if macs:
                    devices = (await session.execute(
                        select(Device).where(Device.mac.in_(macs), Device.ip.isnot(None))
                    )).scalars().all()
                    device_data = [(d.ip, d.mac) for d in devices]

            # Ping: no DB connection held during network I/O
            is_home, triggered_mac = False, None
            for ip, mac in device_data:
                if await self._ping_ip(ip):
                    is_home, triggered_mac = True, mac
                    break

            # Write: update results in a fresh session
            async with AsyncSessionLocal() as session:
                if triggered_mac:
                    dev = (await session.execute(
                        select(Device).where(Device.mac == triggered_mac)
                    )).scalar_one_or_none()
                    if dev:
                        dev.is_online = True
                        dev.last_seen = datetime.now()

                member = (await session.execute(
                    select(Member).where(Member.id == member_id)
                )).scalar_one_or_none()
                if not member:
                    await session.commit()
                    return
                if not self._initialized:
                    member.is_home = is_home
                    await session.commit()
                    return
                if is_home == snap["is_home"]:
                    await session.commit()
                    return
                await self._fire_event(session, member, is_home, triggered_mac)

        except Exception as e:
            logger.warning(f"检测成员 {snap.get('name', snap.get('id'))} 失败: {e}")

    async def _fire_event(self, session, member: Member, is_home: bool, triggered_mac: str | None):
        event = "arrived" if is_home else "left"
        now = datetime.now()

        member.is_home = is_home
        if is_home:
            member.last_arrived_at = now
        else:
            member.last_left_at = now

        session.add(PresenceLog(
            member_id=member.id,
            event=event,
            triggered_by_mac=triggered_mac,
            occurred_at=now,
        ))
        await session.commit()

        payload = {
            "member_id": member.id,
            "name": member.name,
            "triggered_by_mac": triggered_mac,
            "timestamp": now.isoformat(),
        }
        ws_event = f"member_{event}"
        await ws_manager.broadcast(ws_event, payload)
        logger.info(f"[Presence] {member.name} {event}  mac={triggered_mac}")

        if member.webhook_url:
            await self._send_webhook(member.webhook_url, ws_event, member, triggered_mac, now)

        # A1: trigger auto recordings
        auto_cams = member.auto_record_cameras if isinstance(member.auto_record_cameras, list) else []
        if auto_cams:
            if is_home and self._auto_start_cb:
                for cam_mac in auto_cams:
                    asyncio.create_task(self._auto_start_cb(cam_mac))
            elif not is_home and self._auto_stop_cb:
                await self._trigger_auto_stop(session, member, auto_cams)

    async def _trigger_auto_stop(self, session, member, camera_macs: list[str]):
        from app.models.member import Member as MemberModel
        other_home = (await session.execute(
            select(MemberModel).where(MemberModel.is_home == True, MemberModel.id != member.id)
        )).scalars().all()

        for cam_mac in camera_macs:
            other_wants = any(
                isinstance(m.auto_record_cameras, list) and cam_mac in m.auto_record_cameras
                for m in other_home
            )
            if not other_wants and self._auto_stop_cb:
                asyncio.create_task(self._auto_stop_cb(cam_mac))

    async def _ping_ip(self, ip: str) -> bool:
        try:
            if sys.platform == "win32":
                cmd = ["ping", "-n", "1", "-w", "1000", ip]
            else:
                cmd = ["ping", "-c", "1", "-W", "1", ip]
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=3)
            return proc.returncode == 0
        except (asyncio.TimeoutError, Exception):
            return False

    async def _send_webhook(self, url: str, event: str, member: Member, triggered_mac: str | None, ts: datetime):
        try:
            _validate_webhook_url(url)
        except ValueError as e:
            logger.warning(f"Webhook URL 不合法，跳过: {e}")
            return
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                await client.post(url, json={
                    "event": event,
                    "member": {"id": member.id, "name": member.name},
                    "triggered_by_mac": triggered_mac,
                    "timestamp": ts.isoformat(),
                })
        except Exception as e:
            logger.warning(f"Webhook 发送失败 ({url}): {e}")


presence_service = PresenceService()
