"""The kernel's REMINDER model: everything production creates, as durable rows (``rows.DurableRows``), with production's rules.

Production read, never imported (``services/zoe-data``): ``reminder_service.py`` (the write path), ``routers/reminders.py`` (who may
do what), ``proactive/triggers/reminder_scan.py`` (when a row fires), ``reminder_recurrence.py`` (the repeat rule, mirrored in
``schedule.py``). ``docs/architecture/zoe-os.md`` section 13 maps every rule to its production line and lists what does not map.

A row: ``id, owner`` (who it is for), ``by`` (who set it), ``note``, ONE schedule form (``at`` | ``date``/``time`` | ``repeat`` +
``date`` anchor, optional ``until``), ``visibility`` (personal | family), ``active``, ``done``, ``snoozed_until``, ``after`` (nothing
at or before it fires), ``grace_at``. When it fires: ``schedule.reminder_next_fire``, the one pure function.

Actions (all reversible, all read back): ``remind.set`` (today's ``{note, in_minutes}`` still works), ``remind.snooze``,
``remind.done``, ``remind.cancel``, ``remind.edit``. Every refusal is decided BEFORE the write (the dispatcher's ``check``) with a
fixed reason and no content.
"""
from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from . import schedule as sch
from .brain.drivers import ASKED
from .capability import ADULT
from .rows import DurableRows

GRACE_MIN = 2                 # production reminder_scan._SAME_DAY_GRACE_MIN
SNOOZE_DEFAULT_MIN = 15       # production models.SnoozeBody.snooze_minutes
SNOOZE_MAX_MIN = 1440         # production has no bound; the kernel takes a day at most
VISIBILITY = r"personal|family"
ID = {"type": "string", "required": True, "max": 64, "description": "which reminder"}
OPT = lambda kind, desc, **kw: {"type": kind, "required": False, "description": desc, **kw}  # noqa: E731


