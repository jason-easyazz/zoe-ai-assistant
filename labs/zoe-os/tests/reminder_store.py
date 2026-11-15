"""A fake reminder store: the "production table" the lab skill drives through kernel actions.

It is deliberately DUMB about everything the kernel decides: who may ask, whose yes counts, the
time of day. It knows ONE thing the kernel cannot see, because the kernel never reads a store row:
whose row an id names. An action reaches only the rows of the person it is on behalf of. Someone
else's id behaves exactly like an id that does not exist (so ids cannot be confirmed across
members), and a snapshot never copies another person's row into a receipt. The erasure duty
(``purge``, driven by a live forget notice) is the one path that reaches every owner's rows.
"""
from __future__ import annotations

SKILL = "reminders"


class ReminderStore:
    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}

    def register(self, kernel) -> "ReminderStore":
        kernel.register_store_purge(SKILL, self.purge, self.purged)
        reg = kernel.actions.register
        reg("reminder.create", self.create, self.created, needs_accept=False,
            snapshot=self.snapshot, undo=self.put_back)
        reg("reminder.snooze", self.snooze, self.snoozed, snapshot=self.snapshot, undo=self.put_back)
        reg("reminder.ack", self.ack, lambda p, a, r: {"acknowledged": True}
            if (self._own(p, a["id"]) or {}).get("acknowledged") else None)
        reg("reminder.delete", self.delete, lambda p, a, r: {"deleted": a["id"]}
            if self._own(p, a["id"]) is None else None, needs_accept=False, purge_key=('id', 'r'),
            purge=self.purge_one)
        return self

    def _purge_ids(self, person, events):
        if events is None:
            return {rid for rid, row in self.rows.items() if row["owner"] == person}
        # Store keys derive from requests; reversible receipts also name the key.
        ids = {f"r{ev.seq}" for ev in events}
        ids.update(args["id"] for ev in events
                   if ev.payload.get("skill") == SKILL
                   and (args := (ev.payload.get("undo") or {}).get("args", {})).get("id"))
        return ids & self.rows.keys()

    def purge(self, person, events):
        for rid in self._purge_ids(person, events):
            self.rows.pop(rid, None)

    def purged(self, person, events):
        return not self._purge_ids(person, events)

    def _own(self, person, rid) -> dict | None:
        """The row ``rid`` names, if it is ``person``'s; None for a missing id AND for someone else's."""
        row = self.rows.get(rid)
        return row if row is not None and row.get("owner") == person else None

    def _mine(self, person, rid) -> dict:
        row = self._own(person, rid)
        if row is None:
            raise KeyError("no such reminder")      # the same failure for a missing id and a foreign one
        return row

    def create(self, person, args):
        if args["id"] in self.rows and self._own(person, args["id"]) is None:
            raise KeyError("no such reminder")      # never overwrite (and take over) another person's row
        self.rows[args["id"]] = {"owner": person, "note": args["note"], "due": args["at"],
                                 "acknowledged": False}

    def created(self, person, args, result):
        row = self._own(person, args["id"])
        return {"id": args["id"]} if row and row["note"] == args["note"] else None

    def snooze(self, person, args):
        self._mine(person, args["id"])["due"] = args["until"]

    def snoozed(self, person, args, result):
        return {"due": args["until"]} if (self._own(person, args["id"]) or {}).get("due") == args["until"] else None

    def ack(self, person, args):
        self._mine(person, args["id"])["acknowledged"] = True

    def delete(self, person, args):
        if self._own(person, args["id"]) is not None:
            del self.rows[args["id"]]

    def purge_one(self, subject, args):
        """The kernel's erasure duty for ONE id derived from an erased request, whoever owns the row (the action driver;
        ``purge`` above is the store sweep the kernel runs for a forget)."""
        self.rows.pop(args["id"], None)

    def snapshot(self, person, args):
        row = self._own(person, args["id"])
        return {"row": dict(row) if row else None}

    def put_back(self, person, args, before):
        if args["id"] in self.rows and self._own(person, args["id"]) is None:
            raise KeyError("no such reminder")      # an undo never touches another person's row
        if before["row"] is None:
            self.rows.pop(args["id"], None)
        else:
            self.rows[args["id"]] = dict(before["row"])
