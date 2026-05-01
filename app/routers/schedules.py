from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import select
from datetime import datetime
from app.deps import DBDep, CurrentUser
from app.models.schedule import Schedule
from app.schemas.schedule import ScheduleCreate, ScheduleUpdate, ScheduleOut
from app.services.scheduler_service import scheduler_service
from loguru import logger

router = APIRouter(prefix="/schedules", tags=["schedules"])


def _make_recording_callback(request: Request, segment_duration: int):
    recorder = request.app.state.recorder

    async def _trigger(camera_mac: str):
        from sqlalchemy import select as _select
        from urllib.parse import urlparse, urlunparse
        from app.database import AsyncSessionLocal
        from app.models.camera import Camera as CameraModel
        from app.models.recording import Recording as RecordingModel
        rec_id = None
        async with AsyncSessionLocal() as db:
            cam = (await db.execute(
                _select(CameraModel).where(CameraModel.device_mac == camera_mac)
            )).scalar_one_or_none()
            if not cam or not cam.rtsp_url:
                logger.warning(f"调度录制: 摄像头 {camera_mac} 不存在或无 RTSP URL")
                return
            if cam.is_recording:
                logger.info(f"调度录制: {camera_mac} 已在录制中，跳过")
                return
            rtsp_url = cam.rtsp_url
            if cam.onvif_user or cam.onvif_password:
                parsed = urlparse(rtsp_url)
                netloc = f"{cam.onvif_user or ''}:{cam.onvif_password or ''}@{parsed.hostname or ''}"
                if parsed.port:
                    netloc += f":{parsed.port}"
                rtsp_url = urlunparse(parsed._replace(netloc=netloc))
            rec = RecordingModel(
                camera_mac=camera_mac,
                file_path="(pending)",
                started_at=datetime.now(),
                status="recording",
            )
            db.add(rec)
            cam.is_recording = True
            await db.commit()
            await db.refresh(rec)
            rec_id = rec.id
        try:
            await recorder.start_recording(camera_mac=camera_mac, rtsp_url=rtsp_url, segment_seconds=segment_duration)
        except Exception as e:
            logger.error(f"调度录制启动失败 {camera_mac}: {e}")
            async with AsyncSessionLocal() as db:
                rec_db = (await db.execute(
                    _select(RecordingModel).where(RecordingModel.id == rec_id)
                )).scalar_one_or_none()
                if rec_db:
                    rec_db.status = "failed"
                    rec_db.error_msg = str(e)
                cam_db = (await db.execute(
                    _select(CameraModel).where(CameraModel.device_mac == camera_mac)
                )).scalar_one_or_none()
                if cam_db:
                    cam_db.is_recording = False
                await db.commit()
            return
        if camera_mac in recorder.active:
            recorder.active[camera_mac].recording_id = rec_id

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
        callback = _make_recording_callback(request, schedule.segment_duration)
        try:
            scheduler_service.add_recording_job(
                job_id=f"schedule_{schedule.id}",
                cron_expr=schedule.cron_expr,
                camera_mac=schedule.camera_mac,
                callback=callback,
            )
            logger.info(f"已注册调度任务: schedule_{schedule.id} ({schedule.cron_expr})")
        except Exception as e:
            logger.error(f"APScheduler 注册失败 schedule_{schedule.id}: {e}")
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
        callback = _make_recording_callback(request, schedule.segment_duration)
        try:
            scheduler_service.add_recording_job(
                job_id=job_id,
                cron_expr=schedule.cron_expr,
                camera_mac=schedule.camera_mac,
                callback=callback,
            )
            logger.info(f"已更新调度任务: {job_id} ({schedule.cron_expr})")
        except Exception as e:
            logger.error(f"APScheduler 注册失败 {job_id}: {e}")
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
