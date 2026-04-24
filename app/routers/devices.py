import math
from datetime import datetime
from fastapi import APIRouter, HTTPException, status, BackgroundTasks, Query
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from app.deps import DBDep, CurrentUser
from app.models.device import Device
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
    device_type: str | None = Query(None, alias="type"),
    online: bool | None = None,
):
    q = select(Device)
    if device_type:
        q = q.where(Device.device_type == device_type)
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
async def trigger_scan(background_tasks: BackgroundTasks, db: DBDep, _: CurrentUser):
    settings = get_settings()
    scanner = Scanner(settings.network_range)
    background_tasks.add_task(_run_scan, scanner, db)
    return {"message": "扫描已启动，结果通过 WebSocket 推送"}


async def _run_scan(scanner: Scanner, db):
    await ws_manager.broadcast("scan_started", {})
    try:
        devices = await scanner.arp_scan()
        results = {"found": len(devices), "new": 0, "offline": 0}

        for d in devices:
            vendor = await scanner.lookup_vendor(d["mac"])
            existing = await db.execute(select(Device).where(Device.mac == d["mac"]))
            existing = existing.scalar_one_or_none()
            if existing:
                existing.ip = d["ip"]
                existing.vendor = vendor
                existing.is_online = True
                existing.last_seen = datetime.now()
            else:
                results["new"] += 1
                ports = await scanner.probe_ports(d["ip"])
                device_type = scanner.guess_device_type(vendor, ports)
                db.add(Device(
                    mac=d["mac"], ip=d["ip"], vendor=vendor,
                    device_type=device_type, is_online=True,
                    last_seen=datetime.now(),
                ))

        await db.commit()
        await ws_manager.broadcast("scan_completed", results)
        logger.info(f"扫描完成: {results}")
    except Exception as e:
        logger.error(f"扫描失败: {e}")
        await ws_manager.broadcast("scan_completed", {"error": str(e)})


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
