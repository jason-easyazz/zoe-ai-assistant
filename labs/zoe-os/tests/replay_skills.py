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
