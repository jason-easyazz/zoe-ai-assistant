"""A fake reminder store: the "production table" the lab skill drives through kernel actions.

It is deliberately DUMB. It checks nothing: not who is asking, not whose reminder it is, not
the time of day. Every rule that matters is the kernel's, which is what the tests show.
"""
from __future__ import annotations

SKILL = "reminders"


class ReminderStore:
    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}

    def register(self, kernel) -> "ReminderStore":
        reg = kernel.actions.register
        reg("reminder.create", self.create, self.created, needs_accept=False,
            snapshot=self.snapshot, undo=self.put_back)
        reg("reminder.snooze", self.snooze, self.snoozed, snapshot=self.snapshot, undo=self.put_back)
        reg("reminder.ack", self.ack, lambda p, a, r: {"acknowledged": True}
            if self.rows.get(a["id"], {}).get("acknowledged") else None)
        reg("reminder.delete", self.delete, lambda p, a, r: {"deleted": a["id"]}
            if a["id"] not in self.rows else None, needs_accept=False)
        return self

    def create(self, person, args):
        self.rows[args["id"]] = {"owner": person, "note": args["note"], "due": args["at"],
                                 "acknowledged": False}

    def created(self, person, args, result):
        row = self.rows.get(args["id"])
        return {"id": args["id"]} if row and row["note"] == args["note"] else None

    def snooze(self, person, args):
        self.rows[args["id"]]["due"] = args["until"]

    def snoozed(self, person, args, result):
        return {"due": args["until"]} if self.rows.get(args["id"], {}).get("due") == args["until"] else None

    def ack(self, person, args):
        self.rows[args["id"]]["acknowledged"] = True

    def delete(self, person, args):
        self.rows.pop(args["id"], None)

    def snapshot(self, person, args):
        row = self.rows.get(args["id"])
        return {"row": dict(row) if row else None}

    def put_back(self, person, args, before):
        if before["row"] is None:
            self.rows.pop(args["id"], None)
        else:
            self.rows[args["id"]] = dict(before["row"])
