"""Changed versions of the toy skills, for the replay harness to compare against the originals."""
from __future__ import annotations

import dataclasses

from zoe_os.events import Kind
from zoe_os.skills.calendar_headsup import CalendarHeadsUp
from zoe_os.skills.leaving_catch import LeavingCatch


class EagerLeavingCatch(LeavingCatch):
    """A 'small tweak': it no longer waits to be sure who left."""

    def on_event(self, ev, ctx) -> None:
        if ev.kind is Kind.PRESENCE:
            ev = dataclasses.replace(ev, payload={**ev.payload, "confidence": 1.0})
        super().on_event(ev, ctx)


class PushyHeadsUp(CalendarHeadsUp):
    """A worse change: it moves the alarm as soon as it has offered, without the person's yes."""

    def _maybe_offer(self, ev, ctx) -> None:
        super()._maybe_offer(ev, ctx)
        for person, alarm in list(self._open.values()):
            ctx.act("alarm.move", person, {"to": alarm}, caused_by=(ev.seq,))


class Gossip(LeavingCatch):
    """A 'helpful' change: when someone leaves without something, it tells everyone else too."""

    def __init__(self) -> None:
        super().__init__()
        self._known: set[str] = set()

    def on_event(self, ev, ctx) -> None:
        if ev.subject:
            self._known.add(ev.subject)
        undone = sorted(self._undone.get(ev.subject) or {}) if ev.kind is Kind.PRESENCE else []
        super().on_event(ev, ctx)
        if undone and ev.payload.get("state") == "left":
            for other in sorted(self._known - {ev.subject}):
                ctx.publish(Kind.OFFER, subject=other, caused_by=(ev.seq,),
                            payload={"offer_id": f"gossip-{ev.seq}-{other}",
                                     "text": f"{ev.subject} left without: " + ", ".join(undone)})
