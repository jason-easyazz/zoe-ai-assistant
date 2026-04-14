import asyncio
import json
from typing import Dict, Set
from fastapi import WebSocket
import logging

logger = logging.getLogger(__name__)


class PushBroadcaster:
    """WebSocket push broadcaster for real-time UI updates.

    Clients subscribe to a named channel ("all" is global; "panel_{id}" targets
    a specific touch panel).  The caller can broadcast_to_panel() to send an
    event exclusively to one panel's channel, bypassing the global "all" fan-out.
    """

    def __init__(self):
        self._connections: Dict[str, Set[WebSocket]] = {}
        # Track which panel_id owns each WebSocket for disconnect cleanup.
        self._ws_panels: Dict[WebSocket, str] = {}
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

    async def connect_panel(self, websocket: WebSocket, panel_id: str):
        """Subscribe a panel WebSocket to both 'all' (global) and its own panel channel."""
        panel_channel = f"panel_{panel_id}"
        await websocket.accept()
        for ch in ("all", panel_channel):
            if ch not in self._connections:
                self._connections[ch] = set()
            self._connections[ch].add(websocket)
        self._ws_panels[websocket] = panel_id
        await websocket.send_json({
            "type": "connected",
            "channel": panel_channel,
            "panel_id": panel_id,
            "sequence": self._sequence,
        })

    def disconnect(self, websocket: WebSocket, channel: str = "all"):
        if channel in self._connections:
            self._connections[channel].discard(websocket)
        # Also clean up panel channel if this was a panel connection.
        panel_id = self._ws_panels.pop(websocket, None)
        if panel_id:
            pc = f"panel_{panel_id}"
            if pc in self._connections:
                self._connections[pc].discard(websocket)
            if "all" in self._connections:
                self._connections["all"].discard(websocket)

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

    async def broadcast_to_panel(self, panel_id: str, event_type: str, data: dict):
        """Send an event only to the named panel's dedicated channel.

        Falls back to 'all' channel broadcast if the panel has no dedicated channel
        (e.g. it connected with plain connect() before this feature was deployed).
        """
        panel_channel = f"panel_{panel_id}"
        if panel_channel in self._connections and self._connections[panel_channel]:
            await self.broadcast(panel_channel, event_type, data)
        else:
            # Fallback: broadcast to all with panel_id in data so clients can filter.
            await self.broadcast("all", event_type, data)

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
