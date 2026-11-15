"""anticipation_rules - the RULE PACK of the anticipation engine: situations -> offers (pure: no I/O, no model).

``offers(facts, person)`` runs ``anticipation_situations.situations`` and lets each ``Rule`` turn a situation into at most
one ``Offer``: ONE sentence, ONE proposed action through an existing writer with exact arguments, for one person. Zoe offers;
she never acts unasked - an offer does nothing until its person says yes.

An offer carries: ``rule`` (its id), ``person`` (the only one who may ever see it: every offer is personal), the sentence
(``text(now)``; "{when}" / "{When}" is worded when it is said, so "tomorrow" is never stale), ``tool`` + ``args`` (see ``TOOLS``),
``priority`` (0-100), ``expires`` (the instant its action would be pointless), ``surface_from``, ``key`` (the dedupe key:
the same situation makes the same key every night) and ``sources`` (the facts behind it, from the situation).

PRIVACY. A rule sees the situation and the person's VIEW of the facts (``anticipation_situations.view``), never the raw
household. A sentence names no event title and no place; the words that can come from a fact are the person's own event
title (a list name / a chore reminder), their own contact's name (a birthday) and a household member's name (a child).

Per person: ``disabled`` rule ids make nothing; ``tune(rule_id)`` (the learning loop) returns a ``Tuning`` that shifts the
priority, lengthens the lead or silences the rule. ``MIN_PRIORITY`` is the surfacing threshold after tuning.

``python3 anticipation_rules.py`` prints the rules table of ``docs/knowledge/anticipation.md`` (pinned by a test).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Optional

import anticipation as _p1
import anticipation_situations as S

MIN_PRIORITY = 20                 # an offer whose tuned priority is below this is not made
SURFACE_LEAD_H = 18               # an offer may be said from this long before it expires (tuning can lengthen it)
EVENING = "19:00"
#: tool id -> the existing writer a yes runs (``anticipation._do``) and the exact arguments it takes
TOOLS = {"alarm.create": ("alarms.create_alarm", ("time_hhmm", "once_date", "label")),
         "reminder.create": ("reminder_service.create_reminder_record", ("title", "due_date", "due_time")),
         "list.create": ("list_service.create_list_record", ("name", "items"))}


@dataclass(frozen=True)
class Tuning:
    priority: int = 0             # added to the rule's base priority
    lead_h: int = 0               # extra hours of lead before the expiry
    quiet: bool = False           # the rule makes no offer for this person


@dataclass(frozen=True)
class Offer:
    rule: str
    person: str
    template: str                 # one sentence; "{when}" is the day word
    day: str
    tool: str
    args: dict
    priority: int
    expires: datetime
    surface_from: datetime
    key: str
    sources: tuple
    visibility: str = "personal"

    def text(self, now: datetime) -> str:
        return render(self.template, self.day, now)


@dataclass(frozen=True)
class Rule:
    id: str
    situations: tuple             # the situation kinds it answers
    tool: str
    priority: int
    does: str                     # for the docs table: what a yes does
    when: str                     # for the docs table: when it offers
    build: Callable = field(repr=False, compare=False, default=None)   # (situation, view, now) -> (template, args, expiry) | None
    adults_only: bool = False


def render(template: str, day: str, now: datetime) -> str:
    d = S._d(day) or now.date()
    when = _p1._day(d, now.date())
    return template.replace("{when}", when).replace("{When}", when[:1].upper() + when[1:])


def _at(day: str, hhmm: str, now: datetime) -> datetime:
    d, m = S._d(day), _p1._hhmm(hhmm) or 0
    return datetime(d.year, d.month, d.day, m // 60, m % 60, tzinfo=now.tzinfo)


def _c(hhmm: str) -> str:
    return _p1._clock(hhmm)


def _minus(hhmm: str, minutes: int) -> str:
    return S.fmt(max(0, (_p1._hhmm(hhmm) or 0) - minutes))


def _reminder(title: str, day: str, hhmm: str) -> dict:
    return {"title": title, "due_date": day, "due_time": hhmm}


def _own_title(s: S.Situation, v: S.Facts) -> str:
    eid = s.sources[0].split(":", 1)[1]
    return next((e.title for e in v.events if e.id == eid and e.owner == s.person), "")


# -- the builders: (situation, the person's view, now) -> (template, args, expiry) or None -------------------------------------
def _wake(s, v, now):
    d = s.data
    return (f"You start at {_c(d['start'])} {{when}} and your alarm is usually {_c(d['usual'])} - want a one-off alarm for {_c(d['wake'])}?",
            {"time_hhmm": d["wake"], "once_date": s.day, "label": "Early start"}, _at(s.day, d["wake"], now))


def _alarm_for_event(s, v, now):
    d = s.data
    return (f"You start at {_c(d['start'])} {{when}} and there's no alarm set - want one for {_c(d['wake'])}?",
            {"time_hhmm": d["wake"], "once_date": s.day, "label": "Early start"}, _at(s.day, d["wake"], now))


def _leave(s, v, now):
    d = s.data
    if not d.get("leave_by"):
        return None
    label = d.get("travel") or "appointment"
    return (f"Your {label} {{when}} is at {_c(d['start'])} - want a reminder to leave by {_c(d['leave_by'])}?",
            _reminder(f"Leave for your {label}", s.day, d["leave_by"]), _at(s.day, d["leave_by"], now))


def _packing(s, v, now):
    d = s.data
    if not d.get("multi_day"):
        return None
    items = (_p1.PACKING_TRAVEL_DOCS if d["travel"] in ("flight", "train") else ()) + _p1.PACKING_BASICS
    name = f"Packing: {(_own_title(s, v) or d['travel']).strip()[:60]}"
    return (f"You're away {{when}} for your {d['travel']} - want me to start a private packing list?",
            {"name": name, "items": list(items)}, _at(s.day, d.get("start") or "00:00", now))


def _checkin(s, v, now):
    d = s.data
    eve = (S._d(s.day) - timedelta(days=1)).isoformat()
    if d["travel"] != "flight" or not d.get("start") or _at(eve, EVENING, now) <= now:
        return None
    return (f"You fly {{when}} at {_c(d['start'])} - want a reminder the evening before to check in and find your ID?",
            _reminder("Check in for your flight and find your ID", eve, EVENING), _at(eve, EVENING, now))


def _sort_out(title: str, sentence: str):
    """Something to sort out before it starts: the telling is the point; the reminder is two hours ahead of it."""
    def build(s, v, now):
        hhmm = s.data.get("at") or s.data.get("start") or "00:00"
        due = _at(s.day, hhmm, now) - timedelta(hours=2)
        if due < now + timedelta(minutes=30):
            return None
        t, who = _c(hhmm), s.data.get("child_name") or "them"
        nudge = (due.date().isoformat(), due.strftime("%H:%M"))
        return (sentence.format(t=t, who=who, n=_c(nudge[1])) + "?", _reminder(title.format(t=t, who=who), *nudge), due)
    return build


def _birthday(s, v, now):
    d = s.data
    if _at(s.day, "09:00", now) <= now:
        return None
    return (f"It's {d['name']}'s birthday {{when}} and there's no reminder for it - want one that morning?",
            _reminder(f"Wish {d['name']} a happy birthday", s.day, "09:00"), _at(s.day, "09:00", now))


def _chore(s, v, now):
    eve = s.data["evening_before"]
    title = _own_title(s, v)
    if not title or _at(eve, EVENING, now) <= now:
        return None
    return (f"There's a chore on your calendar {{when}} with nothing to prompt it - want a reminder at {_c(EVENING)} the evening before?",
            _reminder(title[:80], eve, EVENING), _at(eve, EVENING, now))


def _full_day(s, v, now):
    d = s.data
    eve = (S._d(s.day) - timedelta(days=1)).isoformat()
    due = (eve, "20:00") if _at(eve, "20:00", now) > now else (s.day, _minus(d["first"], 60))
    if _at(*due, now) <= now:
        return None
    return (f"{{When}} is fuller than usual, {d['count']} things with the first at {_c(d['first'])} - want a reminder at {_c(due[1])} to get ready?",
            _reminder(f"Get ready: {d['count']} things, first at {_c(d['first'])}", *due), _at(*due, now))


def _empty_day(s, v, now):
    n = s.data["undated"]
    if n < 1 or _at(s.day, "10:00", now) <= now:
        return None
    return (f"{{When}} is clear and you have {n} reminder{'s' if n != 1 else ''} with no date - want a nudge at {_c('10:00')} to go through them?",
            _reminder("Go through your undated reminders", s.day, "10:00"), _at(s.day, "10:00", now))


def _wind_down(s, v, now):
    d = s.data
    if _at(s.day, "22:00", now) <= now:
        return None
    return (f"You're out late {{when}} and start at {_c(d['early_start'])} the next morning - want a reminder at {_c('22:00')} to head home?",
            _reminder(f"Head home: you start at {_c(d['early_start'])} tomorrow", s.day, "22:00"), _at(s.day, "22:00", now))


def _tight_gap(s, v, now):
    d = s.data
    due = _minus(d["leave_at"], 10)
    if _at(s.day, due, now) <= now:
        return None
    return (f"You have {d['gap']} minutes to get between two places {{when}} before {_c(d['next_start'])} - want a reminder at {_c(due)} to wrap up and go?",
            _reminder(f"Wrap up: next thing at {_c(d['next_start'])}", s.day, due), _at(s.day, due, now))


RULES = (
    Rule("wake", ("early_start",), "alarm.create", 90, "a one-off alarm that morning (the usual alarm is untouched)",
         "the first timed thing of a day needs an earlier start than the usual alarm (15 minutes or more)", _wake, adults_only=True),
    Rule("child_cover", ("child_uncovered",), "reminder.create", 85, "a personal reminder to sort out who takes them",
         "a child's short event starts while every adult is visibly busy", _sort_out(
             "Sort out who takes {who} at {t}", "Every adult looks busy when {who}'s {t} thing starts {{when}} - want a reminder at {n} to sort out who takes them"),
         adults_only=True),
    Rule("alarm_for_event", ("no_alarm",), "alarm.create", 80, "a one-off alarm that morning",
         "a day starts before 9 am and no alarm rings that day", _alarm_for_event, adults_only=True),
    Rule("clash_heads_up", ("clash",), "reminder.create", 75, "a personal reminder to sort it out",
         "the person's own event overlaps another member's event they are asked to", _sort_out(
             "Sort out the clash at {t}", "You're asked to something at {t} {{when}} that overlaps one of your own - want a reminder at {n} to sort it out")),
    Rule("leave", ("travel_day", "appointment"), "reminder.create", 70, "a personal reminder at the leave-by time",
         "a timed flight, train, trip or an appointment with a place", _leave),
    Rule("tight_gap_leave", ("tight_gap",), "reminder.create", 65, "a personal reminder ten minutes before the first one ends",
         "two of the person's events in different places are under 30 minutes apart", _tight_gap),
    Rule("double_booked", ("double_booked",), "reminder.create", 62, "a personal reminder to sort it out",
         "two of the person's own timed events overlap", _sort_out(
             "Sort out the two things at {t}", "You have two things at {t} {{when}} - want a reminder at {n} to sort it out")),
    Rule("flight_checkin", ("travel_day",), "reminder.create", 60, "a personal reminder at 7 pm the evening before",
         "a timed flight on a later day", _checkin),
    Rule("birthday_reminder", ("birthday",), "reminder.create", 55, "a personal reminder at 9 am on the day",
         "a contact's birthday within a week with no reminder or event that names them", _birthday),
    Rule("packing", ("travel_day",), "list.create", 50, "the person's PRIVATE packing list with a few basics",
         "a flight, train or trip that spans more than one day", _packing),
    Rule("chore_night_before", ("chore_due",), "reminder.create", 45, "a personal reminder at 7 pm the evening before",
         "a bin / chore event of the person's on a later day with no reminder near it", _chore),
    Rule("wind_down", ("late_then_early",), "reminder.create", 40, "a personal reminder at 10 pm to head home",
         "an evening that ends after 10:30 pm before a day that starts before 8 am", _wind_down),
    Rule("full_day_prep", ("full_day",), "reminder.create", 35, "a personal reminder the evening before (or an hour before the first)",
         "four or more timed events in the next whole day, and at least twice the person's usual", _full_day),
    Rule("empty_day_catchup", ("empty_day",), "reminder.create", 20, "a personal reminder at 10 am",
         "a usually busy person has a clear day and reminders with no date", _empty_day),
)
BY_ID = {r.id: r for r in RULES}


def offers(facts: S.Facts, person: str, *, disabled: frozenset = frozenset(),
           tune: Optional[Callable[[str], Tuning]] = None) -> list:
    """Every offer the person's situations support now, best first (priority, then the nearest expiry, then the key)."""
    v = S.view(facts, person)                                     # a rule words an offer from the person's view only
    me = next((m for m in v.members if m.id == person), None)
    if me is None:
        return []
    now = facts.now
    out, seen = [], set()
    for s in S.situations(facts, person):
        for rule in RULES:
            if s.kind not in rule.situations or rule.id in disabled or (rule.adults_only and not me.adult):
                continue
            t = tune(rule.id) if tune else Tuning()
            priority = max(0, min(100, rule.priority + t.priority))
            if t.quiet or priority < MIN_PRIORITY:
                continue
            built = rule.build(s, v, now)
            if built is None:
                continue
            template, args, expires = built
            key = f"{rule.id}:{s.day}:{'+'.join(sorted(s.sources))}"
            if expires <= now or key in seen:
                continue
            seen.add(key)
            out.append(Offer(rule.id, person, template, s.day, rule.tool, args, priority, expires,
                             max(now, expires - timedelta(hours=SURFACE_LEAD_H + t.lead_h)), key, s.sources))
    return sorted(out, key=lambda o: (-o.priority, o.expires, o.key))


def rules_table() -> str:
    """The docs table, generated from the pack (docs/knowledge/anticipation.md holds a copy; a test pins them equal)."""
    lines = ["| rule | offers when | a yes does | tool | priority | who |", "|---|---|---|---|---|---|"]
    for r in RULES:
        lines.append(f"| `{r.id}` | {r.when} | {r.does} | `{r.tool}` | {r.priority} | {'adults' if r.adults_only else 'members'} |")
    return "\n".join(lines)


if __name__ == "__main__":
    print(rules_table())
