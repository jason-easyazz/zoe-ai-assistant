import asyncio
import json
from typing import Dict, Set
from fastapi import WebSocket
import logging

logger = logging.getLogger(__name__)


class PushBroadcaster:
    """WebSocket push broadcaster for real-time UI updates."""

    def __init__(self):
        self._connections: Dict[str, Set[WebSocket]] = {}
        self._sequence = 0

    async def connect(self, websocket: WebSocket, channel: str = "all"):
        await websocket.accept()
        if channel not in self._connections:
            self._connections[channel] = set()
        self._connections[channel].add(websocket)
        await websocket.send_json({
            "type": "connected",
            "channel": channel,
            "sequence": self._sequence
        })

    def disconnect(self, websocket: WebSocket, channel: str = "all"):
        if channel in self._connections:
            self._connections[channel].discard(websocket)

    async def broadcast(self, channel: str, event_type: str, data: dict):
        self._sequence += 1
        message = {
            "type": event_type,
            "channel": channel,
            "data": data,
            "sequence": self._sequence
        }
        if channel not in self._connections:
            return

        dead = set()
        for ws in self._connections[channel]:
            try:
                await ws.send_json(message)
            except Exception:
                dead.add(ws)

        for ws in dead:
            self._connections[channel].discard(ws)

    async def broadcast_all(self, event_type: str, data: dict):
        """Broadcast to all channels."""
        for channel in list(self._connections.keys()):
            await self.broadcast(channel, event_type, data)

    async def catchup(self, websocket: WebSocket, since_sequence: int):
        """Client reconnection catch-up. For now, just send current sequence."""
        await websocket.send_json({
            "type": "catchup",
            "current_sequence": self._sequence,
            "message": "Full state refresh recommended"
        })


broadcaster = PushBroadcaster()
