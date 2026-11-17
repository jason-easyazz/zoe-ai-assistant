"""anticipation_situations - the SITUATION MODEL of the anticipation engine (pure: no I/O, no model, no clock of its own).

``situations(facts, person)`` reads one person's next ``HORIZON_H`` hours of structured facts (calendar events, alarms,
reminders, birthdays, the other household members' events) and returns a small list of typed ``Situation`` records. It is
deterministic and explainable: every situation carries ``sources`` (the ids of the facts it came from) and ``data`` (the
numbers it was decided on). The rule pack (``anticipation_rules``) turns situations into offers; this module never words one.

PRIVACY - THE ONE CLAMP. ``view(facts, person)`` is the only door to another member's facts: the person's own facts as they
are, another member's ``family`` event as it is, another member's ``busy`` event as a bare busy block (no title, no place),
another member's ``personal`` event NOT AT ALL, and nobody else's alarms, reminders or birthdays. A guest or an unknown id
sees nothing. Every detector below reads the view, never ``facts``, so a situation cannot be built from a fact its person
may not see. (Remove the clamp and ``tests/test_anticipation_situations.py`` fails: that is its negative control.)

Kinds: ``travel_day`` ``early_start`` ``no_alarm`` ``double_booked`` ``clash`` ``child_uncovered`` ``birthday``
``chore_due`` ``full_day`` ``empty_day`` ``late_then_early`` ``tight_gap``. Event kinds (flight / train / trip /
appointment) and the leave-by leads are phase 1's (``anticipation.classify``, ``LEAVE_LEAD_MIN``): one definition.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from typing import Optional

import anticipation as _p1

HORIZON_H = 36
BIRTHDAY_DAYS = 7                 # a birthday is worth a reminder up to a week ahead (the one fact read past the horizon)
DEFAULT_LEAD_MIN = 30             # leave-by lead for an event phase 1 does not classify
DEFAULT_LEN_MIN = 60              # an event with no end time is taken to last this long
MORNING_MIN = 9 * 60              # "no alarm before it" only matters for a start before this
LATE_MIN = 22 * 60 + 30           # an evening that ends at or after this is a late night
EARLY_MIN = 8 * 60                # ... when the next day starts before this
TIGHT_GAP_MIN = 30
CHILD_EVENT_MAX_MIN = 180         # a longer child event (a school day) is not something an adult is "needed at the start" of
FULL_DAY_MIN_EVENTS = 4
KINDS = ("travel_day", "early_start", "no_alarm", "double_booked", "clash", "child_uncovered", "birthday", "chore_due",
         "full_day", "empty_day", "late_then_early", "tight_gap")
_CHORE_RX = re.compile(r"\b(?:bins?|rubbish|recycling|garbage|green waste|chores?)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Member:
    id: str
    adult: bool = True
    guest: bool = False
    name: str = ""


@dataclass(frozen=True)
class Ev:
    id: str
    owner: str
    title: str
    day: str                      # household-local ISO date of this occurrence
    start: str = ""               # "HH:MM"; "" = all day / untimed
    end: str = ""                 # "HH:MM"; "" = DEFAULT_LEN_MIN after the start
    end_day: str = ""             # last day (inclusive) of a multi-day event
    place: str = ""
    visibility: str = "family"    # family | busy | personal (calendar_service.VISIBILITIES)
    attendees: tuple = ()         # member ids asked to be there
    repeats: bool = False


@dataclass(frozen=True)
class Alarm:
    id: str
    owner: str
    time: str                     # "HH:MM"
    days: frozenset = frozenset() # ISO weekdays it repeats on; empty = a one-off on ``once``
    once: str = ""


@dataclass(frozen=True)
class Reminder:
    id: str
    owner: str
    title: str
    day: str = ""                 # "" = undated
    time: str = ""


@dataclass(frozen=True)
class Birthday:
    id: str
    owner: str                    # the member whose contact this is
    name: str
    month: int
    day: int


@dataclass(frozen=True)
class Facts:
    now: datetime                 # aware, in the household zone
    members: tuple = ()
    events: tuple = ()
    alarms: tuple = ()
    reminders: tuple = ()
    birthdays: tuple = ()
    typical: dict = field(default_factory=dict)   # member id -> their usual number of timed events a day


@dataclass(frozen=True)
class Situation:
    kind: str
    person: str
    day: str
    sources: tuple                # ("event:<id>", "alarm:<id>", ...): the facts it came from
    data: dict = field(default_factory=dict)


# -- the clamp ---------------------------------------------------------------------------------------------------------------
def view(facts: Facts, person: str) -> Facts:
    """What ``person`` may see of the household's facts. The ONLY path from another member's fact to a situation."""
    me = next((m for m in facts.members if m.id == person), None)
    if me is None or me.guest:
        return Facts(now=facts.now)
    events = []
    for e in facts.events:
        if e.owner == person or e.visibility == "family":
            events.append(e)
        elif e.visibility == "busy":
            events.append(replace(e, title="", place="", attendees=()))
    return Facts(now=facts.now, members=facts.members, events=tuple(events),
                 alarms=tuple(a for a in facts.alarms if a.owner == person),
                 reminders=tuple(r for r in facts.reminders if r.owner == person),
                 birthdays=tuple(b for b in facts.birthdays if b.owner == person),
                 typical={person: facts.typical[person]} if person in facts.typical else {})


# -- clock maths ---------------------------------------------------------------------------------------------------------------
def _d(raw: str) -> Optional[date]:
    try:
        return date.fromisoformat(str(raw or "")[:10])
    except ValueError:
        return None


def fmt(minutes: int) -> str:
    return f"{minutes // 60 % 24:02d}:{minutes % 60:02d}"


def _span(e: Ev, today: date) -> Optional[tuple]:
    """``(start, end)`` in minutes after today's midnight; an all-day event covers its whole day(s)."""
    d0 = _d(e.day)
    if d0 is None:
        return None
    base = (d0 - today).days * 1440
    last = max(_d(e.end_day) or d0, d0)
    s = _p1._hhmm(e.start)
    if s is None:
        return base, ((last - today).days + 1) * 1440
    en = _p1._hhmm(e.end)
    if last > d0:
        return base + s, (last - today).days * 1440 + (en if en is not None else 1440)
    if en is None:
        return base + s, base + s + DEFAULT_LEN_MIN
    return base + s, base + (en if en > s else en + 1440)      # an end before the start is past midnight


