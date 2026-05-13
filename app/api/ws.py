from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from app.auth import verify_token
from app.config import get_settings
from app.services.ws_manager import ws_manager
from loguru import logger

router = APIRouter(tags=["websocket"])


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket, token: str = Query(...)):
    settings = get_settings()
    username = verify_token(token, settings.jwt_secret_key)
    if username is None:
        await ws.close(code=4001)
        return

    await ws_manager.connect(ws)
    try:
        while True:
            await ws.receive_text()  # keep connection alive, ignore client messages
    except WebSocketDisconnect:
        pass
    finally:
        await ws_manager.disconnect(ws)
