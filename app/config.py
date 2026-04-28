from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Network
    network_range: str = "auto"
    scan_interval_seconds: int = 60
    presence_poll_interval_seconds: int = 30

    # Camera
    camera_onvif_user: str = "admin"
    camera_onvif_password: str = ""

    # NAS / 本地存储
    nas_mode: str = "local"  # local | mount | smb
    local_storage_path: str = "./data/recordings"
    nas_mount_path: str = "/nas/cameras"
    nas_smb_host: str = ""
    nas_smb_share: str = ""
    nas_smb_user: str = ""
    nas_smb_password: str = ""

    # Recording
    recording_temp_dir: str = "/tmp/recordings"
    recording_segment_seconds: int = 1800
    recording_retention_days: int = 30

    # App
    jwt_secret_key: str = "change_me_to_a_random_string_at_least_32_chars"
    admin_username: str = "admin"
    admin_password: str = "change_me"
    log_level: str = "INFO"
    debug: bool = False

    # Database
    database_url: str = "sqlite+aiosqlite:///./data/smart_home.db"

    # App meta
    app_version: str = "1.0.0"


@lru_cache
def get_settings() -> Settings:
    return Settings()
