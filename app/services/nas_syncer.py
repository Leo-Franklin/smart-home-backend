import shutil
from pathlib import Path
from datetime import datetime
from loguru import logger


class NasSyncer:
    def __init__(self, mode: str, mount_path: str = "",
                 smb_host: str = "", smb_share: str = "",
                 smb_user: str = "", smb_password: str = ""):
        self.mode = mode
        self.mount_path = Path(mount_path) if mount_path else None
        self.smb_config = {
            "host": smb_host, "share": smb_share,
            "user": smb_user, "password": smb_password,
        }

    def sync_file(self, src: Path, camera_mac: str) -> Path:
        date_dir = datetime.now().strftime("%Y-%m-%d")
        safe_mac = camera_mac.replace(":", "")
        relative = f"{safe_mac}/{date_dir}/{src.name}"
        if self.mode == "mount":
            return self._sync_via_mount(src, relative)
        elif self.mode == "smb":
            return self._sync_via_smb(src, relative)
        else:
            raise ValueError(f"未知 NAS_MODE: {self.mode}")

    def _sync_via_mount(self, src: Path, relative: str) -> Path:
        dest = self.mount_path / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        logger.info(f"NAS同步(mount): {src} → {dest}")
        shutil.move(str(src), str(dest))
        return dest

    def _sync_via_smb(self, src: Path, remote_path: str) -> Path:
        from smbclient import register_session, open_file
        register_session(
            self.smb_config["host"],
            username=self.smb_config["user"],
            password=self.smb_config["password"],
        )
        share = self.smb_config["share"]
        full_remote = f"\\\\{self.smb_config['host']}\\{share}\\{remote_path}"
        logger.info(f"NAS同步(SMB): {src} → {full_remote}")
        with open(src, "rb") as local_f:
            with open_file(full_remote, mode="wb") as remote_f:
                shutil.copyfileobj(local_f, remote_f, length=1024 * 1024)
        src.unlink()
        return Path(full_remote)

    def check_writable(self) -> bool:
        try:
            if self.mode == "mount" and self.mount_path:
                test_file = self.mount_path / ".health_check"
                test_file.write_text("ok")
                test_file.unlink()
                return True
            elif self.mode == "smb":
                from smbclient import register_session
                register_session(
                    self.smb_config["host"],
                    username=self.smb_config["user"],
                    password=self.smb_config["password"],
                )
                return True
            return False
        except Exception as e:
            logger.error(f"NAS 健康检查失败: {e}")
            return False
