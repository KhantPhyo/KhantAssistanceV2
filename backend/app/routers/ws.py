from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from ..security import decode_token
from ..services.ws_hub import hub

router = APIRouter()


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket, token: str = Query(None)):
    sub = decode_token(token) if token else None
    if not sub:
        await ws.close(code=4401)
        return
    await hub.connect(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        await hub.disconnect(ws)
    except Exception:
        await hub.disconnect(ws)
