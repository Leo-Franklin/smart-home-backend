import math
from datetime import datetime, timedelta
from fastapi import APIRouter, HTTPException, status, BackgroundTasks, Query
from sqlalchemy import select, func, case, Integer
from sqlalchemy.exc import IntegrityError
from app.deps import DBDep, CurrentUser
from app.models.device import Device
from app.models.device_online_log import DeviceOnlineLog
from app.models.member import Member, MemberDevice
from app.schemas.device import DeviceOut, DeviceUpdate
from app.schemas import PagedResponse
from app.domain.services.scanner import _run_scan
from app.config import get_settings

router = APIRouter(prefix="/devices", tags=["devices"])


@router.get("", response_model=PagedResponse[DeviceOut])
async def list_devices(
    db: DBDep,
    _: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    device_types: list[str] = Query([], alias="device_type"),
    online: bool | None = None,
    search: str | None = Query(None),
):
    flat_types = [t for raw in device_types for t in raw.split(",") if t]
    q = select(Device)
    if flat_types:
        q = q.where(Device.device_type.in_(flat_types))
    if online is not None:
        q = q.where(Device.is_online == online)
    if search:
        q = q.where(
            (Device.ip.contains(search)) |
            (Device.mac.ilike(f"%{search}%")) |
            (Device.alias.ilike(f"%{search}%"))
        )

    total_result = await db.execute(select(func.count()).select_from(q.subquery()))
    total = total_result.scalar_one()

    q = q.order_by(Device.is_online.desc()).offset((page - 1) * page_size).limit(page_size)
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


_HEATMAP_RANGE_DAYS = {"7d": 7, "30d": 30, "90d": 90}


@router.get("/heatmap")
async def device_heatmap(
    db: DBDep,
    _: CurrentUser,
    range_str: str = Query("7d", alias="range"),
    device_type: str = Query(""),
):
    days = _HEATMAP_RANGE_DAYS.get(range_str, 7)
    since = datetime.now() - timedelta(days=days)

    q = (
        select(
            func.cast(func.strftime("%w", DeviceOnlineLog.bucket_hour), Integer).label("day"),
            func.cast(func.strftime("%H", DeviceOnlineLog.bucket_hour), Integer).label("hour"),
            func.sum(
                case((DeviceOnlineLog.online_count > 0, 1), else_=0)
            ).label("value"),
        )
        .where(DeviceOnlineLog.bucket_hour >= since)
        .group_by(
            func.strftime("%w", DeviceOnlineLog.bucket_hour),
            func.strftime("%H", DeviceOnlineLog.bucket_hour),
        )
    )

    if device_type:
        types = [t.strip() for t in device_type.split(",") if t.strip()]
        if types:
            q = q.where(DeviceOnlineLog.device_type.in_(types))

    result = await db.execute(q)
    return {
        "cells": [
            {"day": row.day, "hour": row.hour, "value": row.value}
            for row in result
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