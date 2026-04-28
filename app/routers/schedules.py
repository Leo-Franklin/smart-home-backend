from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import select
from app.deps import DBDep, CurrentUser
from app.models.schedule import Schedule
from app.schemas.schedule import ScheduleCreate, ScheduleUpdate, ScheduleOut
from app.services.scheduler_service import scheduler_service
from loguru import logger

router = APIRouter(prefix="/schedules", tags=["schedules"])


async def _make_recording_callback(request: Request):
    recorder = request.app.state.recorder

    async def _trigger(camera_mac: str):
        from sqlalchemy import select as _select
        from app.database import AsyncSessionLocal
        from app.models.camera import Camera as CameraModel
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                _select(CameraModel).where(CameraModel.device_mac == camera_mac)
            )
            cam = result.scalar_one_or_none()
            if not cam or not cam.rtsp_url:
                logger.warning(f"调度录制: 摄像头 {camera_mac} 不存在或无 RTSP URL")
                return
            if cam.is_recording:
                logger.info(f"调度录制: {camera_mac} 已在录制中，跳过")
                return
        await recorder.start_recording(camera_mac=cam.device_mac, rtsp_url=cam.rtsp_url)

    return _trigger


@router.get("", response_model=list[ScheduleOut])
async def list_schedules(db: DBDep, _: CurrentUser):
    result = await db.execute(select(Schedule))
    return result.scalars().all()


@router.post("", response_model=ScheduleOut, status_code=status.HTTP_201_CREATED)
async def create_schedule(body: ScheduleCreate, request: Request, db: DBDep, _: CurrentUser):
    parts = body.cron_expr.split()
    if len(parts) != 5:
        raise HTTPException(status_code=400, detail="cron 表达式必须是 5 字段格式")
    schedule = Schedule(**body.model_dump())
    db.add(schedule)
    await db.commit()
    await db.refresh(schedule)
    if schedule.enabled:
        callback = await _make_recording_callback(request)
        scheduler_service.add_recording_job(
            job_id=f"schedule_{schedule.id}",
            cron_expr=schedule.cron_expr,
            camera_mac=schedule.camera_mac,
            callback=callback,
        )
        logger.info(f"已注册调度任务: schedule_{schedule.id} ({schedule.cron_expr})")
    return schedule


@router.get("/{schedule_id}", response_model=ScheduleOut)
async def get_schedule(schedule_id: int, db: DBDep, _: CurrentUser):
    result = await db.execute(select(Schedule).where(Schedule.id == schedule_id))
    schedule = result.scalar_one_or_none()
    if not schedule:
        raise HTTPException(status_code=404, detail="计划不存在")
    return schedule


@router.patch("/{schedule_id}", response_model=ScheduleOut)
async def update_schedule(schedule_id: int, body: ScheduleUpdate, request: Request, db: DBDep, _: CurrentUser):
    result = await db.execute(select(Schedule).where(Schedule.id == schedule_id))
    schedule = result.scalar_one_or_none()
    if not schedule:
        raise HTTPException(status_code=404, detail="计划不存在")
    if body.cron_expr is not None and len(body.cron_expr.split()) != 5:
        raise HTTPException(status_code=400, detail="cron 表达式必须是 5 字段格式")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(schedule, field, value)
    await db.commit()
    await db.refresh(schedule)

    job_id = f"schedule_{schedule.id}"
    if schedule.enabled:
        callback = await _make_recording_callback(request)
        scheduler_service.add_recording_job(
            job_id=job_id,
            cron_expr=schedule.cron_expr,
            camera_mac=schedule.camera_mac,
            callback=callback,
        )
        logger.info(f"已更新调度任务: {job_id} ({schedule.cron_expr})")
    else:
        scheduler_service.remove_job(job_id)
        logger.info(f"已禁用调度任务: {job_id}")
    return schedule


@router.delete("/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_schedule(schedule_id: int, db: DBDep, _: CurrentUser):
    result = await db.execute(select(Schedule).where(Schedule.id == schedule_id))
    schedule = result.scalar_one_or_none()
    if not schedule:
        raise HTTPException(status_code=404, detail="计划不存在")
    scheduler_service.remove_job(f"schedule_{schedule_id}")
    await db.delete(schedule)
    await db.commit()