def _kind(e: Ev) -> Optional[str]:
    return _p1.classify(_p1.Event(e.id, e.owner, e.title, e.day, e.start, location=e.place))


def _usual_alarm(alarms: tuple, d: date) -> Optional[int]:
    hits = [m for a in alarms if (d.isoweekday() in a.days or a.once == d.isoformat())
            for m in [_p1._hhmm(a.time)] if m is not None]
    return min(hits) if hits else None


def _src(e: Ev) -> str:
    return f"event:{e.id}"


# -- the model -----------------------------------------------------------------------------------------------------------------
def situations(facts: Facts, person: str) -> list:
    """Every situation ``person``'s visible facts support in the next ``HORIZON_H`` hours, in a stable order."""
    v = view(facts, person)
    if not v.members:
        return []
    now = v.now
    today = now.date()
    now_m = now.hour * 60 + now.minute
    end_m = now_m + HORIZON_H * 60
    spans = {e.id: sp for e in v.events for sp in [_span(e, today)] if sp is not None}
    ahead = sorted((e for e in v.events if e.id in spans and now_m < spans[e.id][0] <= end_m),
                   key=lambda e: (spans[e.id], e.id))
    own = [e for e in ahead if e.owner == person]
    timed = [e for e in own if e.start]
    out: list = []

    def add(kind: str, day: date, sources: list, **data) -> None:
        out.append(Situation(kind, person, day.isoformat(), tuple(sources), data))

    def day_of(e: Ev) -> date:
        return today + timedelta(days=spans[e.id][0] // 1440)

    first_of_day: dict = {}
    for e in timed:
        first_of_day.setdefault(day_of(e), e)
    for e in own:                                                   # travel
        k = _kind(e)
        if k in ("flight", "train", "trip"):
            s = spans[e.id][0]
            add("travel_day", day_of(e), [_src(e)], travel=k, start=fmt(s) if e.start else "",
                leave_by=fmt(s - _p1.LEAVE_LEAD_MIN[k]) if e.start and s % 1440 >= _p1.LEAVE_LEAD_MIN[k] else "",
                multi_day=bool(_d(e.end_day) and _d(e.end_day) > _d(e.day)), end_day=e.end_day)
    for d, e in sorted(first_of_day.items()):                       # the morning: alarm too late, or no alarm at all
        s = spans[e.id][0] % 1440
        need = (s - _p1.LEAVE_LEAD_MIN.get(_kind(e) or "", DEFAULT_LEAD_MIN) - _p1.PREP_MIN) // 5 * 5
        usual = _usual_alarm(v.alarms, d)
        if need < _p1.EARLIEST_WAKE_MIN or (d == today and need <= now_m):
            continue
        if usual is not None and need <= usual - _p1.WAKE_MARGIN_MIN:
            add("early_start", d, [_src(e)] + [f"alarm:{a.id}" for a in v.alarms if _p1._hhmm(a.time) == usual],
                start=fmt(s), wake=fmt(need), usual=fmt(usual))
        elif usual is None and s < MORNING_MIN:
            add("no_alarm", d, [_src(e)], start=fmt(s), wake=fmt(need))
    for i, a in enumerate(timed):                                   # the person's own day against itself
        for b in timed[i + 1:]:
            (a0, a1), (b0, b1) = spans[a.id], spans[b.id]
            if b0 < a1:
                add("double_booked", day_of(b), [_src(a), _src(b)], at=fmt(b0))
            elif day_of(a) == day_of(b) and b0 - a1 < TIGHT_GAP_MIN and a.place.strip() and b.place.strip() \
                    and a.place.strip().lower() != b.place.strip().lower() and b is timed[i + 1]:
                add("tight_gap", day_of(b), [_src(a), _src(b)], gap=b0 - a1, leave_at=fmt(a1), next_start=fmt(b0))
    for e in timed:                                                 # against an event of another member the person is asked to
        for o in ahead:
            if o.owner != person and o.start and person in o.attendees and spans[o.id][0] < spans[e.id][1] \
                    and spans[e.id][0] < spans[o.id][1]:
                add("clash", day_of(e), [_src(e), _src(o)], at=fmt(max(spans[e.id][0], spans[o.id][0])))
    me = next(m for m in v.members if m.id == person)
    if me.adult:                                                    # a child's event while every adult is busy
        adults = [m for m in v.members if m.adult and not m.guest]
        kids = {m.id: m for m in v.members if not m.adult and not m.guest}
        for e in ahead:
            if e.owner not in kids or not e.start or spans[e.id][1] - spans[e.id][0] > CHILD_EVENT_MAX_MIN:
                continue
            busy = [next((o for o in v.events if o.owner == a.id and o.id in spans and (o.start or a.id != person)
                          and spans[o.id][0] < spans[e.id][1] and spans[e.id][0] < spans[o.id][1]), None) for a in adults]
            if adults and all(busy):
                add("child_uncovered", day_of(e), [_src(e)] + [_src(o) for o in busy], child=e.owner,
                    child_name=kids[e.owner].name, start=fmt(spans[e.id][0]))
    for b in v.birthdays:                                           # a birthday nobody has a reminder for
        for i in range(BIRTHDAY_DAYS + 1):
            d = today + timedelta(days=i)
            if (d.month, d.day) != (b.month, b.day):
                continue
            first = (b.name.split() or [""])[0].lower()
            covered = any(first and first in r.title.lower() for r in v.reminders) or \
                any(first and first in e.title.lower() and "birthday" in e.title.lower() and e.owner == person for e in v.events)
            if not covered and (i > 0 or now_m < 12 * 60):
                add("birthday", d, [f"birthday:{b.id}"], name=b.name, days_until=i)
    for e in own:                                                   # bins and chores with nothing to prompt them
        if _CHORE_RX.search(e.title) and day_of(e) > today and now_m < 19 * 60 and not any(
                _CHORE_RX.search(r.title) and _d(r.day) in (day_of(e), day_of(e) - timedelta(days=1)) for r in v.reminders):
            add("chore_due", day_of(e), [_src(e)], evening_before=(day_of(e) - timedelta(days=1)).isoformat())
    target = today if now_m < 12 * 60 else today + timedelta(days=1)   # the shape of the next whole day
    n = sum(1 for e in timed if day_of(e) == target)
    typical = float(v.typical.get(person, 1.0))
    all_day_own = any(e.owner == person and not e.start and sp[0] <= (target - today).days * 1440 < sp[1]
                      for e in v.events for sp in [spans.get(e.id)] if sp)
    if n >= FULL_DAY_MIN_EVENTS and n >= 2 * typical:
        day_events = [e for e in timed if day_of(e) == target]
        add("full_day", target, [_src(e) for e in day_events], count=n, first=fmt(spans[day_events[0].id][0]))
    elif n == 0 and typical >= 2 and not all_day_own:
        undated = [r for r in v.reminders if not r.day]
        add("empty_day", target, [f"reminder:{r.id}" for r in undated], undated=len(undated))
    for e in timed:                                                 # out late, then up early
        e1 = spans[e.id][1]
        d = day_of(e)
        nxt = first_of_day.get(d + timedelta(days=1))
        if e1 % 1440 >= LATE_MIN or e1 // 1440 > spans[e.id][0] // 1440:
            if nxt is not None and nxt is not e and spans[nxt.id][0] % 1440 < EARLY_MIN:
                add("late_then_early", d, [_src(e), _src(nxt)], late_end=fmt(e1), early_start=fmt(spans[nxt.id][0]))
    return out
