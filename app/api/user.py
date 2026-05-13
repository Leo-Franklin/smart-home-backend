from fastapi import APIRouter, HTTPException, status
from app.deps import DBDep, CurrentUser
from app.models.user_settings import UserSettings
from app.schemas.user import UserProfile, UserProfileUpdate

router = APIRouter()


@router.get("/user/profile", response_model=UserProfile)
async def get_user_profile(db: DBDep, _: CurrentUser):
    settings = await db.get(UserSettings, 1)
    if settings is None:
        # Create default settings if not exists
        settings = UserSettings(id=1, language="zh-CN")
        db.add(settings)
        await db.commit()
        await db.refresh(settings)
    return UserProfile(language=settings.language)


@router.put("/user/profile", response_model=UserProfile)
async def update_user_profile(db: DBDep, _: CurrentUser, body: UserProfileUpdate):
    settings = await db.get(UserSettings, 1)
    if settings is None:
        settings = UserSettings(id=1, language=body.language)
        db.add(settings)
    else:
        settings.language = body.language
    await db.commit()
    await db.refresh(settings)
    return UserProfile(language=settings.language)
