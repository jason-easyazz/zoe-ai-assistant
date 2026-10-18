import asyncio
import json
import time
from typing import Dict, Optional, Set
from fastapi import WebSocket
import logging

logger = logging.getLogger(__name__)

# One deaf-panel WARNING per panel per this many seconds (a voice turn calls the
# check from several entry points — /turn_stream then /command — and a turn lasts
# up to ~20 s, so this is "one per turn").
DEAF_PANEL_WARN_INTERVAL_S = 30.0

# A panel that has had NO push subscriber for this long while voice turns keep arriving is not a
# blip (a page reload or a deploy restart reconnects in seconds): it is a dead channel nobody is
# looking at (2026-10-14: the wall panel sat deaf for hours behind one WARNING per turn). The turn
# then logs an ERROR, at most once per DEAF_PANEL_ERROR_INTERVAL_S per panel.
DEAF_PANEL_ERROR_AFTER_S = 300.0
DEAF_PANEL_ERROR_INTERVAL_S = 600.0

# A panel counts as live (see ``panel_live``) if it acked a UI action this recently.
PANEL_LIVE_WINDOW_S = 60.0


# A voice turn with no usable panel id pushes to this channel, which no socket can ever join (connect_panel subscribes
# ``panel_<id>``; a colon never appears there), so its events reach nobody instead of every screen.
NO_PANEL_CHANNEL = "panel:none"
_NO_PANEL_IDS = frozenset({"", "unknown", "none", "default", "all"})


def panel_channel(panel_id: Optional[str]) -> str:
    """The push channel for ONE panel's per-turn UI events (``voice:*``, the turn's ``ui_action`` cards/forms/navigation).

    A11 (architecture deep dive 2026-10-18): the voice path used to broadcast these on ``"all"``, so with a second panel,
    panel A showed panel B's transcripts and answers, and every signed-in desktop socket on ``"all"`` received them too.
    The turn's panel id is server-decided (``voice_tts._lane_panel_id``: a device token acts for its own panel), and only
    that panel's own socket subscribes to ``panel_<id>``. A blank / unknown id gets ``NO_PANEL_CHANNEL`` (nobody), never
    a fan-out. Unlike ``broadcast_to_panel`` there is no fallback to ``"all"``. Household-wide events (announcements,
    notifications) keep using ``"all"`` and are not routed through here."""
    pid = str(panel_id or "").strip()
    if pid.lower() in _NO_PANEL_IDS:
        return NO_PANEL_CHANNEL
    return f"panel_{pid}"


