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
    # 新增字段
    storage_type: str  # "local" | "nas"
    nas_access_url: str | None  # NAS 时返回可访问 URL，本地为 None
    file_name: str  # 从 file_path 提取的文件名

    model_config = {"from_attributes": True}
