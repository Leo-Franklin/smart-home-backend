from typing import Protocol
from app.domain.models.schedule import Schedule


class ScheduleRepository(Protocol):
    """Schedule repository interface."""

    async def get_enabled(self) -> list[Schedule]:
        ...

    async def get_by_id(self, schedule_id: int) -> Schedule | None:
        ...