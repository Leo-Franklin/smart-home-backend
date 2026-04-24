from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.memory import MemoryJobStore
from loguru import logger


class SchedulerService:
    def __init__(self):
        self.scheduler = AsyncIOScheduler(
            jobstores={"default": MemoryJobStore()},
            timezone="Asia/Shanghai",
        )

    def start(self):
        self.scheduler.start()
        logger.info("APScheduler 已启动")

    def shutdown(self):
        self.scheduler.shutdown(wait=False)
        logger.info("APScheduler 已停止")

    def add_recording_job(self, job_id: str, cron_expr: str, camera_mac: str, callback):
        parts = cron_expr.split()
        if len(parts) != 5:
            raise ValueError(f"无效 cron 表达式: {cron_expr}")
        minute, hour, day, month, day_of_week = parts
        self.scheduler.add_job(
            callback,
            "cron",
            id=job_id,
            replace_existing=True,
            args=[camera_mac],
            minute=minute,
            hour=hour,
            day=day,
            month=month,
            day_of_week=day_of_week,
        )
        logger.info(f"已添加录制计划: {job_id} ({cron_expr})")

    def remove_job(self, job_id: str):
        try:
            self.scheduler.remove_job(job_id)
            logger.info(f"已删除计划: {job_id}")
        except Exception:
            pass


scheduler_service = SchedulerService()
