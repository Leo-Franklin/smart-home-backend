import asyncio
from loguru import logger


class OnvifClient:
    def __init__(self, host: str, port: int, user: str, password: str):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self._camera = None

    def _get_camera(self):
        if self._camera is None:
            from onvif import ONVIFCamera
            self._camera = ONVIFCamera(self.host, self.port, self.user, self.password)
        return self._camera

    async def get_device_info(self) -> dict:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._get_device_info_sync)

    def _get_device_info_sync(self) -> dict:
        cam = self._get_camera()
        svc = cam.create_devicemgmt_service()
        info = svc.GetDeviceInformation()
        return {
            "manufacturer": info.Manufacturer,
            "model": info.Model,
            "firmware": info.FirmwareVersion,
            "serial": info.SerialNumber,
        }

    async def get_stream_uri(self, profile_index: int = 0) -> str:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._get_stream_uri_sync, profile_index)

    def _get_stream_uri_sync(self, profile_index: int) -> str:
        cam = self._get_camera()
        media = cam.create_media_service()
        profiles = media.GetProfiles()
        if profile_index >= len(profiles):
            profile_index = 0
        token = profiles[profile_index].token
        uri = media.GetStreamUri({
            "StreamSetup": {"Stream": "RTP-Unicast", "Transport": {"Protocol": "RTSP"}},
            "ProfileToken": token,
        })
        return uri.Uri

    async def get_snapshot_uri(self) -> str:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._get_snapshot_uri_sync)

    def _get_snapshot_uri_sync(self) -> str:
        cam = self._get_camera()
        media = cam.create_media_service()
        profiles = media.GetProfiles()
        token = profiles[0].token
        uri = media.GetSnapshotUri({"ProfileToken": token})
        return uri.Uri

    async def get_profiles(self) -> list[dict]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._get_profiles_sync)

    def _get_profiles_sync(self) -> list[dict]:
        cam = self._get_camera()
        media = cam.create_media_service()
        profiles = media.GetProfiles()
        return [
            {
                "index": i,
                "name": p.Name,
                "token": p.token,
            }
            for i, p in enumerate(profiles)
        ]

    async def is_reachable(self) -> bool:
        try:
            await self.get_device_info()
            return True
        except Exception as e:
            logger.debug(f"ONVIF 不可达 {self.host}:{self.port}: {e}")
            return False