class PushBroadcaster:
    """WebSocket push broadcaster for real-time UI updates.

    Clients subscribe to a named channel ("all" is global; "panel_{id}" targets
    a specific touch panel).  The caller can broadcast_to_panel() to send an
    event exclusively to one panel's channel, bypassing the global "all" fan-out.

    User-scoped broadcasts: pass user_id= to broadcast() so that messages are
    only delivered to connections owned by that user, preventing cross-user
    data leakage.  Panel and anonymous connections have no user mapping and so
    NEVER receive user-scoped broadcasts; panel-targeted delivery uses
    broadcast_to_panel (or an unscoped broadcast).
    """

    def __init__(self):
        self._connections: Dict[str, Set[WebSocket]] = {}
        # Track which panel_id owns each WebSocket for disconnect cleanup.
        self._ws_panels: Dict[WebSocket, str] = {}
        # Map WebSocket → user_id for scoped delivery (None = panel/anonymous).
        self._ws_users: Dict[WebSocket, str | None] = {}
        self._sequence = 0
        # Per-panel liveness book-keeping (epoch seconds) for ``panel_push_status``
        # and the deaf-panel warning. Bounded by the number of distinct panel ids.
        self._panel_connected_at: Dict[str, float] = {}
        self._panel_disconnected_at: Dict[str, float] = {}
        self._deaf_warned_at: Dict[str, float] = {}
        self._deaf_errored_at: Dict[str, float] = {}
        # Voice turns that found the panel with ZERO subscribers since it last had one
        # (reset on connect). A counter, not an alert: ``panel_push_status`` serves it.
        self._deaf_turns: Dict[str, int] = {}
        # Epoch seconds this process started: the deaf clock for a panel never seen connecting.
        self._started_at = time.time()
        self._panel_acked_at: Dict[str, float] = {}

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
        self._panel_connected_at[panel_id] = time.time()
        self._deaf_turns.pop(panel_id, None)
        self._deaf_errored_at.pop(panel_id, None)
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
            self._panel_disconnected_at[panel_id] = time.time()
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
        excluded from scoped sends; unscoped broadcasts still reach them.

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
        # Snapshot (socket, owner) together: a send is a suspension point, and a socket
        # disconnected mid-fan-out must keep the owner it had when the fan-out began.
        for ws, ws_user in [(w, self._ws_users.get(w)) for w in list(self._connections[channel])]:
            # A user-scoped message goes ONLY to that user's own sockets. Unmapped
            # sockets (panels/kiosks on "all", anonymous) are skipped too: they are
            # shared screens and must not see one member's private events.
            if user_id is not None and ws_user != user_id:
                continue
            try:
                await ws.send_json(message)
                delivered += 1
            except Exception:
                dead.add(ws)

        for ws in dead:
            self.disconnect(ws, channel)

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

    def panel_subscriber_count(self, panel_id: str) -> int:
        """Live sockets subscribed to this panel's dedicated channel."""
        return len(self._connections.get(f"panel_{panel_id}", ()))

    def deaf_for_s(self, panel_id: str, *, wall_now: Optional[float] = None) -> Optional[float]:
        """Seconds this panel has had zero subscribers; None while one is connected.

        Measured from its last disconnect, or from process start when it has never connected to
        this process (the push-status ``last_connect_at: null`` signature of 2026-10-14)."""
        if self.panel_subscriber_count(panel_id) > 0:
            return None
        t = time.time() if wall_now is None else wall_now
        since = self._panel_disconnected_at.get(panel_id) or self._started_at
        return max(0.0, t - since)

    def panel_push_status(self, panel_id: str, *, wall_now: Optional[float] = None) -> dict:
        """Read-only liveness of one panel's push channel (no household data).

        ``last_connect_at`` / ``last_disconnect_at`` are epoch seconds (None if never seen
        since this process started). ``subscribers`` > 0 means a socket is registered; it
        cannot prove the browser is still reading it (a half-open socket is only reaped by
        the idle timeout), but 0 PROVES nothing can receive a ``voice:*`` event.

        ``deaf_turns`` counts voice turns that targeted the panel while it had no subscriber
        (since it last had one); ``deaf_for_s`` is how long it has been deaf (None when
        connected); ``deaf_alert`` is True once voice turns were lost AND the deafness outlasted
        ``DEAF_PANEL_ERROR_AFTER_S`` — what ``zoe_ground_truth.sh`` flags.
        """
        deaf_for = self.deaf_for_s(panel_id, wall_now=wall_now)
        turns = self._deaf_turns.get(panel_id, 0)
        return {
            "panel_id": panel_id,
            "subscribers": self.panel_subscriber_count(panel_id),
            "last_connect_at": self._panel_connected_at.get(panel_id),
            "last_disconnect_at": self._panel_disconnected_at.get(panel_id),
            "deaf_turns": turns,
            "deaf_for_s": None if deaf_for is None else int(deaf_for),
            "deaf_alert": bool(turns > 0 and deaf_for is not None and deaf_for >= DEAF_PANEL_ERROR_AFTER_S),
        }

    def note_panel_ack(self, panel_id: Optional[str], *, now: Optional[float] = None) -> None:
        """Record that this panel just acknowledged a UI action (proof the page is alive)."""
        pid = str(panel_id or "").strip()
        if pid:
            self._panel_acked_at[pid] = time.time() if now is None else now

    def panel_live(self, panel_id: Optional[str], *, within_s: float = PANEL_LIVE_WINDOW_S,
                   now: Optional[float] = None) -> bool:
        """Can this panel plausibly SEE something pushed to it right now?

        True when it has a live push subscriber, or acked a UI action within ``within_s``
        (the page is alive even if its socket is mid-reconnect). Callers gate any
        "check the panel / tap Done" line on this and otherwise say the result aloud —
        while the wall panel was deaf (2026-10-12) the voice path kept telling the owner
        to look at a form that never showed. A blank/unknown id is not live.
        """
        pid = str(panel_id or "").strip()
        if not pid:
            return False
        if self.panel_subscriber_count(pid) > 0:
            return True
        acked = self._panel_acked_at.get(pid)
        t = time.time() if now is None else now
        return acked is not None and 0 <= t - acked <= within_s

    def warn_if_panel_deaf(self, panel_id: Optional[str], *, now: Optional[float] = None,
                           wall_now: Optional[float] = None) -> bool:
        """Log ONE WARNING when a voice turn targets a panel with zero push subscribers.

        Every ``voice:*`` event and ``show_card`` reaches the wall panel only over its
        ``/ws/push`` socket, and ``broadcast``'s delivered count used to be ignored — so a
        deaf panel (2026-10-12) looked exactly like a healthy one from the server side.
        Rate-limited per panel (``DEAF_PANEL_WARN_INTERVAL_S``, i.e. one per turn). Returns True
        when it warned.

        A panel that stays deaf past ``DEAF_PANEL_ERROR_AFTER_S`` while turns keep arriving also
        logs an ERROR (once per ``DEAF_PANEL_ERROR_INTERVAL_S``): by then it is not a reconnect
        blip. Every deaf turn (one per limiter window) is counted in ``panel_push_status``.
        ``now`` is the limiter clock (monotonic), ``wall_now`` the epoch clock the deaf duration
        is measured on.
        Never raises: it runs on the voice hot path.
        """
        try:
            pid = str(panel_id or "").strip()
            if not pid or pid.lower() in {"unknown", "none", "default"}:
                return False
            if self.panel_subscriber_count(pid) > 0:
                return False
            t = time.monotonic() if now is None else now
            last = self._deaf_warned_at.get(pid)
            if last is not None and t - last < DEAF_PANEL_WARN_INTERVAL_S:
                return False   # same turn (several entry points call this): counted/alerted once below
            self._deaf_warned_at[pid] = t
            self._deaf_turns[pid] = self._deaf_turns.get(pid, 0) + 1
            deaf_for = self.deaf_for_s(pid, wall_now=wall_now)
            if deaf_for is not None and deaf_for >= DEAF_PANEL_ERROR_AFTER_S:
                last_err = self._deaf_errored_at.get(pid)
                if last_err is None or t - last_err >= DEAF_PANEL_ERROR_INTERVAL_S:
                    self._deaf_errored_at[pid] = t
                    logger.error(
                        "PANEL DEAF: %s has had ZERO push subscribers for %d min; %d voice turn(s) (this one "
                        "included) were NOT shown on the panel. The kiosk page's /ws/push reconnect is not "
                        "recovering (a page reload restores it). Check GET /api/panels/%s/push-status",
                        pid, int(deaf_for // 60), self._deaf_turns[pid], pid,
                    )
            disconnected = self._panel_disconnected_at.get(pid)
            ago = f"{int(time.time() - disconnected)}s ago" if disconnected else "never since this process started"
            logger.warning(
                "voice turn for panel %s but its push channel has ZERO subscribers (last disconnect %s): "
                "the panel will not see this turn's text/cards. Check GET /api/panels/%s/push-status",
                pid, ago, pid,
            )
            return True
        except Exception:
            return False

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


def panel_live(panel_id: Optional[str]) -> bool:
    """Module-level convenience for ``broadcaster.panel_live`` (see there). Never raises."""
    try:
        return broadcaster.panel_live(panel_id)
    except Exception:
        return False
