from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator
from functools import lru_cache

_INSECURE_JWT_DEFAULTS = {
    "change_me_to_a_random_string_at_least_32_chars",
    "",
}
_INSECURE_PASSWORD_DEFAULTS = {"change_me", ""}


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

    # CORS — separate from debug flag so security policy isn't coupled to debug mode
    # Multiple origins: comma-separated, e.g. "http://localhost:5173,https://app.example.com"
    cors_allow_origins: str = "http://localhost:5173"

    # Database
    database_url: str = "sqlite+aiosqlite:///./data/smart_home.db"

    # App meta
    app_version: str = "1.0.0"

    @field_validator("jwt_secret_key")
    @classmethod
    def jwt_secret_must_be_changed(cls, v: str) -> str:
        if v in _INSECURE_JWT_DEFAULTS or len(v) < 32:
            raise ValueError(
                "JWT_SECRET_KEY 必须设置为至少 32 字符的随机字符串，"
                "请在 .env 中配置 JWT_SECRET_KEY"
            )
        return v

    @field_validator("admin_password")
    @classmethod
    def admin_password_must_be_changed(cls, v: str) -> str:
        if v in _INSECURE_PASSWORD_DEFAULTS:
            raise ValueError(
                "ADMIN_PASSWORD 不能使用默认值，"
                "请在 .env 中配置强密码"
            )
        if len(v) < 8:
            raise ValueError("ADMIN_PASSWORD 至少需要 8 个字符")
        return v

    def get_cors_origins(self) -> list[str]:
        return [o.strip() for o in self.cors_allow_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
