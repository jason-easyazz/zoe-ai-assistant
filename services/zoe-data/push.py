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

    User-scoped broadcasts: pass user_id= to broadcast() so that messages are
    only delivered to connections owned by that user, preventing cross-user
    data leakage.  Panel connections are exempt from user scoping (they use
    connect_panel and broadcast_to_panel instead).
    """

    def __init__(self):
        self._connections: Dict[str, Set[WebSocket]] = {}
        # Track which panel_id owns each WebSocket for disconnect cleanup.
        self._ws_panels: Dict[WebSocket, str] = {}
        # Map WebSocket → user_id for scoped delivery (None = panel/anonymous).
        self._ws_users: Dict[WebSocket, str | None] = {}
        self._sequence = 0

    async def connect(self, websocket: WebSocket, channel: str = "all", user_id: str | None = None):
        await websocket.accept()
        if channel not in self._connections:
            self._connections[channel] = set()
        self._connections[channel].add(websocket)
        self._ws_users[websocket] = user_id
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
        self._ws_users.pop(websocket, None)

    async def broadcast(
        self,
        channel: str,
        event_type: str,
        data: dict,
        user_id: str | None = None,
    ) -> int:
        """Broadcast to all subscribers on a channel.

        Pass ``user_id`` to restrict delivery to connections owned by that
        user.  Connections without an associated user (panels, anonymous) are
        always included so that global events still reach them.

        Returns the number of subscribers that received the message
        successfully.  Returns 0 if the channel has no connections or all
        sends failed.
        """
        self._sequence += 1
        message = {
            "type": event_type,
            "channel": channel,
            "data": data,
            "sequence": self._sequence
        }
        if channel not in self._connections:
            return 0

        dead = set()
        delivered = 0
        for ws in self._connections[channel]:
            # Skip if this connection belongs to a different user.
            ws_user = self._ws_users.get(ws)
            if user_id is not None and ws_user is not None and ws_user != user_id:
                continue
            try:
                await ws.send_json(message)
                delivered += 1
            except Exception:
                dead.add(ws)

        for ws in dead:
            self._connections[channel].discard(ws)

        return delivered

    async def broadcast_to_panel(self, panel_id: str, event_type: str, data: dict) -> int:
        """Send an event to the named panel's dedicated channel.

        Returns only confirmed delivery to the panel-specific channel. A legacy
        fallback still broadcasts to all so older clients can opportunistically
        receive the event, but that fallback is not proof the target panel saw it.
        """
        panel_channel = f"panel_{panel_id}"
        if panel_channel in self._connections and self._connections[panel_channel]:
            return await self.broadcast(panel_channel, event_type, data)

        # Fallback: broadcast to all with panel_id in data so clients can filter,
        # but report zero dedicated panel deliveries for durable queue semantics.
        await self.broadcast("all", event_type, data)
        return 0

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
