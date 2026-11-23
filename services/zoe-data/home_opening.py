"""Unlocking or opening the house needs an ADULT (security fix, owner's rule; on by default, no flag).

A command that could OPEN the house - a lock or a cover (garage doors and blinds are covers), and any service on one but
closing, locking or arming it - is sent to Home Assistant only for:

  * a household member who is POSITIVELY an adult by their account record (``manner_block.is_adult``: a missing account, an
    unknown role, a child, a guest and a failed lookup are all NOT an adult), or
  * the panel while a VERIFIED VOICE SESSION is open on it (``voice_session.active_member``), and that member is an adult.
    WHICH panel a caller is comes from ``panel_principal.panel_of`` (device token, kiosk credential or registered address).

The shared panel with nobody identified, and a guest, are refused. Lock, close and arm stay open to everyone who may control
the house today. Every member-driven sender asks ``refused(...)`` right before it sends; a refusal sends nothing.

The Zoe OS kernel's home door (``ZOE_OS_HOME=kernel``) decides the same classes when it is on. It does NOT replace this
check: this one stays on whatever the flag says and runs first, the door (in ``home_control.send``) is asked only for a
command this has let through, and both must agree before anything is sent.
"""
from __future__ import annotations

import contextvars
import logging
from typing import Any, Mapping, Optional

logger = logging.getLogger(__name__)

REFUSAL = "Unlocking or opening that needs a grown-up I can recognise, so I haven't done it."
#: Home Assistant domains whose things open the house: locks and alarm panels; covers (garage doors, blinds) and valves
SECURE_DOMAINS = frozenset({"lock", "alarm_control_panel", "cover", "valve"})
#: the only services on those domains that do NOT open anything. Every other service - unlock, open, disarm, toggle, a set
#: position, a generic turn_on / turn_off, one this list has never heard of - counts as opening.
CLOSING = frozenset({"lock", "close", "close_cover", "close_cover_tilt", "close_valve", "stop_cover", "stop_cover_tilt",
                     "alarm_arm_away", "alarm_arm_home", "alarm_arm_night", "alarm_arm_vacation", "alarm_arm_custom_bypass"})
#: the caller of THIS turn (chat, voice, the brain's dispatch), noted where the turn's home-control verdict is recorded
_TURN: contextvars.ContextVar[Optional[Mapping[str, Any]]] = contextvars.ContextVar("zoe_home_opening_turn", default=None)


def note_turn(user: Optional[Mapping[str, Any]], request: Any = None) -> None:
    """Note this turn's caller and the request it came in on. Nothing is looked up here: WHICH panel the turn is from is
    resolved (``panel_principal.panel_of``) only if the turn asks to open something, and then once."""
    _TURN.set({"user": dict(user or {}), "request": request})


def _domains(entity_id: Any, data: Any) -> set:
    ids = [entity_id]
    extra = (data or {}).get("entity_id") if isinstance(data, Mapping) else None      # a service's own target overrides the path's
    ids += list(extra) if isinstance(extra, (list, tuple, set)) else [extra]
    return {str(i).partition(".")[0].strip().lower() for i in ids if i}


def opens(entity_id: Any, action: Any, data: Any = None) -> bool:
    """Could this command open the house? A secure domain, and any service but one that closes, locks or arms."""
    return bool(_domains(entity_id, data) & SECURE_DOMAINS) and str(action or "").strip().lower() not in CLOSING


async def _adult(user_id: Any) -> bool:
    try:
        import manner_block

        return bool(user_id) and bool(await manner_block.is_adult(str(user_id), quiet=True))     # quiet: no id in the log
    except Exception:  # noqa: BLE001 - not knowing is not an adult
        return False


async def may_open(user: Optional[Mapping[str, Any]], request: Any = None, turn: Optional[dict] = None) -> bool:
    """Is this caller an adult member, or a panel whose open verified voice session belongs to one? Never raises.
    With a ``request`` the panel is whatever ``panel_principal.panel_of`` proves it to be (kept on ``turn``, so a turn resolves
    it once); without one, the record's own ``panel_id``."""
    u = user or {}
    if u.get("unauthenticated_override"):      # a header-less caller promoted by a legacy setting is nobody, whatever its id
        u = {"user_id": "guest", "role": "guest", "panel_id": u.get("panel_id")}
    elif await _adult(u.get("user_id")):
        return True
    try:
        import panel_principal
        import voice_session

        if turn is not None and "panel" in turn:
            panel = turn["panel"]
        elif request is not None:
            panel = await panel_principal.panel_of(request, u)
            if turn is not None:
                turn["panel"] = panel
        else:
            panel = str(u.get("panel_id") or "").strip()
        if not panel:
            return False
        return await _adult(voice_session.active_member(panel))
    except Exception:  # noqa: BLE001 - no session to read is no session
        return False


async def refused(entity_id: Any, action: Any, data: Any = None, *, user: Any = "turn", request: Any = None,
                  via: str = "") -> bool:
    """True when this command would open the house and its caller may not. ``user``: the caller's record (with its
    ``request``, which says which panel it is), or (default) the caller of this turn. One log line, closed codes only: the lane, the domain and the service class, never a name or an id."""
    if not opens(entity_id, action, data):
        return False
    turn = _TURN.get() if user == "turn" else None
    if user == "turn":
        user, request = (turn or {}).get("user"), (turn or {}).get("request")
    if await may_open(user, request, turn):
        return False
    logger.warning("HOME_OPENING refused via=%s domain=%s", via or "-", ",".join(sorted(_domains(entity_id, data) & SECURE_DOMAINS)))
    return True
