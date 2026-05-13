"""Domain services - re-exported from app.services for backward compatibility."""
# Re-export everything from app.services for backward compatibility
from app.services import *
# New domain services
from app.domain.services.onvif_client import OnvifClient
from app.domain.services.ws_manager import ws_manager
from app.services.ws_manager import WebSocketManager
from app.domain.services.scheduler_service import scheduler_service, SchedulerService
from app.domain.services.nas_syncer import NasSyncer
from app.domain.services.recorder import Recorder, RecordingTask
from app.domain.services.camera_health import CameraHealthChecker
from app.domain.services.presence_service import PresenceService, presence_service
from app.domain.services.dlna_service import DLNAController
from app.domain.services.scanner import Scanner
from app.domain.services.recording_domain import RecordingDomainService
from app.domain.services.presence_domain import PresenceDomainService