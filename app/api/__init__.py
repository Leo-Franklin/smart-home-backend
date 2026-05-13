"""API layer - re-exported from app.routers for backward compatibility."""
from app.routers import *
from app.api.devices import router as devices_router
from app.api.cameras import router as cameras_router
from app.api.recordings import router as recordings_router
from app.api.schedules import router as schedules_router
from app.api.members import router as members_router
from app.api.dlna import router as dlna_router
from app.api.analytics import router as analytics_router
from app.api.system import router as system_router
from app.api.user import router as user_router
from app.api.ws import router as ws_router