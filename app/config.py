import sys
import configparser
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator
from functools import lru_cache

_INSECURE_JWT_DEFAULTS = {
    "change_me_to_a_random_string_at_least_32_chars",
    "",
}
_INSECURE_PASSWORD_DEFAULTS = {"change_me", ""}


def is_packaged() -> bool:
    """Returns True when running as a PyInstaller-bundled exe."""
    return getattr(sys, "frozen", False)


def _read_app_cfg() -> configparser.ConfigParser:
    """Read app.cfg for packaged mode."""
    if not is_packaged():
        return None
    cfg_path = Path(sys.executable).parent / "app.cfg"
    if not cfg_path.exists():
        return None
    config = configparser.ConfigParser()
    config.read(cfg_path, encoding="utf-8")
    return config


def get_data_dir() -> Path:
    """
    Packaged mode: read data_dir from app.cfg next to the exe.
    Dev mode: use ./data relative to cwd.
    """
    if is_packaged():
        cfg = _read_app_cfg()
        if cfg and cfg.has_option("paths", "data_dir"):
            return Path(cfg.get("paths", "data_dir"))
    return Path("./data")


_data_dir = get_data_dir()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # In packaged mode, override from app.cfg
        if is_packaged():
            cfg = _read_app_cfg()
            if cfg:
                if cfg.has_option("app", "jwt_secret_key"):
                    object.__setattr__(self, "jwt_secret_key", cfg.get("app", "jwt_secret_key"))
                if cfg.has_option("app", "admin_username"):
                    object.__setattr__(self, "admin_username", cfg.get("app", "admin_username"))
                if cfg.has_option("app", "admin_password"):
                    object.__setattr__(self, "admin_password", cfg.get("app", "admin_password"))
                if cfg.has_option("app", "log_level"):
                    object.__setattr__(self, "log_level", cfg.get("app", "log_level"))

    # Network
    network_range: str = "auto"
    scan_interval_seconds: int = 60
    presence_poll_interval_seconds: int = 30
    camera_health_interval_seconds: int = 60   # A3: camera probe interval
    server_port: int = 8000                    # A4: for constructing DLNA media URLs

    # Camera
    camera_onvif_user: str = "admin"
    camera_onvif_password: str = ""

    # NAS / 本地存储
    nas_mode: str = "local"  # local | mount | smb
    local_storage_path: str = str(_data_dir / "recordings")
    nas_mount_path: str = "/nas/cameras"
    nas_smb_host: str = ""
    nas_smb_share: str = ""
    nas_smb_user: str = ""
    nas_smb_password: str = ""

    # Recording
    recording_temp_dir: str = str(_data_dir / "recordings" / "tmp")
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
    database_url: str = f"sqlite+aiosqlite:///{_data_dir / 'smart_home.db'}"

    # App meta
    app_version: str = "1.0.0"

    @field_validator("jwt_secret_key")
    @classmethod
    def jwt_secret_must_be_changed(cls, v: str) -> str:
        if is_packaged():
            return v  # packaged exe generates its own key at install time
        if v in _INSECURE_JWT_DEFAULTS or len(v) < 32:
            raise ValueError(
                "JWT_SECRET_KEY 必须设置为至少 32 字符的随机字符串，"
                "请在 .env 中配置 JWT_SECRET_KEY"
            )
        return v

    @field_validator("admin_password")
    @classmethod
    def admin_password_must_be_changed(cls, v: str) -> str:
        if is_packaged():
            return v  # packaged exe manages its own credentials
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