class DurableReminders(DurableRows):
    prefix, creates = "rem", "remind.set"
    actions = ("remind.set", "remind.snooze", "remind.done", "remind.cancel", "remind.edit")

    def __init__(self, spine, clock, grants, *, tz: str = "UTC", default_time: str = "09:00") -> None:
        super().__init__(spine, clock)
        self.grants, self.tz = grants, ZoneInfo(tz)
        self.default_hm = sch.hm(default_time)
        if self.default_hm is None:
            raise ValueError("the household default reminder time is HH:MM")

    # ---- reads ------------------------------------------------------------------------------------------------------------------
    def summary(self, row: dict) -> dict:
        return {"note": row["note"]}            # the proof key the shadow and authority clients compare (os_shadow.VERBS)

    def next_fire(self, row: dict, after: float | None = None) -> float | None:
        return sch.reminder_next_fire(row, self.clock() if after is None else after, self.tz, self.default_hm)

    def visible_to(self, person: str) -> list[dict]:
        """Production's list filter, ``visibility = 'family' OR user_id = me`` (routers/reminders.py ``_visibility_filter_sql``),
        plus the setter of a row they set for someone else."""
        return [r | {"next_fire": self.next_fire(r)} for _, r in sorted(self.rows.items())
                if r["visibility"] == "family" or person in (r["owner"], r["by"])]

    # ---- who may ---------------------------------------------------------------------------------------------------------------
    def _guardian(self, adult: str, child: str) -> bool:
        who, by = self.grants.people.get(child), self.grants.people.get(adult)
        return who is not None and by is not None and by.role == ADULT and adult in who.guardians

    def _may(self, person: str, row: dict | None, what: str) -> str:
        if row is None:
            return "no such reminder"
        mine = person in (row["owner"], row["by"]) or self._guardian(person, row["owner"])
        if what == "edit":           # update / delete: own, or any member for a family reminder (routers/reminders.py:202-212, 293-301)
            return "" if mine or row["visibility"] == "family" else "no such reminder"
        if person == row["owner"]:   # snooze / done: the owner only (routers/reminders.py:334-335, 398-399)
            return ""
        return "only its owner may do that" if mine or row["visibility"] == "family" else "no such reminder"

    def _today(self) -> date:
        return datetime.fromtimestamp(self.clock(), self.tz).date()

    def _schedule(self, args: dict, now: float) -> tuple[dict, str]:
        """The write path's resolution (reminder_service.normalize_due_date / normalize_recurrence_fields): fields or a refusal."""
        if "in_minutes" in args:
            if set(args) & {"date", "time", "repeat", "until"}:
                return {}, "one schedule at a time"
            return {"at": now + 60 * args["in_minutes"]}, ""
        day, clock, repeat, until = args.get("date"), args.get("time"), args.get("repeat"), args.get("until")
        if not (day or clock or repeat):
            return {}, "a reminder needs a time or a day"
        try:
            d = date.fromisoformat(day) if day else None
            end = date.fromisoformat(until) if until else None
        except ValueError:
            return {}, "not a date I can use"
        if d is not None and d < self._today():
            return {}, "that day has already passed"                                 # reminder_service.py:57-63
        out = {"date": day, "time": clock}
        if repeat:
            rule = sch.parse_rrule(repeat)
            if rule is None:
                return {}, "not a repeat I can use"                                  # reminder_service.py:172-179
            h, m = sch.hm(clock) or self.default_hm
            first = sch.first_occurrence_date(rule, d or self._today(), h, m, datetime.fromtimestamp(now, self.tz), self.tz)
            if first is None or (end is not None and end < first):
                return {}, "that repeat has no upcoming time"                         # reminder_service.py:188-189
            return out | {"date": first.isoformat(), "repeat": sch.format_rrule(rule), "until": until}, ""
        if end is not None:
            return {}, "an end needs a repeat"
        if d is not None and clock and sch.local_at(d, *sch.hm(clock), self.tz) <= now:
            return {}, "that time has already passed"     # production stores it and it never fires (reminder_scan.py:306-322)
        if d is not None and not clock and sch.local_at(d, *self.default_hm, self.tz) <= now:
            out["grace_at"] = now + 60 * GRACE_MIN                                     # reminder_scan.py:309-313
        return out, ""

    # ---- the checks (before the write, recorded as refusals) -----------------------------------------------------------------
    def _check_set(self, person, args, caused_by):
        owner = args.get("for", person)
        if owner != person:
            if not self._guardian(person, owner):
                return "only a guardian may set one for someone else"
            if not any(owner in getattr(self.spine._get(s), "mentions", ()) for s in caused_by):
                return "the asking event must name who it is for"       # so that person's forget reaches it
        return self._schedule(args, self.clock())[1]

    def _check_row(self, what):
        def check(person, args, caused_by):
            return self._may(person, self.rows.get(args["id"]), what)
        return check

    def _check_edit(self, person, args, caused_by):
        why = self._may(person, self.rows.get(args["id"]), "edit")
        return why or (self._edited(person, args)[1] if set(args) & {"date", "time"} else "")

    # ---- the writes: (row id, what the row must now be) ----------------------------------------------------------------------
    def _set(self, person, args):
        now, rid = self.clock(), self.fresh(args)
        fields, _ = self._schedule(args, now)
        row = {"id": rid, "owner": args.get("for", person), "by": person, "note": args["note"], "at": None, "date": None,
               "time": None, "repeat": None, "until": None, "grace_at": None, **fields,
               "visibility": args.get("visibility", "personal"), "active": 1, "done": 0, "snoozed_until": None, "after": now}
        self.rows[rid] = row
        return rid, dict(row)

    def _snooze(self, person, args):
        until = self.clock() + 60 * args.get("minutes", SNOOZE_DEFAULT_MIN)
        row = self.rows[args["id"]] | {"snoozed_until": until, "after": until}
        self.rows[row["id"]] = row
        return row["id"], dict(row)

    def _done(self, person, args):
        """A repeating reminder is done for THIS occurrence (the next one stays); anything else is done for good
        (routers/reminders.py:401-424)."""
        row = self.rows[args["id"]]
        row = row | ({"snoozed_until": None, "after": self.clock()} if row["repeat"] else {"done": 1})
        self.rows[row["id"]] = row
        return row["id"], dict(row)

    def _cancel(self, person, args):
        self.rows.pop(args["id"], None)          # production soft-deletes (deleted = 1); the kernel's undo is the way back
        return args["id"], None

    def _edited(self, person, args) -> tuple[dict, str]:
        row, now = self.rows[args["id"]], self.clock()
        new = dict(row)
        for key in ("note", "time"):
            if key in args:
                new[key] = args[key]
        if "active" in args:
            new["active"] = args["active"]
        if "visibility" in args and person == row["owner"]:       # a non-owner's visibility change is dropped (routers/reminders.py:217-222)
            new["visibility"] = args["visibility"]
        if set(args) & {"date", "time"}:
            if row["at"] is not None:            # "in 30 minutes" moved to a wall time: the day and time it had, then the change
                was = datetime.fromtimestamp(row["at"], self.tz)
                new |= {"at": None, "date": was.date().isoformat(), "time": new["time"] if "time" in args else was.strftime("%H:%M")}
            if "date" in args:
                new["date"] = args["date"]
            if row["repeat"] and new["date"] == row["date"]:
                fields, why = {}, ""             # a re-sent anchor is not a date change: the rule keeps its phase (routers/reminders.py:224-229)
            else:                                # a new day re-anchors (or re-checks) exactly as a new write would
                fields, why = self._schedule({k: new[k] for k in ("date", "time", "repeat", "until") if new[k]}, now)
            if why:
                return new, why
            new |= {"grace_at": None, **fields}
        new["after"] = now                       # rescheduled from now; a pending snooze is kept (routers/reminders.py:45-78)
        return new, ""

    def _edit(self, person, args):
        new, _ = self._edited(person, args)
        self.rows[new["id"]] = new
        return new["id"], dict(new)

    # ---- registration ----------------------------------------------------------------------------------------------------------
    def register(self, kernel) -> "DurableReminders":
        when = {"date": OPT("string", "the day, YYYY-MM-DD", pattern=sch.DAY, max=10),
                "time": OPT("string", "24-hour HH:MM", pattern=sch.HHMM, max=5)}
        self.register_all(kernel, {
            "remind.set": (self._set, self._check_set, "Set a reminder for the person", {
                "note": {"type": "string", "required": True, "description": "what to remind"},
                "in_minutes": OPT("integer", "minutes from now as a whole number (half an hour is 30, 2 hours is 120)",
                                  minimum=1, maximum=10080), **when,
                "repeat": OPT("string", "a repeat rule (RRULE: FREQ=DAILY|WEEKLY|MONTHLY|YEARLY ...)", max=120),
                "until": OPT("string", "the last day it repeats, YYYY-MM-DD", pattern=sch.DAY, max=10),
                "for": OPT("string", "who it is for, when not the person asking", max=64),
                "visibility": OPT("string", "personal or family", pattern=VISIBILITY, max=8), "asked": ASKED}),
            "remind.snooze": (self._snooze, self._check_row("own"), "Snooze one of the person's reminders", {
                "id": ID, "minutes": OPT("integer", "how long", minimum=1, maximum=SNOOZE_MAX_MIN), "asked": ASKED}),
            "remind.done": (self._done, self._check_row("own"), "Mark a reminder done", {"id": ID, "asked": ASKED}),
            "remind.cancel": (self._cancel, self._check_row("edit"), "Cancel a reminder", {"id": ID, "asked": ASKED}),
            "remind.edit": (self._edit, self._check_edit, "Change a reminder", {
                "id": ID, "note": OPT("string", "what to remind"), **when,
                "visibility": OPT("string", "personal or family", pattern=VISIBILITY, max=8),
                "active": OPT("integer", "1 on, 0 off", minimum=0, maximum=1), "asked": ASKED}),
        })
        return self
