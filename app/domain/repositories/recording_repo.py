from typing import Protocol
from app.domain.models.recording import Recording


class RecordingRepository(Protocol):
    """Recording repository interface."""

    async def get_by_id(self, recording_id: int) -> Recording | None:
        ...

    async def list_by_camera(self, camera_mac: str) -> list[Recording]:
        ...