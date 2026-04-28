import math
from fastapi import APIRouter, HTTPException, status, Query
from sqlalchemy import select, func
from app.deps import DBDep, CurrentUser
from app.models.member import Member, MemberDevice, PresenceLog
from app.models.device import Device
from app.schemas.member import (
    MemberCreate, MemberUpdate, MemberOut,
    MemberDeviceCreate, MemberDeviceOut,
    PresenceLogOut,
)
from app.schemas.device import DeviceOut
from app.schemas import PagedResponse

router = APIRouter(prefix="/members", tags=["members"])


def _not_found():
    raise HTTPException(status_code=404, detail="成员不存在")


@router.get("", response_model=list[MemberOut])
async def list_members(db: DBDep, _: CurrentUser):
    result = await db.execute(select(Member).order_by(Member.id))
    return result.scalars().all()


@router.post("", response_model=MemberOut, status_code=status.HTTP_201_CREATED)
async def create_member(body: MemberCreate, db: DBDep, _: CurrentUser):
    member = Member(**body.model_dump())
    db.add(member)
    await db.commit()
    await db.refresh(member)
    return member


@router.get("/{member_id}", response_model=MemberOut)
async def get_member(member_id: int, db: DBDep, _: CurrentUser):
    member = (await db.execute(select(Member).where(Member.id == member_id))).scalar_one_or_none()
    if not member:
        _not_found()
    return member


@router.patch("/{member_id}", response_model=MemberOut)
async def update_member(member_id: int, body: MemberUpdate, db: DBDep, _: CurrentUser):
    member = (await db.execute(select(Member).where(Member.id == member_id))).scalar_one_or_none()
    if not member:
        _not_found()
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(member, field, value)
    await db.commit()
    await db.refresh(member)
    return member


@router.delete("/{member_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_member(member_id: int, db: DBDep, _: CurrentUser):
    member = (await db.execute(select(Member).where(Member.id == member_id))).scalar_one_or_none()
    if not member:
        _not_found()
    await db.delete(member)
    await db.commit()


@router.get("/{member_id}/devices", response_model=list[MemberDeviceOut])
async def list_member_devices(member_id: int, db: DBDep, _: CurrentUser):
    member = (await db.execute(select(Member).where(Member.id == member_id))).scalar_one_or_none()
    if not member:
        _not_found()
    result = await db.execute(select(MemberDevice).where(MemberDevice.member_id == member_id))
    bound = result.scalars().all()

    macs = [d.mac for d in bound]
    device_map: dict[str, Device] = {}
    if macs:
        dev_result = await db.execute(select(Device).where(Device.mac.in_(macs)))
        for dev in dev_result.scalars().all():
            device_map[dev.mac] = dev

    return [
        MemberDeviceOut(
            id=d.id,
            member_id=d.member_id,
            mac=d.mac,
            label=d.label,
            device_info=DeviceOut.model_validate(device_map[d.mac]) if d.mac in device_map else None,
        )
        for d in bound
    ]


@router.post("/{member_id}/devices", response_model=MemberDeviceOut, status_code=status.HTTP_201_CREATED)
async def bind_device(member_id: int, body: MemberDeviceCreate, db: DBDep, _: CurrentUser):
    member = (await db.execute(select(Member).where(Member.id == member_id))).scalar_one_or_none()
    if not member:
        _not_found()

    existing = (await db.execute(
        select(MemberDevice).where(MemberDevice.member_id == member_id, MemberDevice.mac == body.mac)
    )).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=409, detail="该设备已绑定到此成员")

    md = MemberDevice(member_id=member_id, mac=body.mac, label=body.label)
    db.add(md)
    await db.commit()
    await db.refresh(md)

    device = (await db.execute(select(Device).where(Device.mac == body.mac))).scalar_one_or_none()
    return MemberDeviceOut(
        id=md.id,
        member_id=md.member_id,
        mac=md.mac,
        label=md.label,
        device_info=DeviceOut.model_validate(device) if device else None,
    )


@router.delete("/{member_id}/devices/{mac}", status_code=status.HTTP_204_NO_CONTENT)
async def unbind_device(member_id: int, mac: str, db: DBDep, _: CurrentUser):
    md = (await db.execute(
        select(MemberDevice).where(MemberDevice.member_id == member_id, MemberDevice.mac == mac)
    )).scalar_one_or_none()
    if not md:
        raise HTTPException(status_code=404, detail="绑定关系不存在")
    await db.delete(md)
    await db.commit()


@router.get("/{member_id}/logs", response_model=PagedResponse[PresenceLogOut])
async def list_presence_logs(
    member_id: int,
    db: DBDep,
    _: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    member = (await db.execute(select(Member).where(Member.id == member_id))).scalar_one_or_none()
    if not member:
        _not_found()

    q = select(PresenceLog).where(PresenceLog.member_id == member_id).order_by(PresenceLog.occurred_at.desc())
    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    items = (await db.execute(q.offset((page - 1) * page_size).limit(page_size))).scalars().all()

    return PagedResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        pages=math.ceil(total / page_size) if total else 0,
    )
