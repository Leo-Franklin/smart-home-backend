from typing import Protocol
from app.domain.models.camera import Camera


class CameraRepository(Protocol):
    """Camera repository interface."""

    async def get_by_mac(self, mac: str) -> Camera | None:
        ...

    async def list_all(self) -> list[Camera]:
        ...