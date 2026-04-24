from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from app.deps import DBDep, CurrentUser
from app.models.schedule import Schedule
from app.schemas.schedule import ScheduleCreate, ScheduleUpdate, ScheduleOut
from app.services.scheduler_service import scheduler_service
from loguru import logger

router = APIRouter(prefix="/schedules", tags=["schedules"])


@router.get("", response_model=list[ScheduleOut])
async def list_schedules(db: DBDep, _: CurrentUser):
    result = await db.execute(select(Schedule))
    return result.scalars().all()


@router.post("", response_model=ScheduleOut, status_code=status.HTTP_201_CREATED)
async def create_schedule(body: ScheduleCreate, db: DBDep, _: CurrentUser):
    parts = body.cron_expr.split()
    if len(parts) != 5:
        raise HTTPException(status_code=400, detail="cron 表达式必须是 5 字段格式")
    schedule = Schedule(**body.model_dump())
    db.add(schedule)
    await db.commit()
    await db.refresh(schedule)
    if schedule.enabled:
        logger.info(f"注册计划任务: {schedule.id} ({schedule.cron_expr})")
    return schedule


@router.get("/{schedule_id}", response_model=ScheduleOut)
async def get_schedule(schedule_id: int, db: DBDep, _: CurrentUser):
    result = await db.execute(select(Schedule).where(Schedule.id == schedule_id))
    schedule = result.scalar_one_or_none()
    if not schedule:
        raise HTTPException(status_code=404, detail="计划不存在")
    return schedule


@router.patch("/{schedule_id}", response_model=ScheduleOut)
async def update_schedule(schedule_id: int, body: ScheduleUpdate, db: DBDep, _: CurrentUser):
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
