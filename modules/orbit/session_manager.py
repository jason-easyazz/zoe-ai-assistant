"""In-memory session state and WebSocket fan-out for Orbit."""
from __future__ import annotations
import asyncio
import json
import logging
from typing import Optional
from fastapi import WebSocket

log = logging.getLogger("orbit.session")

INACTIVITY_TIMEOUT = 20 * 60  # 20 minutes in seconds


class SessionManager:
    def __init__(self) -> None:
        # session_id -> set of host WebSockets
        self._host_sockets: dict[str, set[WebSocket]] = {}
        # checkin_id -> WebSocket
        self._player_sockets: dict[str, WebSocket] = {}
        # checkin_id -> session_id (for routing)
        self._checkin_session: dict[str, str] = {}
        # checkin_id -> last_seen timestamp (for inactivity)
        self._last_seen: dict[str, float] = {}
        # pending inactivity tasks
        self._inactivity_tasks: dict[str, asyncio.Task] = {}

    # ── Host connections ──────────────────────────────────────────────────────

    def register_host(self, session_id: str, ws: WebSocket) -> None:
        self._host_sockets.setdefault(session_id, set()).add(ws)

    def unregister_host(self, session_id: str, ws: WebSocket) -> None:
        sockets = self._host_sockets.get(session_id, set())
        sockets.discard(ws)

    async def broadcast_host(self, session_id: str, msg: dict) -> None:
        dead: list[WebSocket] = []
        for ws in list(self._host_sockets.get(session_id, set())):
            try:
                await ws.send_text(json.dumps(msg))
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._host_sockets.get(session_id, set()).discard(ws)

    # ── Player connections ────────────────────────────────────────────────────

    def register_player(self, checkin_id: str, session_id: str, ws: WebSocket) -> None:
        self._player_sockets[checkin_id] = ws
        self._checkin_session[checkin_id] = session_id
        import time
        self._last_seen[checkin_id] = time.time()
        self._schedule_inactivity_check(checkin_id)

    def unregister_player(self, checkin_id: str) -> None:
        self._player_sockets.pop(checkin_id, None)
        self._checkin_session.pop(checkin_id, None)
        self._last_seen.pop(checkin_id, None)
        task = self._inactivity_tasks.pop(checkin_id, None)
        if task:
            task.cancel()

    def touch(self, checkin_id: str) -> None:
        import time
        self._last_seen[checkin_id] = time.time()

    def get_session_for(self, checkin_id: str) -> Optional[str]:
        return self._checkin_session.get(checkin_id)

    async def send_player(self, checkin_id: str, msg: dict) -> bool:
        ws = self._player_sockets.get(checkin_id)
        if not ws:
            return False
        try:
            await ws.send_text(json.dumps(msg))
            return True
        except Exception:
            self.unregister_player(checkin_id)
            return False

    async def broadcast_session_players(self, session_id: str, msg: dict) -> None:
        """Send a message to all players in a session."""
        targets = [
            cid for cid, sid in self._checkin_session.items()
            if sid == session_id
        ]
        for cid in targets:
            await self.send_player(cid, msg)

    # ── Inactivity ────────────────────────────────────────────────────────────

    def _schedule_inactivity_check(self, checkin_id: str) -> None:
        task = self._inactivity_tasks.pop(checkin_id, None)
        if task:
            task.cancel()
        self._inactivity_tasks[checkin_id] = asyncio.create_task(
            self._inactivity_watchdog(checkin_id)
        )

    async def _inactivity_watchdog(self, checkin_id: str) -> None:
        import time
        await asyncio.sleep(INACTIVITY_TIMEOUT + 5)
        last = self._last_seen.get(checkin_id, 0)
        if time.time() - last >= INACTIVITY_TIMEOUT:
            log.info("Auto-checkout due to inactivity: %s", checkin_id)
            # Signal back to main.py via a lightweight event
            # The WS handler reads this and triggers checkout
            ws = self._player_sockets.get(checkin_id)
            if ws:
                try:
                    await ws.send_text(json.dumps({"type": "auto_checkout"}))
                except Exception:
                    pass


# Singleton
manager = SessionManager()
