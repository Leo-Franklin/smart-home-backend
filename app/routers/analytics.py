# app/routers/analytics.py
from datetime import datetime, timedelta
from fastapi import APIRouter, Query
from sqlalchemy import select, func, Integer, case
from app.deps import DBDep, CurrentUser
from app.models.device import Device
from app.models.recording import Recording
from app.models.device_online_log import DeviceOnlineLog

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


_KNOWN_TYPES = ["camera", "computer", "phone", "iot", "unknown"]


@router.get("/online-trend")
async def online_trend(
    db: DBDep,
    _: CurrentUser,
    range_str: str = Query("7d", alias="range"),
):
    since = datetime.now() - timedelta(days=_days(range_str))

    # Per hour bucket: count distinct devices with online_count > 0
    subq = (
        select(
            func.strftime("%Y-%m-%d", DeviceOnlineLog.bucket_hour).label("date"),
            func.sum(
                case((DeviceOnlineLog.online_count > 0, 1), else_=0)
            ).label("online_count"),
        )
        .where(DeviceOnlineLog.bucket_hour >= since)
        .group_by(DeviceOnlineLog.bucket_hour)
        .subquery()
    )

    result = await db.execute(
        select(
            subq.c.date,
            func.avg(subq.c.online_count).label("avg_count"),
        )
        .group_by(subq.c.date)
        .order_by(subq.c.date)
    )
    return {
        "data": [
            {"timestamp": f"{row.date}T00:00:00", "count": round(row.avg_count)}
            for row in result
        ]
    }


@router.get("/device-stability")
async def device_stability(
    db: DBDep,
    _: CurrentUser,
    range_str: str = Query("7d", alias="range"),
):
    since = datetime.now() - timedelta(days=_days(range_str))

    agg = await db.execute(
        select(
            DeviceOnlineLog.mac,
            func.sum(DeviceOnlineLog.online_count).label("total_online"),
            func.sum(DeviceOnlineLog.scan_count).label("total_scans"),
        )
        .where(DeviceOnlineLog.bucket_hour >= since)
        .group_by(DeviceOnlineLog.mac)
    )
    rows = agg.all()

    macs = [r.mac for r in rows]
    name_result = await db.execute(
        select(Device.mac, Device.alias, Device.hostname).where(Device.mac.in_(macs))
    )
    name_map = {d.mac: (d.alias or d.hostname or d.mac) for d in name_result.all()}

    data = [
        {
            "mac": r.mac,
            "name": name_map.get(r.mac, r.mac),
            "uptime_pct": round(r.total_online / r.total_scans * 100, 1) if r.total_scans else 0.0,
        }
        for r in rows
    ]
    data.sort(key=lambda x: x["uptime_pct"], reverse=True)
    return {"data": data}


@router.get("/type-activity")
async def type_activity(
    db: DBDep,
    _: CurrentUser,
    range_str: str = Query("7d", alias="range"),
):
    since = datetime.now() - timedelta(days=_days(range_str))

    result = await db.execute(
        select(
            func.cast(func.strftime("%H", DeviceOnlineLog.bucket_hour), Integer).label("hour"),
            DeviceOnlineLog.device_type,
            func.avg(
                case(
                    (DeviceOnlineLog.scan_count > 0,
                     DeviceOnlineLog.online_count * 1.0 / DeviceOnlineLog.scan_count),
                    else_=0.0,
                )
            ).label("fraction"),
        )
        .where(DeviceOnlineLog.bucket_hour >= since)
        .group_by(
            func.strftime("%H", DeviceOnlineLog.bucket_hour),
            DeviceOnlineLog.device_type,
        )
    )

    pivot: dict[int, dict[str, float]] = {
        h: {t: 0.0 for t in _KNOWN_TYPES} for h in range(24)
    }
    for row in result:
        if row.device_type in _KNOWN_TYPES:
            pivot[row.hour][row.device_type] = round(row.fraction or 0.0, 3)

    return {
        "data": [{"hour": h, **fractions} for h, fractions in sorted(pivot.items())]
    }
