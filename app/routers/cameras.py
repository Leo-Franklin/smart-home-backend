from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from app.deps import DBDep, CurrentUser
from app.models.camera import Camera
from app.schemas.camera import CameraCreate, CameraUpdate, CameraOut
from app.services.onvif_client import OnvifClient
from loguru import logger

router = APIRouter(prefix="/cameras", tags=["cameras"])


@router.get("", response_model=list[CameraOut])
async def list_cameras(db: DBDep, _: CurrentUser):
    result = await db.execute(select(Camera))
    return result.scalars().all()


@router.post("", response_model=CameraOut, status_code=status.HTTP_201_CREATED)
async def create_camera(body: CameraCreate, db: DBDep, _: CurrentUser):
    camera = Camera(**body.model_dump())
    db.add(camera)
    await db.commit()
    await db.refresh(camera)
    return camera


@router.get("/{mac}", response_model=CameraOut)
async def get_camera(mac: str, db: DBDep, _: CurrentUser):
    result = await db.execute(select(Camera).where(Camera.device_mac == mac))
    camera = result.scalar_one_or_none()
    if not camera:
        raise HTTPException(status_code=404, detail="摄像头未配置")
    return camera


@router.put("/{mac}", response_model=CameraOut)
async def update_camera(mac: str, body: CameraUpdate, db: DBDep, _: CurrentUser):
    result = await db.execute(select(Camera).where(Camera.device_mac == mac))
    camera = result.scalar_one_or_none()
    if not camera:
        raise HTTPException(status_code=404, detail="摄像头未配置")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(camera, field, value)
    await db.commit()
    await db.refresh(camera)
    return camera


@router.delete("/{mac}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_camera(mac: str, db: DBDep, _: CurrentUser):
    result = await db.execute(select(Camera).where(Camera.device_mac == mac))
    camera = result.scalar_one_or_none()
    if not camera:
        raise HTTPException(status_code=404, detail="摄像头未配置")
    await db.delete(camera)
    await db.commit()


@router.post("/{mac}/probe")
async def probe_camera(mac: str, db: DBDep, _: CurrentUser):
    result = await db.execute(select(Camera).where(Camera.device_mac == mac))
    camera = result.scalar_one_or_none()
    if not camera:
        raise HTTPException(status_code=404, detail="摄像头未配置")
    client = OnvifClient(camera.onvif_host, camera.onvif_port,
                         camera.onvif_user or "", camera.onvif_password or "")
    try:
        info = await client.get_device_info()
        profiles = await client.get_profiles()
        return {"device_info": info, "profiles": profiles}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"ONVIF 通信异常: {e}")


@router.post("/{mac}/record/start", status_code=status.HTTP_202_ACCEPTED)
async def start_recording(mac: str, db: DBDep, _: CurrentUser):
    result = await db.execute(select(Camera).where(Camera.device_mac == mac))
    camera = result.scalar_one_or_none()
    if not camera:
        raise HTTPException(status_code=404, detail="摄像头未配置")
    if camera.is_recording:
        raise HTTPException(status_code=409, detail="该摄像头已在录制中")
    # Actual FFmpeg start is handled by recorder service (wired in main.py)
    return {"message": "录制启动请求已接受"}


@router.post("/{mac}/record/stop", status_code=status.HTTP_202_ACCEPTED)
async def stop_recording(mac: str, db: DBDep, _: CurrentUser):
    result = await db.execute(select(Camera).where(Camera.device_mac == mac))
    camera = result.scalar_one_or_none()
    if not camera:
        raise HTTPException(status_code=404, detail="摄像头未配置")
    if not camera.is_recording:
        raise HTTPException(status_code=409, detail="该摄像头未在录制")
    return {"message": "停止录制请求已接受"}
