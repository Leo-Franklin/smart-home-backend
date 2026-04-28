import asyncio
import ipaddress
import sys
from datetime import datetime
from urllib.parse import urlparse
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
        for net in _PRIVATE_NETWORKS:
            if addr in net:
                raise ValueError(f"Webhook URL 不能指向内网地址: {hostname}")
    except ValueError as e:
        if "内网" in str(e) or "https" in str(e) or "无效" in str(e):
            raise
        # hostname is a domain name, not an IP — allow it (DNS resolution not done here)


class PresenceService:
    def __init__(self, poll_interval: int = 30):
        self._poll_interval = poll_interval
        self._task: asyncio.Task | None = None
        self._initialized = False

    async def start(self):
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
                async with AsyncSessionLocal() as session:
                    await self._check_all_members(session)
                self._initialized = True
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"PresenceService 轮询异常: {e}")
            await asyncio.sleep(self._poll_interval)

    async def _check_all_members(self, session):
        result = await session.execute(select(Member))
        members = result.scalars().all()
        tasks = [self._check_member(session, m) for m in members]
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _check_member(self, session, member: Member):
        try:
            is_home, triggered_mac = await self._is_member_home(session, member)
            if not self._initialized:
                # 首轮仅建立基准，不触发事件
                member.is_home = is_home
                await session.commit()
                return
            if is_home == member.is_home:
                return
            await self._fire_event(session, member, is_home, triggered_mac)
        except Exception as e:
            logger.warning(f"检测成员 {member.name} 失败: {e}")

    async def _is_member_home(self, session, member: Member) -> tuple[bool, str | None]:
        result = await session.execute(
            select(MemberDevice).where(MemberDevice.member_id == member.id)
        )
        bound_devices = result.scalars().all()
        if not bound_devices:
            return False, None

        macs = [d.mac for d in bound_devices]
        device_result = await session.execute(
            select(Device).where(Device.mac.in_(macs), Device.ip.isnot(None))
        )
        devices = device_result.scalars().all()

        for device in devices:
            if await self._ping_ip(device.ip):
                # Update device online status on successful ping
                device.is_online = True
                device.last_seen = datetime.now()
                return True, device.mac

        return False, None

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

    async def _send_webhook(self, url: str, event: str, member: Member, triggered_mac: str | None, ts: datetime):
        try:
            _validate_webhook_url(url)
        except ValueError as e:
            logger.warning(f"Webhook URL 不合法，跳过: {e}")
            return
        try:
            import httpx
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
