"""Calendar heads-up: something early landed on tomorrow; offer to move the alarm.

Offer first. It acts only on the person's accept, and the kernel checks that for itself:
``alarm.move`` is refused unless the cause is that person's unused yes to this skill's offer.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ..events import Event, Kind

EARLY_HOUR = 8
LEAD = timedelta(minutes=45)


class CalendarHeadsUp:
    manifest = {
        "skill": "calendar-heads-up",
        "reads": {"calendar_change": "private", "offer_response": "private",
                  "action_receipt": "private"},
        "emits": ["offer", "claim"],
        "acts": ["alarm.move"],
    }

    def __init__(self) -> None:
        self._open: dict[str, tuple[str, str]] = {}     # offer_id -> (person, proposed alarm)

    def on_event(self, ev: Event, ctx) -> None:
        if ev.kind is Kind.FORGET:
            gone = ev.payload.get("subject")
            self._open = {k: v for k, v in self._open.items() if v[0] != gone}
        elif ev.kind is Kind.CALENDAR_CHANGE and ev.payload["change"] == "created":
            self._maybe_offer(ev, ctx)
        elif ev.kind is Kind.OFFER_RESPONSE and ev.payload["offer_id"] in self._open:
            person, alarm = self._open.pop(ev.payload["offer_id"])
            if ev.payload["response"] != "accepted" or ev.subject != person:
                return
            receipt = ctx.act("alarm.move", person, {"to": alarm}, caused_by=(ev.seq,))
            if receipt.verified:     # claim only what the receipt proves
                ctx.publish(Kind.CLAIM, subject=person, caused_by=(receipt.seq,),
                            payload={"action": "alarm.move", "receipt_seq": receipt.seq})

    def _maybe_offer(self, ev: Event, ctx) -> None:
        start = datetime.fromtimestamp(ev.payload["starts_at"], timezone.utc)
        today = datetime.fromtimestamp(ctx.now(), timezone.utc).date()
        if start.date() != today + timedelta(days=1) or start.hour >= EARLY_HOUR:
            return
        alarm = (start - LEAD).strftime("%H:%M")
        offer_id = f"headsup-{ev.seq}"
        self._open[offer_id] = (ev.subject, alarm)
        ctx.publish(Kind.OFFER, subject=ev.subject, caused_by=(ev.seq,),
                    payload={"offer_id": offer_id, "proposal": {"alarm": alarm},
                             "text": f"Tomorrow starts at {start:%H:%M}. Move your alarm to {alarm}?"})
