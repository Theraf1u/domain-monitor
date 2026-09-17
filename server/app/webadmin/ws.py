"""Live events WebSocket. Auth: the browser's session cookie travels with
the WS handshake automatically (same-origin), so we just validate it the
same way a page route would - no separate token scheme needed.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.database import Database

router = APIRouter()


@router.websocket("/admin/ws/events")
async def ws_events(websocket: WebSocket) -> None:
    db: Database = websocket.app.state.db
    session_id = websocket.cookies.get("session_id")
    session = db.get_session(session_id) if session_id else None
    if session is None or session.expires_at < datetime.now(timezone.utc):
        await websocket.close(code=4401)
        return

    broadcaster = websocket.app.state.broadcaster
    await broadcaster.connect(websocket)
    try:
        while True:
            await websocket.receive_text()  # client sends nothing meaningful; just keeps the socket open
    except WebSocketDisconnect:
        pass
    finally:
        await broadcaster.disconnect(websocket)
