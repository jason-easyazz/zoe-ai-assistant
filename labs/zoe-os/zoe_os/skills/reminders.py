"""Reminders as a kernel skill: create, fire, snooze, acknowledge, forget.

The same job production's reminders do (read, not imported: ``services/zoe-data/routers/
reminders.py``, ``reminder_service.py``, ``proactive/triggers/reminders.py``), written against
the kernel and a fake store. What is NOT in this file is the point: no visibility filter, no
role check, no quiet hours, no "may she speak", no receipt bookkeeping, no claim guard. The
kernel does those. What stays here is the reminder logic itself: when one is due, firing it
once per schedule, and what a snooze means.

Inputs it reads (all private to their subject):
* ``utterance`` carrying an intent the voice adapter already parsed:
  ``{"text": ..., "intent": {"name": "reminder.create", "at": <unix time>, "note": ...}}``;
* ``timer_fired`` ticks from the clock adapter (household): time passing;
* ``offer_response`` to its own offers: ``accepted`` with ``choice`` "done" (default) or
  "snooze" (+ ``minutes``);
* its own ``action_receipt`` and ``offer`` events: ``restore(history)`` rebuilds a restarted
  skill's state from the log, so it keeps no private database (an acknowledgement is the one
  thing it cannot relearn: that receipt carries no arguments);
* ``forget`` notices: it deletes the person's reminders from the store and drops its state.
"""
from __future__ import annotations

from ..events import Event, Kind

SNOOZE_MINUTES = 10


class Reminders:
    manifest = {
        "skill": "reminders",
        "reads": {"utterance": "private", "timer_fired": "household", "offer_response": "private",
                  "action_receipt": "private", "offer": "private"},
        "emits": ["offer", "claim"],
        "acts": ["reminder.create", "reminder.snooze", "reminder.ack", "reminder.delete"],
    }

    def __init__(self) -> None:
        # reminder id -> {person, note, due, gen, receipt (seq of the create receipt), open}
        self._items: dict[str, dict] = {}
        self._offered: dict[str, int] = {}      # offer id -> reminder generation it was made for

    def restore(self, history) -> None:
        """Relearn from the log after a restart: receipts and own offers only. Acts on nothing."""
        for ev in history:
            if ev.kind is Kind.ACTION_RECEIPT:
                self._learn(ev)
            elif ev.kind is Kind.OFFER and ev.source == self.manifest["skill"]:
                self._offered[ev.payload["offer_id"]] = ev.payload.get("gen", 0)

    def on_event(self, ev: Event, ctx) -> None:
        if ev.kind is Kind.FORGET:
            self._forget(ev, ctx)
        elif ev.kind is Kind.UTTERANCE:
            intent = ev.payload.get("intent") or {}
            if intent.get("name") == "reminder.create" and f"r{ev.seq}" not in self._items:
                self._create(ev, intent, ctx)
        elif ev.kind is Kind.TIMER_FIRED:
            self._fire_due(ev, ctx)
        elif ev.kind is Kind.OFFER_RESPONSE:
            self._answered(ev, ctx)

    # ---- create -------------------------------------------------------------------------------
    def _create(self, ev: Event, intent: dict, ctx) -> None:
        rid = f"r{ev.seq}"
        receipt = ctx.act("reminder.create", ev.subject,
                          {"id": rid, "at": intent["at"], "note": intent["note"], "asked": ev.seq},
                          caused_by=(ev.seq,))
        if receipt.verified:                 # say "I've set that" only on a verified receipt
            self._items[rid] = {"person": ev.subject, "note": intent["note"], "due": intent["at"],
                                "gen": 0, "receipt": receipt.seq, "asked": ev.seq, "open": True}
            ctx.publish(Kind.CLAIM, subject=ev.subject, caused_by=(receipt.seq,),
                        payload={"action": "reminder.create", "receipt_seq": receipt.seq})

    def _learn(self, ev: Event) -> None:
        """Restore only: a reversible receipt carries its arguments (see ``Dispatcher.undo``)."""
        p = ev.payload
        if p.get("skill") != self.manifest["skill"] or not p.get("verified"):
            return
        args = (p.get("undo") or {}).get("args") or {}
        rid = args.get("id")
        if p["action"] == "reminder.create" and rid:
            self._items[rid] = {"person": ev.subject, "note": args["note"], "due": args["at"], "gen": 0,
                                "receipt": ev.seq, "asked": args["asked"], "open": True}
        elif rid in self._items and p["action"] == "reminder.snooze":
            self._items[rid] |= {"due": args["until"], "gen": self._items[rid]["gen"] + 1}

    # ---- fire: once per schedule, as an OFFER the kernel routes -------------------------------
    def _fire_due(self, tick: Event, ctx) -> None:
        for rid, item in sorted(self._items.items()):
            offer_id = f"reminder-{rid}-{item['gen']}"
            if not item["open"] or item["due"] > ctx.now() or offer_id in self._offered:
                continue
            self._offered[offer_id] = item["gen"]
            ctx.publish(Kind.OFFER, subject=item["person"], caused_by=(item["receipt"], tick.seq),
                        payload={"offer_id": offer_id, "reminder_id": rid, "gen": item["gen"],
                                 "text": "Reminder: " + item["note"]})

    def _answered(self, ev: Event, ctx) -> None:
        offer_id = ev.payload.get("offer_id", "")
        rid = offer_id.rsplit("-", 1)[0].removeprefix("reminder-")
        item = self._items.get(rid)
        if item is None or not item["open"] or ev.payload.get("response") != "accepted" \
                or self._offered.get(offer_id) != item["gen"]:
            return                           # not ours, already closed, or an answer to an old fire
        if ev.payload.get("choice") == "snooze":
            until = ctx.now() + 60 * int(ev.payload.get("minutes", SNOOZE_MINUTES))
            if ctx.act("reminder.snooze", item["person"], {"id": rid, "until": until},
                       caused_by=(ev.seq,)).verified:
                item |= {"due": until, "gen": item["gen"] + 1}
        elif ctx.act("reminder.ack", item["person"], {"id": rid}, caused_by=(ev.seq,)).verified:
            item["open"] = False

    # ---- forget ---------------------------------------------------------------------------------
    def _forget(self, notice: Event, ctx) -> None:
        """The kernel erased the events. The store is outside the kernel, so the skill erases
        there: everything of the person's, or (a scoped forget) the reminders whose request or
        create receipt is among the erased events."""
        gone = set(notice.payload.get("seqs", ()))
        scoped = "scope" in notice.payload
        for rid, item in sorted(self._items.items()):
            if item["person"] != notice.payload.get("subject"):
                continue
            if scoped and not ({item["receipt"], item["asked"]} & gone):
                continue
            ctx.act("reminder.delete", item["person"], {"id": rid}, caused_by=(notice.seq,))
            del self._items[rid]
        self._offered = {o: g for o, g in self._offered.items()
                         if o.rsplit("-", 1)[0].removeprefix("reminder-") in self._items}
