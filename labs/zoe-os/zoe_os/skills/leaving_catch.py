"""Leaving catch: you walked out without something on your take-with-you list.

It only ever OFFERS. Its manifest declares no actions, so the kernel would refuse one.
"""
from __future__ import annotations

from ..events import Event, Kind

MIN_CONFIDENCE = 0.8


class LeavingCatch:
    manifest = {
        "skill": "leaving-catch",
        "reads": {"routine_step": "private", "presence": "private"},
        "emits": ["offer"],
        "acts": [],
    }

    def __init__(self) -> None:
        self._undone: dict[str, dict[str, int]] = {}    # person -> item -> seq of the step

    def on_event(self, ev: Event, ctx) -> None:
        if ev.kind is Kind.FORGET:
            self._undone.pop(ev.payload.get("subject", ""), None)   # drop what we derived
        elif ev.kind is Kind.ROUTINE_STEP and ev.payload["routine"] == "take-with-you":
            items = self._undone.setdefault(ev.subject, {})
            if ev.payload["done"]:
                items.pop(ev.payload["item"], None)
            else:
                items[ev.payload["item"]] = ev.seq
        elif ev.kind is Kind.PRESENCE and ev.payload["state"] == "left":
            items = self._undone.get(ev.subject) or {}
            if not items or ev.payload["confidence"] < MIN_CONFIDENCE:
                return      # not sure enough that it was them: say nothing
            names = sorted(items)
            ctx.publish(Kind.OFFER, subject=ev.subject,
                        payload={"offer_id": f"leaving-{ev.seq}", "items": names,
                                 "text": "You left without: " + ", ".join(names)},
                        caused_by=(ev.seq, *sorted(items.values())))
            self._undone.pop(ev.subject, None)
