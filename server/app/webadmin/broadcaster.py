"""In-process fan-out to connected Web Admin WebSocket clients. No
external pub/sub (Redis etc.) - a single server process is the deployment
model here, so a plain in-memory set of sockets is enough and adds zero
extra infrastructure.
"""
from __future__ import annotations

import asyncio
import json
import logging

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class EventBroadcaster:
    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._clients.add(ws)

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(ws)

    async def broadcast(self, payload: dict) -> None:
        if not self._clients:
            return
        message = json.dumps(payload)
        async with self._lock:
            clients = list(self._clients)
        for ws in clients:
            try:
                await ws.send_text(message)
            except Exception:
                await self.disconnect(ws)
