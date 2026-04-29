import asyncio
import json
import math
from datetime import datetime
from fastapi import APIRouter, HTTPException, status, BackgroundTasks, Query
from sqlalchemy import select, func
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from app.deps import DBDep, CurrentUser
from app.database import AsyncSessionLocal
from app.models.device import Device
from app.models.device_online_log import DeviceOnlineLog
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
    background_tasks.add_task(_run_scan, settings.network_range)
    return {"message": "扫描已启动，结果通过 WebSocket 推送"}


async def _enrich_device(scanner: Scanner, d: dict) -> dict:
    """Concurrently resolve vendor/hostname/latency for one device."""
    vendor, hostname, latency = await asyncio.gather(
        scanner.lookup_vendor(d["mac"]),
        scanner.resolve_hostname(d["ip"]),
        scanner.measure_latency(d["ip"]),
    )
    return {
        "mac": d["mac"], "ip": d["ip"],
        "vendor": vendor or "Unknown",
        "hostname": hostname,
        "latency": latency,
        # Infer type from vendor alone — avoids slow nmap during discovery
        "device_type": scanner.guess_device_type(vendor or "", []),
    }


def _find_unknown_devices(
    enriched: list[dict],
    original_last_seen: dict[str, "datetime | None"],
    bound_macs: set[str],
    now: datetime,
    staleness_hours: int = 24,
) -> list[dict]:
    """Return devices not bound to any member that are new or stale (not seen recently).

    original_last_seen: snapshot of last_seen values taken BEFORE the DB update loop,
    keyed by MAC. MACs absent from this dict are newly discovered devices.
    """
    result = []
    for data in enriched:
        mac = data["mac"]
        if mac in bound_macs:
            continue
        is_new = mac not in original_last_seen
        last_seen = original_last_seen.get(mac)
        is_stale = (
            not is_new
            and last_seen is not None
            and (now - last_seen).total_seconds() > staleness_hours * 3600
        )
        if is_new or is_stale:
            result.append(data)
    return result


async def _log_scan_result(
    db: AsyncSession,
    enriched: list[dict],
    bucket_hour: datetime,
) -> None:
    """Upsert per-device presence into DeviceOnlineLog for the given hour bucket."""
    online_macs = {d["mac"] for d in enriched}

    all_result = await db.execute(select(Device.mac, Device.device_type))
    all_devices = all_result.all()
    if not all_devices:
        return

    rows = [
        {
            "mac": d.mac,
            "bucket_hour": bucket_hour,
            "device_type": d.device_type or "unknown",
            "online_count": 1 if d.mac in online_macs else 0,
            "scan_count": 1,
        }
        for d in all_devices
    ]

    stmt = sqlite_insert(DeviceOnlineLog).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["mac", "bucket_hour"],
        set_={
            "online_count": DeviceOnlineLog.online_count + stmt.excluded.online_count,
            "scan_count": DeviceOnlineLog.scan_count + 1,
        },
    )
    await db.execute(stmt)
    await db.commit()


async def _run_scan(network_range: str):
    loop = asyncio.get_running_loop()
    scanner = await loop.run_in_executor(None, Scanner, network_range)
    await ws_manager.broadcast("scan_started", {})
    try:
        devices = await scanner.arp_scan()
        results = {"found": len(devices), "new": 0, "offline": 0}

        # Enrich all devices concurrently (cap at 64, matched to _IO_EXECUTOR workers / 2)
        sem = asyncio.Semaphore(64)

        async def enrich_with_sem(d: dict) -> dict:
            async with sem:
                return await _enrich_device(scanner, d)

        enriched = await asyncio.gather(*[enrich_with_sem(d) for d in devices])

        async with AsyncSessionLocal() as db:
            macs = [d["mac"] for d in enriched]
            existing_rows = (await db.execute(
                select(Device).where(Device.mac.in_(macs))
            )).scalars().all()
            existing_map = {d.mac: d for d in existing_rows}
            # Snapshot last_seen before the update loop so stale detection sees original values
            original_last_seen: dict[str, datetime | None] = {
                mac: dev.last_seen for mac, dev in existing_map.items()
            }

            now = datetime.now()
            for data in enriched:
                existing = existing_map.get(data["mac"])
                if existing:
                    existing.ip = data["ip"]
                    existing.vendor = data["vendor"]
                    existing.hostname = data["hostname"]
                    existing.response_time_ms = data["latency"]
                    existing.is_online = True
                    existing.last_seen = now
                else:
                    results["new"] += 1
                    db.add(Device(
                        mac=data["mac"], ip=data["ip"],
                        vendor=data["vendor"], hostname=data["hostname"],
                        response_time_ms=data["latency"],
                        device_type=data["device_type"],
                        is_online=True, last_seen=now,
                    ))
            await db.commit()

            # A2: unknown device detection
            try:
                bound_result = await db.execute(select(MemberDevice.mac))
                bound_macs = {row[0] for row in bound_result.all()}
                unknowns = _find_unknown_devices(enriched, original_last_seen, bound_macs, now)
                for u in unknowns:
                    await ws_manager.broadcast("unknown_device_detected", {
                        "mac": u["mac"],
                        "ip": u["ip"],
                        "vendor": u.get("vendor"),
                        "hostname": u.get("hostname"),
                        "first_seen": now.isoformat(),
                    })
                if unknowns:
                    logger.info(f"[A2] 发现 {len(unknowns)} 台陌生设备")
            except Exception as e:
                logger.warning(f"[A2] 陌生设备检测失败，不影响扫描结果: {e}")

            # Analytics: log hourly device presence
            try:
                bucket_hour = now.replace(minute=0, second=0, microsecond=0)
                await _log_scan_result(db, enriched, bucket_hour)
            except Exception as e:
                logger.warning(f"[Analytics] 设备在线日志写入失败，不影响扫描结果: {e}")

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
