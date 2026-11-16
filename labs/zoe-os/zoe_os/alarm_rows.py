"""The kernel's ALARM model: several alarms per person, as durable rows (``rows.DurableRows``), with production's rules.

Production read, never imported: ``services/zoe-data/alarms.py`` (schedule maths, ring lifecycle, ``can_see`` / ``can_edit`` /
``can_silence``), ``routers/alarms.py``, ``alarm_voice.py``. ``docs/architecture/zoe-os.md`` section 13 maps each rule.

A row: ``id, owner, by, label, time`` (HH:MM), ``repeat`` (once | daily | weekdays | weekends | days:1,3,5), ``date`` (a one-off's
day), ``snooze_minutes``, ``enabled``, and the ring bookkeeping ``after`` / ``snoozed_until`` / ``snooze_of`` / ``snooze_count``.
Whether it is ringing NOW is ``schedule.alarm_state`` (pure), where production's ring engine writes a ``state`` column.

Actions (all reversible, all read back): ``alarm.set`` (today's ``{time}`` still works: it now ADDS an alarm, as production does,
instead of replacing the one the lab driver kept), ``alarm.edit``, ``alarm.toggle``, ``alarm.delete``, ``alarm.snooze``,
``alarm.dismiss``. Who may: the owner (``alarms.can_edit`` for a personal alarm), and a CARER (owner rule 2026-11-16: a guardian on
the roster of a child or of an adult in care) for the person they care for, except an alarm its owner made ``private``.
Organising never includes forget, undo or explain; forgetting on someone's behalf is a separate named power, today offered only to
a child's guardian (``capability.guards``), to be replaced by memory care. An alarm owned by a DEVICE or SHARED principal (production
writes the owner ``__household__`` for one set at the wall panel) and the panel's right to silence any ringing alarm are NOT
expressible: the kernel acts only for a person.
"""
from __future__ import annotations

from datetime import date

from zoneinfo import ZoneInfo

from . import schedule as sch
from .brain.drivers import ASKED
from .rows import DurableRows

MAX_PER_OWNER, LABEL_MAX, MAX_SNOOZES = 30, 60, 8          # alarms.MAX_ALARMS_PER_OWNER, LABEL_MAX, MAX_SNOOZES
SNOOZE_RANGE, DEFAULT_SNOOZE_MIN = (1, 30), 9              # alarms.SNOOZE_RANGE, DEFAULT_SNOOZE_MIN
ID = {"type": "string", "required": True, "max": 64, "description": "which alarm"}
OPT = lambda kind, desc, **kw: {"type": kind, "required": False, "description": desc, **kw}  # noqa: E731
TIME = {"type": "string", "required": True, "max": 5, "pattern": sch.HHMM,
        "description": "24-hour HH:MM with two digits each, such as 07:00 or 18:30"}
FIELDS = {"repeat": OPT("string", "once, daily, weekdays, weekends, or days:1,3,5 (Monday is 1)", max=40),
          "date": OPT("string", "a one-off's day, YYYY-MM-DD", pattern=sch.DAY, max=10),
          "label": OPT("string", "a name for it", max=LABEL_MAX),
          "snooze_minutes": OPT("integer", "how long a snooze lasts", minimum=SNOOZE_RANGE[0], maximum=SNOOZE_RANGE[1]),
          "private": OPT("integer", "1: only its owner sees and manages it (not a carer); owner only", minimum=0, maximum=1)}
ON = OPT("integer", "1 on, 0 off", minimum=0, maximum=1)


