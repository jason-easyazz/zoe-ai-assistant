"""Leaving catch: you walked out without something on your take-with-you list.

It only ever OFFERS. Its manifest declares no actions, so the kernel would refuse one.
"""
from __future__ import annotations

from ..events import Event, Kind
from ..provenance import erased_request, still_live, unresolved_person

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
            for person, items in list(self._undone.items()):
                self._undone[person] = {k: s for k, s in items.items() if not erased_request(ev, s, person)}
                if not self._undone[person]:
                    self._undone.pop(person, None)
            if unresolved_person(ev, ctx):     # possibly anyone: keep only still-readable sources
                keep = still_live(ctx, self.manifest["reads"],
                                  {s for items in self._undone.values() for s in items.values()})
                self._undone = {p: kept for p, items in self._undone.items()
                                if (kept := {k: s for k, s in items.items() if s in keep})}
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
