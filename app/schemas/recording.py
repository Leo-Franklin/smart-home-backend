from datetime import datetime
from pydantic import BaseModel


class RecordingOut(BaseModel):
    id: int
    camera_mac: str
    file_path: str
    file_size: int | None
    duration: int | None
    started_at: datetime
    ended_at: datetime | None
    status: str
    error_msg: str | None
    created_at: datetime

    model_config = {"from_attributes": True}
