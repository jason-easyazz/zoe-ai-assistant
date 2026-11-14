"""Thin adapters: how today's modules would publish to the spine without being rewritten.

Each function turns the values ALREADY IN SCOPE at one production call site into one event.
Nothing here is imported by production code (labs contract). The wiring notes, with the
exact file and line for each call site, are in ``docs/architecture/zoe-os.md``.

Two rules every adapter obeys:
* IDS AND LABELS ONLY. No utterance, reply, title or message text crosses the bridge in
  stage 1; the spine carries "a reminder fired for this person", not what it said.
* NEVER IN THE WAY. ``Bridge.publish`` cannot raise and cannot wait: a full or broken spine
  costs the caller one dropped event and one counter increment, never a slower voice turn.
"""
from __future__ import annotations

from .events import Event, Kind, Visibility
from .spine import Spine


def turn_end(*, user_id: str, session_id: str, tool_names: tuple[str, ...], replay: bool) -> Event | None:
    """``brain_dispatch._tracked_stream`` finally-block: a brain turn finished."""
    if replay or not user_id:
        return None                 # a replay-gate turn is not a household event
    return Event(Kind.TURN_END, "bridge.brain", subject=user_id,
                 payload={"session": session_id, "tools": list(tool_names)})


def reminder_fired(*, user_id: str, item_id: str, pending_id: str) -> Event:
    """``proactive/triggers/reminders.py``, after ``fire_notification`` returns."""
    return Event(Kind.REMINDER_FIRED, "bridge.reminders", subject=user_id,
                 payload={"item_id": item_id, "pending_id": pending_id})


def calendar_changed(*, user_id: str, event_id: str, change: str, household_visible: bool,
                     starts_at: float | None = None) -> Event:
    """``calendar_service.create_event_record`` and the router's update / delete handlers."""
    if change not in ("created", "updated", "deleted"):
        raise ValueError(change)
    vis = Visibility.HOUSEHOLD if household_visible else Visibility.PRIVATE
    return Event(Kind.CALENDAR_CHANGE, "bridge.calendar", subject=user_id, visibility=vis,
                 payload={"event_id": event_id, "change": change, "starts_at": starts_at})


def presence_changed(*, member_id: str, panel_id: str, state: str, confidence: float) -> Event:
    """``voice_session.publish``: a voice session opened, was renewed or closed on a panel."""
    return Event(Kind.PRESENCE, "bridge.presence", subject=member_id,
                 payload={"state": state, "confidence": float(confidence), "panel": panel_id})


class Bridge:
    def __init__(self, spine: Spine | None) -> None:
        self.spine = spine
        self.published = self.dropped = 0

    def publish(self, event: Event | None) -> bool:
        if event is None or self.spine is None:
            return False
        try:
            self.spine.publish(event)
            self.published += 1
            return True
        except Exception:           # includes Refused: the bridge is never the caller's problem
            self.dropped += 1
            return False

    def forget(self, *, user_id: str, actor: str) -> bool:
        """``MemoryService.delete_user`` / ``memory_forgotten.add``: the spine forgets too.

        Unlike ``publish`` this is NOT best effort in production: a forget that cannot reach
        the spine must fail the forget loudly (see the bridge plan). The lab returns False.
        """
        if self.spine is None:
            return False
        try:
            self.spine.forget(user_id, actor=actor)
            return True
        except Exception:
            self.dropped += 1
            return False
