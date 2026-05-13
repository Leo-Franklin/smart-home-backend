"""Repository interfaces for decoupling ORM from business logic."""
from app.domain.repositories.device_repo import DeviceRepository
from app.domain.repositories.camera_repo import CameraRepository
from app.domain.repositories.recording_repo import RecordingRepository
from app.domain.repositories.schedule_repo import ScheduleRepository

__all__ = [
    "DeviceRepository",
    "CameraRepository",
    "RecordingRepository",
    "ScheduleRepository",
]