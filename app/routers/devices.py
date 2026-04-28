import asyncio
import json
import math
from datetime import datetime
from fastapi import APIRouter, HTTPException, status, BackgroundTasks, Query
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from app.deps import DBDep, CurrentUser
from app.database import AsyncSessionLocal
from app.models.device import Device
from app.models.member import Member, MemberDevice
from app.schemas.device import DeviceOut, DeviceUpdate
from app.schemas import PagedResponse
from app.services.scanner import Scanner
from app.services.ws_manager import ws_manager
from app.config import get_settings
from loguru import logger

router = APIRouter(prefix="/devices", tags=["devices"])


@router.get("", response_model=PagedResponse[DeviceOut])
async def list_devices(
    db: DBDep,
    _: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    device_types: list[str] = Query([], alias="device_type"),
    online: bool | None = None,
):
    flat_types = [t for raw in device_types for t in raw.split(",") if t]
    q = select(Device)
    if flat_types:
        q = q.where(Device.device_type.in_(flat_types))
    if online is not None:
        q = q.where(Device.is_online == online)

    total_result = await db.execute(select(func.count()).select_from(q.subquery()))
    total = total_result.scalar_one()

    q = q.offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(q)
    items = result.scalars().all()

    return PagedResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        pages=math.ceil(total / page_size) if total else 0,
    )


@router.post("/scan", status_code=status.HTTP_202_ACCEPTED, tags=["devices"])
async def trigger_scan(background_tasks: BackgroundTasks, _: CurrentUser):
    settings = get_settings()
    scanner = Scanner(settings.network_range)
    background_tasks.add_task(_run_scan, scanner)
    return {"message": "扫描已启动，结果通过 WebSocket 推送"}


async def _run_scan(scanner: Scanner):
    await ws_manager.broadcast("scan_started", {})
    try:
        devices = await scanner.arp_scan()
        results = {"found": len(devices), "new": 0, "offline": 0}

        async with AsyncSessionLocal() as db:
            for d in devices:
                vendor, hostname, latency = await asyncio.gather(
                    scanner.lookup_vendor(d["mac"]),
                    scanner.resolve_hostname(d["ip"]),
                    scanner.measure_latency(d["ip"]),
                )
                existing = (await db.execute(select(Device).where(Device.mac == d["mac"]))).scalar_one_or_none()
                if existing:
                    existing.ip = d["ip"]
                    existing.vendor = vendor
                    existing.hostname = hostname
                    existing.response_time_ms = latency
                    existing.is_online = True
                    existing.last_seen = datetime.now()
                else:
                    results["new"] += 1
                    ports = await scanner.probe_ports(d["ip"])
                    device_type = scanner.guess_device_type(vendor, ports)
                    db.add(Device(
                        mac=d["mac"], ip=d["ip"], vendor=vendor,
                        hostname=hostname, response_time_ms=latency,
                        open_ports=json.dumps(ports) if ports else None,
                        device_type=device_type, is_online=True,
                        last_seen=datetime.now(),
                    ))

            await db.commit()
        await ws_manager.broadcast("scan_completed", results)
        logger.info(f"扫描完成: {results}")
    except Exception as e:
        logger.error(f"扫描失败: {e}")
        await ws_manager.broadcast("scan_completed", {"error": str(e)})


@router.get("/types", response_model=list[str])
async def list_device_types(db: DBDep, _: CurrentUser):
    result = await db.execute(select(Device.device_type).distinct())
    return result.scalars().all()


@router.get("/topology")
async def get_topology(db: DBDep, _: CurrentUser):
    devices_result = await db.execute(select(Device))
    devices = devices_result.scalars().all()

    bindings_result = await db.execute(
        select(MemberDevice, Member).join(Member, Member.id == MemberDevice.member_id)
    )
    mac_owners: dict[str, list] = {}
    for md, member in bindings_result.all():
        mac_owners.setdefault(md.mac, []).append({
            "id": member.id,
            "name": member.name,
            "avatar_url": member.avatar_url,
            "is_home": member.is_home,
        })

    return {
        "nodes": [
            {
                "mac": d.mac,
                "ip": d.ip,
                "hostname": d.hostname,
                "vendor": d.vendor,
                "device_type": d.device_type or "unknown",
                "alias": d.alias,
                "response_time_ms": d.response_time_ms,
                "is_online": d.is_online,
                "last_seen": d.last_seen.isoformat() if d.last_seen else None,
                "owners": mac_owners.get(d.mac, []),
            }
            for d in devices
        ]
    }


@router.get("/{mac}", response_model=DeviceOut)
async def get_device(mac: str, db: DBDep, _: CurrentUser):
    result = await db.execute(select(Device).where(Device.mac == mac))
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="设备不存在")
    return device


@router.patch("/{mac}", response_model=DeviceOut)
async def update_device(mac: str, body: DeviceUpdate, db: DBDep, _: CurrentUser):
    result = await db.execute(select(Device).where(Device.mac == mac))
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="设备不存在")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(device, field, value)
    await db.commit()
    await db.refresh(device)
    return device


@router.delete("/{mac}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_device(mac: str, db: DBDep, _: CurrentUser):
    result = await db.execute(select(Device).where(Device.mac == mac))
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="设备不存在")
    await db.delete(device)
    await db.commit()
