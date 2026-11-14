"""alarm_push - tell the wall panels the moment the RINGING set changes (``ZOE_ALARMS_RINGING_PUSH``, default on; needs ``ZOE_ALARMS``).

A push only says that something changed: ``{"type": "alarms_ringing_changed", "data": {"seq": n}}``. No label, no id, no detail: the
panel re-reads ``GET /api/alarms/ringing`` through its authenticated route, so who may see what stays decided in ONE place
(``routers/alarms.ringing_now``). The panel's poll stays as the safety net (``js/touch-alarms.js``).

Audience: only sockets that proved they are a PANEL (``push.PushBroadcaster.connect_panel``). That is the principal the ringing route
answers for every ringing alarm (``alarms.can_silence`` is true for ``Viewer(panel=True)``), so a panel learns nothing it would not
learn from its next poll. A member's own socket, a browser on ``all`` and an admin that joined ``panel_<id>`` by name get nothing.

A beat every ``BEAT_S`` (``alarms_ringing_beat`` with the current seq) lets the panel prove the socket still DELIVERS broadcasts (a
pong proves only that it answers) and catch a missed change by its seq; no beat for a few seconds = the panel polls fast again.
The ``connected`` hello carries ``alarms_ringing_seq`` while this is live: the capability the panel needs before it slows its poll.
"""
from __future__ import annotations

import asyncio
import logging

import alarms
from typed_env import flag

logger = logging.getLogger(__name__)

BEAT_S = 2.0
# What changes the ringing set. ``missed`` never does (a missed occurrence or snooze was not ringing).
RING_SET_EVENTS = frozenset({"rang", "snoozed", "dismissed", "auto_stopped", "deleted"})

_seq = 0
_registered = False


def enabled() -> bool:
    return _registered and alarms.enabled() and bool(flag("ZOE_ALARMS_RINGING_PUSH"))


def hello_fields() -> dict:
    """Extra fields for a panel socket's ``connected`` frame: the capability (and the seq baseline), or nothing."""
    return {"alarms_ringing_seq": _seq} if enabled() else {}


def _changes_ring_set(event: str, alarm: dict) -> bool:
    # An edit stops a ring in progress (update_alarm lands on idle); the hook gets the row as it was before the edit.
    return event in RING_SET_EVENTS or (event == "updated" and alarm.get("state") == "ringing")


async def _send(event_type: str) -> tuple[int, int]:
    from push import broadcaster

    return await broadcaster.broadcast_to_panel_sockets(event_type, {"seq": _seq})


async def on_event(event: str, alarm: dict) -> None:
    """``alarms.HOOKS`` listener. Never raises into the transition (``alarms._emit`` also guards it)."""
    global _seq
    if not enabled() or not _changes_ring_set(event, alarm or {}):
        return
    _seq += 1
    sent, audience = await _send("alarms_ringing_changed")
    logger.info("ALARM_RING_PUSH event=%s seq=%d sent=%d audience=%d", event, _seq, sent, audience)


async def beat_loop(interval_s: float = BEAT_S) -> None:
    while True:
        try:
            if enabled():
                await _send("alarms_ringing_beat")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - a missed beat only makes the panel poll fast for a while
            logger.warning("alarm push beat failed (%s)", type(exc).__name__)
        await asyncio.sleep(interval_s)


def register() -> None:
    global _registered
    if on_event not in alarms.HOOKS:
        alarms.HOOKS.insert(0, on_event)  # first: a slow listener (music stopping) must not delay the panel learning of the change
    _registered = True
