from typing import Protocol
from app.domain.models.device import Device


class DeviceRepository(Protocol):
    """Device repository interface."""

    async def get_by_mac(self, mac: str) -> Device | None:
        ...

    async def list_all(self) -> list[Device]:
        ...

    async def upsert_batch(self, devices: list[Device]) -> None:
        ...

    async def mark_offline(self, exclude_macs: list[str]) -> int:
        ...