class DurableAlarms(DurableRows):
    prefix, creates = "alm", "alarm.set"
    actions = ("alarm.set", "alarm.edit", "alarm.toggle", "alarm.delete", "alarm.snooze", "alarm.dismiss")
    removes = ("alarm.delete",)

    def __init__(self, spine, clock, grants=None, *, tz: str = "UTC") -> None:
        super().__init__(spine, clock)
        self.grants, self.tz = grants, ZoneInfo(tz)

    def _carer(self, adult: str, person: str) -> bool:
        people = getattr(self.grants, "people", {}) or {}
        who, by = people.get(person), people.get(adult)
        # both STATED: a least-access person (no role stated) is nobody's to manage, and a least-access carer cares for no one
        return who is not None and by is not None and by.role == "adult" and who.role in ("adult", "child") \
            and adult in who.guardians

    def _may_see(self, person: str, row: dict) -> bool:
        return row["owner"] == person or (not row.get("private") and self._carer(person, row["owner"]))

    def summary(self, row: dict) -> dict:
        return {"alarm": row["time"]}           # the proof key the shadow client compares (os_shadow.VERBS alarm_create)

    def state(self, row: dict, now: float | None = None):
        return sch.alarm_state(row, self.clock() if now is None else now, self.tz)

    def visible_to(self, person: str) -> list[dict]:
        """``alarms.can_see`` for a member: their own (alarms of a device or shared principal are not modelled). ``enabled`` as production shows it: a
        one-off that has rung is off."""
        out = []
        for _, r in sorted(self.rows.items()):
            if self._may_see(person, r):
                state, _occ, nxt = self.state(r)
                out.append(r | {"state": state, "next_fire": nxt, "enabled": int(bool(r["enabled"]) and (nxt is not None or state != "idle"))})
        return out

    # ---- checks ----------------------------------------------------------------------------------------------------------------
    def _plan(self, row: dict, now: float) -> tuple[dict, str]:
        """The schedule a write lands on (alarms._schedule): a one-off with no day takes the next such time; one in the past is refused."""
        rule = sch.alarm_repeat(row["repeat"])
        if rule is None:
            return row, "That is not a repeat I can use."
        if row["date"] is not None:                      # the shape check lets "2030-02-30" through: refuse it, never raise
            try:
                date.fromisoformat(row["date"])
            except (TypeError, ValueError):
                return row, "That is not a day I can use."
        row = row | {"repeat": rule, "date": row["date"] if rule == "once" else None}
        if rule == "once" and row["enabled"]:
            row["date"] = row["date"] or sch.next_once_date(row["time"], now, self.tz)
            if sch.alarm_occurrence(row["time"], rule, row["date"], now, self.tz) is None:
                return row, "That time has already passed."
        return row, ""

    def _check_set(self, person, args, caused_by):
        if sum(1 for r in self.rows.values() if r["owner"] == person) >= MAX_PER_OWNER:
            return "That is the most alarms I can keep."
        return self._plan(self._new(person, args), self.clock())[1]

    def _mine(self, person, args) -> str:
        row = self.rows.get(args["id"])
        return "" if row is not None and self._may_see(person, row) else "no such alarm"     # alarms.can_edit; a carer, not private

    def _check_edit(self, person, args, caused_by):
        return self._mine(person, args) or self._edited(person, args)[1]

    def _check_snooze(self, person, args, caused_by):
        why = self._mine(person, args)
        if why:
            return why
        row = self.rows[args["id"]]
        if self.state(row)[0] != "ringing":
            return "That alarm is not ringing."
        if self._count(row, self.state(row)[1]) >= MAX_SNOOZES:
            return "That alarm has been snoozed as much as it can be."
        return ""

    def _check_mine(self, person, args, caused_by):
        return self._mine(person, args)

    # ---- writes ----------------------------------------------------------------------------------------------------------------
    def _new(self, person, args) -> dict:
        return {"owner": person, "by": person, "label": " ".join(str(args.get("label", "")).split())[:LABEL_MAX],
                "time": args["time"], "repeat": args.get("repeat", "once"), "date": args.get("date"),
                "snooze_minutes": args.get("snooze_minutes", DEFAULT_SNOOZE_MIN), "enabled": 1, "after": self.clock(),
                "snoozed_until": None, "snooze_of": None, "snooze_count": 0, "private": args.get("private", 0)}

    def _set(self, person, args):
        rid = self.fresh(args)
        row, _ = self._plan(self._new(person, args), self.clock())
        self.rows[rid] = row | {"id": rid}
        return rid, dict(self.rows[rid])

    def _edited(self, person, args) -> tuple[dict, str]:
        """alarms.update_alarm: a changed time or repeat re-picks a one-off's day unless one is given; a spent one-off turned back on
        rings at the NEXT such time; every edit lands idle (a ring in progress stops)."""
        row, now = self.rows[args["id"]], self.clock()
        new = row | {k: args[k] for k in ("time", "repeat", "date", "snooze_minutes") if k in args}
        if "label" in args:
            new["label"] = " ".join(args["label"].split())[:LABEL_MAX]
        if "enabled" in args:
            new["enabled"] = args["enabled"]
        if "private" in args and person == row["owner"]:       # only the owner says who else may see it (a carer's change is dropped)
            new["private"] = args["private"]
        if new["repeat"] == "once" and (set(args) & {"time", "repeat"}) and "date" not in args:
            new["date"] = None
        landed = {"after": now, "snoozed_until": None, "snooze_of": None, "snooze_count": 0}
        if new["repeat"] == "once" and new["date"] and "date" not in args:
            try:
                spent = new["enabled"] and sch.alarm_occurrence(new["time"], "once", new["date"], now, self.tz) is None
            except ValueError:
                spent = False                                # an unusable stored day: let _plan refuse it
            if spent:
                # Only an edit that asks for a new ring re-picks the day: a changed time or repeat (handled above) or an
                # explicit switch back on. A label or snooze-length edit leaves a spent one-off spent.
                if not args.get("enabled"):
                    return new | landed, ""
                new["date"] = None
        new, why = self._plan(new, now)
        return new | landed, why

    def _edit(self, person, args):
        new, _ = self._edited(person, args)
        self.rows[new["id"]] = new
        return new["id"], dict(new)

    def _delete(self, person, args):
        self.rows.pop(args["id"], None)
        return args["id"], None

    @staticmethod
    def _count(row, occ) -> int:
        return row["snooze_count"] if row["snooze_of"] == occ else 0      # a fresh occurrence starts its own count

    def _snooze(self, person, args):
        row, now = self.rows[args["id"]], self.clock()
        _state, occ, _ = self.state(row, now)
        mins = args.get("minutes") or row["snooze_minutes"]
        new = row | {"snoozed_until": now + 60 * mins, "snooze_of": occ, "snooze_count": self._count(row, occ) + 1,
                     "after": max(row["after"], occ)}
        self.rows[new["id"]] = new
        return new["id"], dict(new)

    def _dismiss(self, person, args):
        """Ringing or snoozed -> idle for this occurrence (the next stays). An idle alarm is left alone, as production does."""
        row, now = self.rows[args["id"]], self.clock()
        state, occ, _ = self.state(row, now)
        if state in ("ringing", "snoozed"):
            row = row | {"snoozed_until": None, "snooze_of": None, "snooze_count": 0, "after": max(row["after"], occ)}
            self.rows[row["id"]] = row
        return row["id"], dict(row)

    def register(self, kernel) -> "DurableAlarms":
        self.register_all(kernel, {
            "alarm.set": (self._set, self._check_set, "Set an alarm for the person", {"time": TIME, **FIELDS, "asked": ASKED}),
            "alarm.edit": (self._edit, self._check_edit, "Change one of the person's alarms",
                           {"id": ID, "time": TIME | {"required": False}, **FIELDS, "enabled": ON, "asked": ASKED}),
            "alarm.toggle": (self._edit, self._check_edit, "Turn one of the person's alarms on or off",
                             {"id": ID, "enabled": ON | {"required": True}, "asked": ASKED}),
            "alarm.delete": (self._delete, self._check_mine, "Delete one of the person's alarms", {"id": ID, "asked": ASKED}),
            "alarm.snooze": (self._snooze, self._check_snooze, "Snooze the person's ringing alarm",
                             {"id": ID, "minutes": OPT("integer", "how long", minimum=SNOOZE_RANGE[0], maximum=SNOOZE_RANGE[1]),
                              "asked": ASKED}),
            "alarm.dismiss": (self._dismiss, self._check_mine, "Turn off the person's ringing alarm", {"id": ID, "asked": ASKED}),
        })
        return self
