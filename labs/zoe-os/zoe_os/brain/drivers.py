"""Kernel-side drivers for the brain's actions: three tiny stores, rule-free on purpose.

These run in the KERNEL process (registered on its ``Dispatcher``). The brain never imports
this module. Each store is as dumb as production's are: it checks nothing, so every rule shown
in the tests is the kernel's. Each action declares its ``params`` (the tool schema is derived
from them) and is reversible. ``asked`` is a ``from: turn`` parameter: the adapter fills it from
the turn the trusted voice surface recorded; the model never sees it. It is only provenance for
a scoped forget, and a wrong value can at worst mis-scope a delete of the SAME person's row.
"""
from __future__ import annotations

from ..events import Event, Kind

ASKED = {"type": "integer", "required": True, "from": "turn",
         "description": "the utterance this came from (filled by the adapter)"}


class Lists:
    def __init__(self) -> None:
        self.rows: dict[str, list[dict]] = {}              # person -> [{list, item, asked}]

    def register(self, kernel) -> "Lists":
        kernel.actions.register(
            "list.add", self.add, self.added, needs_accept=False, snapshot=self.snapshot,
            undo=self.put_back, doc="Add an item to one of the person's lists",
            params={"item": {"type": "string", "required": True, "description": "what to add"},
                    "list": {"type": "string", "required": False,
                             "description": "which list; the shopping list if not said"},
                    "asked": ASKED})
        return self

    def add(self, person, args):
        self.rows.setdefault(person, []).append(
            {"list": args.get("list", "shopping"), "item": args["item"], "asked": args["asked"]})

    def added(self, person, args, result):
        want = {"list": args.get("list", "shopping"), "item": args["item"], "asked": args["asked"]}
        return {"item": args["item"]} if want in self.rows.get(person, []) else None

    def snapshot(self, person, args):
        return {"rows": [dict(r) for r in self.rows.get(person, [])]}

    def put_back(self, person, args, before):
        self.rows[person] = [dict(r) for r in before["rows"]]


class Reminders:
    def __init__(self, clock) -> None:
        self.clock, self.rows = clock, {}                    # person -> [{note, due, asked}]

    def register(self, kernel) -> "Reminders":
        kernel.actions.register(
            "remind.set", self.add, self.added, needs_accept=False, snapshot=self.snapshot,
            undo=self.put_back, doc="Set a reminder for the person",
            params={"note": {"type": "string", "required": True, "description": "what to remind"},
                    "in_minutes": {"type": "integer", "required": True,
                                   "description": "minutes from now"},
                    "asked": ASKED})
        return self

    def add(self, person, args):
        self.rows.setdefault(person, []).append(
            {"note": args["note"], "due": self.clock() + 60 * args["in_minutes"], "asked": args["asked"]})

    def added(self, person, args, result):
        return {"note": args["note"]} if any(
            r["asked"] == args["asked"] and r["note"] == args["note"] for r in self.rows.get(person, [])) else None

    def snapshot(self, person, args):
        return {"rows": [dict(r) for r in self.rows.get(person, [])]}

    def put_back(self, person, args, before):
        self.rows[person] = [dict(r) for r in before["rows"]]


class Alarms:
    def __init__(self) -> None:
        self.at: dict[str, dict] = {}                        # person -> {time, asked}

    def register(self, kernel) -> "Alarms":
        kernel.actions.register(
            "alarm.set", self.do, self.verify, needs_accept=False, snapshot=self.snapshot,
            undo=self.put_back, doc="Set the person's alarm time",
            params={"time": {"type": "string", "required": True, "max": 5,
                             "description": "24-hour HH:MM"}, "asked": ASKED})
        return self

    def do(self, person, args):
        self.at[person] = {"time": args["time"], "asked": args["asked"]}

    def verify(self, person, args, result):
        return {"alarm": args["time"]} if self.at.get(person, {}).get("time") == args["time"] else None

    def snapshot(self, person, args):
        return {"alarm": self.at.get(person)}

    def put_back(self, person, args, before):
        if before["alarm"] is None:
            self.at.pop(person, None)
        else:
            self.at[person] = dict(before["alarm"])


class Keeper:
    """The kernel's duty to the stores: when the kernel forgets, the stores forget (the effect
    goes with the event). A trusted kernel-side skill, not the brain. Pumped with the kernel."""

    manifest = {"skill": "keeper", "reads": {"offer_response": "private"}}

    def __init__(self, *stores) -> None:
        self.stores = stores

    def on_event(self, ev: Event, ctx) -> None:
        if ev.kind is not Kind.FORGET:
            return
        person, gone = ev.payload.get("subject"), set(ev.payload.get("seqs", ()))
        scoped = "scope" in ev.payload
        for store in self.stores:
            if isinstance(store, Alarms):
                row = store.at.get(person)
                if row and (not scoped or row["asked"] in gone):
                    del store.at[person]
            else:
                keep = [r for r in store.rows.get(person, []) if scoped and r["asked"] not in gone]
                store.rows[person] = keep
                if not keep:
                    store.rows.pop(person, None)


# the brain's manifest: the ceiling on what it may ask the kernel to do
BRAIN = {"skill": "brain", "reads": {"utterance": "private"}, "emits": [],
         "acts": ["list.add", "remind.set", "alarm.set"]}
