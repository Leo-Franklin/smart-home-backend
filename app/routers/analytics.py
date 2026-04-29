# app/routers/analytics.py
from datetime import datetime, timedelta
from fastapi import APIRouter, Query
from sqlalchemy import select, func
from app.deps import DBDep, CurrentUser
from app.models.device import Device
from app.models.recording import Recording

router = APIRouter(prefix="/analytics", tags=["analytics"])

_RANGE_DAYS = {"7d": 7, "30d": 30, "90d": 90}


def _days(range_str: str) -> int:
    return _RANGE_DAYS.get(range_str, 7)


@router.get("/device-type-stats")
async def device_type_stats(db: DBDep, _: CurrentUser):
    result = await db.execute(
        select(Device.device_type, func.count().label("count"))
        .group_by(Device.device_type)
        .order_by(func.count().desc())
    )
    return {"data": [{"type": row.device_type, "count": row.count} for row in result]}


@router.get("/response-time")
async def response_time(db: DBDep, _: CurrentUser):
    result = await db.execute(
        select(Device.mac, Device.alias, Device.hostname, Device.response_time_ms)
        .where(Device.response_time_ms.isnot(None))
        .order_by(Device.response_time_ms.desc())
    )
    return {
        "data": [
            {
                "mac": row.mac,
                "name": row.alias or row.hostname or row.mac,
                "avg_ms": row.response_time_ms,
            }
            for row in result
        ]
    }


@router.get("/recording-calendar")
async def recording_calendar(
    db: DBDep,
    _: CurrentUser,
    range_str: str = Query("90d", alias="range"),
):
    since = datetime.now() - timedelta(days=_days(range_str))
    result = await db.execute(
        select(
            func.strftime("%Y-%m-%d", Recording.started_at).label("date"),
            func.count().label("count"),
        )
        .where(Recording.started_at >= since)
        .group_by(func.strftime("%Y-%m-%d", Recording.started_at))
        .order_by("date")
    )
    return {"data": [{"date": row.date, "count": row.count} for row in result]}


@router.get("/new-devices")
async def new_devices(
    db: DBDep,
    _: CurrentUser,
    range_str: str = Query("90d", alias="range"),
):
    since = datetime.now() - timedelta(days=_days(range_str))
    result = await db.execute(
        select(
            func.strftime("%Y-W%W", Device.created_at).label("period"),
            func.count().label("count"),
        )
        .where(Device.created_at >= since)
        .group_by(func.strftime("%Y-W%W", Device.created_at))
        .order_by("period")
    )
    return {"data": [{"period": row.period, "count": row.count} for row in result]}
