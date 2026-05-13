# Backward-compat re-export — the real manager lives in app.services.ws_manager
from app.services.ws_manager import ws_manager

__all__ = ["ws_manager"]
