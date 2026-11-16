"""Kernel-side drivers for the brain's actions: three tiny stores, rule-free on purpose.

These run in the KERNEL process (registered on its ``Dispatcher``). The brain never imports
this module. Each store is as dumb as production's are: it checks nothing, so every rule shown
in the tests is the kernel's. Each action declares its ``params`` (the tool schema is derived
from them) and is reversible. ``asked`` is a ``from: turn`` parameter: the adapter fills it from
the turn the trusted voice surface recorded; the model never sees it. The dispatcher overwrites
it with the single authorised cause before any driver runs. Multiple causes are refused for
actions using this scalar provenance, so scoped forget cannot miss another cited request.
Request sequences are global: erasure follows them across every owner's rows.
"""
from __future__ import annotations

from ..events import Event, Kind
from ..provenance import erased, unresolved_person
from ..spine import Refused

ASKED = {"type": "integer", "required": True, "from": "turn",
         "description": "the single authorised request (set by the kernel)"}
#: the range an absolute ``due_at`` (UTC epoch SECONDS) must fall in: 2000-01-01 .. 2100-01-01. Not a policy on how far ahead a
#: reminder may be; a units guard: a millisecond timestamp (13 digits) or a negative number is refused, never stored as a date.
DUE_MIN, DUE_MAX = 946_684_800, 4_102_444_800


def _register_forget(kernel, name, store):
    """Trusted pre-erasure purge plus independent read-back, across request owners."""
    def selected(person, events):
        gone = None if events is None else {e.seq for e in events}
        if isinstance(store, Alarms):
            return [(owner, row) for owner, row in store.at.items()
                    if (owner == person if gone is None else row['asked'] in gone)]
        return [(owner, row) for owner, rows in store.rows.items() for row in rows
                if (owner == person if gone is None else row['asked'] in gone)]

    def purge(person, events):
        for owner, row in selected(person, events):
            if isinstance(store, Alarms):
                store.at.pop(owner, None)
            else:
                store.rows[owner].remove(row)
                if not store.rows[owner]:
                    store.rows.pop(owner)

    kernel.register_store_purge(name, purge, lambda p, e: not selected(p, e))


class Lists:
    def __init__(self) -> None:
        self.rows: dict[str, list[dict]] = {}              # person -> [{list, item, asked}]

    def register(self, kernel) -> "Lists":
        _register_forget(kernel, 'brain-lists', self)
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
        return {"provenance": 1, "rows": [dict(r) for r in self.rows.get(person, [])]}

    def put_back(self, person, args, before):
        self.rows[person] = [dict(r) for r in before["rows"]]


class Reminders:
    def __init__(self, clock) -> None:
        self.clock, self.rows = clock, {}                    # person -> [{note, due (int, UTC epoch s), asked}]

    def register(self, kernel) -> "Reminders":
        _register_forget(kernel, 'brain-reminders', self)
        kernel.actions.register(
            "remind.set", self.add, self.added, needs_accept=False, snapshot=self.snapshot,
            undo=self.put_back, doc="Set a reminder for the person",
            params={"note": {"type": "string", "required": True, "description": "what to remind"},
                    "in_minutes": {"type": "integer", "required": False, "one_of": "when", "minimum": 1, "maximum": 10080,
                                   "description": "minutes from now as a whole number "
                                                  "(half an hour is 30, 2 hours is 120)"},
                    # exactly one of in_minutes / due_at. due_at is a CALLER's (zoe-data's shadow, which already holds the absolute
                    # instant), never the model's: the brain bridge hides every ``from`` param. A due_at in the past is accepted
                    # and stored as given (this driver records, it does not fire; an overdue reminder is the store's to handle).
                    "due_at": {"type": "integer", "required": False, "one_of": "when", "from": "caller",
                               "minimum": DUE_MIN, "maximum": DUE_MAX, "description": "the due instant, UTC epoch seconds"},
                    "asked": ASKED})
        return self

    def add(self, person, args):
        due = args["due_at"] if "due_at" in args else int(self.clock()) + 60 * args["in_minutes"]
        self.rows.setdefault(person, []).append({"note": args["note"], "due": due, "asked": args["asked"]})

    def added(self, person, args, result):
        """The read-back: the note and the STORED due instant (``due_at``, integer UTC epoch seconds), however it was set."""
        for r in self.rows.get(person, []):
            if r["asked"] == args["asked"] and r["note"] == args["note"]:
                return {"note": args["note"], "due_at": r["due"]}
        return None

    def snapshot(self, person, args):
        return {"provenance": 1, "rows": [dict(r) for r in self.rows.get(person, [])]}

    def put_back(self, person, args, before):
        self.rows[person] = [dict(r) for r in before["rows"]]


class Alarms:
    def __init__(self) -> None:
        self.at: dict[str, dict] = {}                        # person -> {time, asked}

    def register(self, kernel) -> "Alarms":
        _register_forget(kernel, 'brain-alarms', self)
        kernel.actions.register(
            "alarm.set", self.do, self.verify, needs_accept=False, snapshot=self.snapshot,
            undo=self.put_back, doc="Set the person's alarm time",
            params={"time": {"type": "string", "required": True, "max": 5,
                             "pattern": r"([01]\d|2[0-3]):[0-5]\d",
                             "description": "24-hour HH:MM with two digits each, such as 07:00 or 18:30"}, "asked": ASKED})
        return self

    def do(self, person, args):
        self.at[person] = {"time": args["time"], "asked": args["asked"]}

    def verify(self, person, args, result):
        return {"alarm": args["time"]} if self.at.get(person, {}).get("time") == args["time"] else None

    def snapshot(self, person, args):
        return {"provenance": 1, "alarm": self.at.get(person)}

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
        if unresolved_person(ev, ctx):
            # Its own reads cannot vouch for any row: never guess whose rows these are. Not
            # acknowledged, so it is retried; registered stores were already swept by the kernel.
            raise Refused('purge unresolved')
        for store in self.stores:
            if isinstance(store, Alarms):
                for person in erased(ev, ctx, ((p, p, (row["asked"],)) for p, row in store.at.items()),
                                     kinds=self.manifest["reads"]):
                    del store.at[person]
            else:
                gone = erased(ev, ctx, (((p, i), p, (r["asked"],)) for p, rows in store.rows.items()
                                        for i, r in enumerate(rows)), kinds=self.manifest["reads"])
                for person, rows in list(store.rows.items()):
                    keep = [r for i, r in enumerate(rows) if (person, i) not in gone]
                    store.rows[person] = keep
                    if not keep:
                        store.rows.pop(person, None)


# the brain's manifest: the ceiling on what it may ask the kernel to do
BRAIN = {"skill": "brain", "reads": {"utterance": "private"}, "emits": [],
         "acts": ["list.add", "remind.set", "alarm.set"]}
