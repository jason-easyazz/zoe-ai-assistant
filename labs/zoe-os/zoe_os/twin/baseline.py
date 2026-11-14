"""The learned baseline: what is normal for each person. Counts and simple distributions, in
code. No model, no score, nothing inferred about why.

``Twin`` is a kernel SKILL. It learns only from what the kernel delivers to it, so:
* a person who has not enabled it (tracking off) is never delivered and has no baseline;
* it reads PRIVATE events only. A household-visible event (a guest arriving) would reach it
  through someone else's grant, so it is ignored: nobody is tracked on another's consent;
* a FORGET notice removes what it derived (``on_event``), for the person or for the named events.

What it keeps is REDUCED: per person and day, a few clock times, rooms seen, and which routine
steps were done, each with the sequence number it came from. Never free text (a room or a
routine item's label is kept as the key of a count), and never older than ``window_days``:
a day that far behind the newest one seen is dropped. The window is the twin's own retention
and is an OWNER DECISION (``docs/architecture/zoe-os.md``, the twin section); 56 is the lab's
placeholder, not a choice.

``normal(person, day_type)`` says nothing until it has ``MIN_DAYS[day_type]`` days of that
type for that person: ten working days, or six weekend days. Below that the answer is the
string ``"not enough days yet"`` with the count, not a guess from two Tuesdays. The numbers
ten and six are a judgement (enough to tell a one-day-in-ten habit from a constant one), not
a measurement.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from ..events import Event, Kind

MIN_DAYS = {"workday": 10, "weekend": 6}
WINDOW_DAYS = 56
DAY_STARTS = 4                     # a "day" runs 04:00 to 04:00, so a late bedtime stays in its day
TIMES = ("up", "left", "arrived", "bed")
NOT_ENOUGH, OFF, OK = "not enough days yet", "no data: not enabled for this person", "ok"


def day_of(ts: float) -> tuple[str, int]:
    """(the day an instant belongs to, minutes since that day's midnight; may pass 1440)."""
    t = datetime.fromtimestamp(ts, timezone.utc)
    day = (t - timedelta(hours=DAY_STARTS)).date()
    return day.isoformat(), int((t - datetime(day.year, day.month, day.day, tzinfo=timezone.utc)).total_seconds() // 60)


def day_type(day: str) -> str:
    return "weekend" if datetime.fromisoformat(day).weekday() >= 5 else "workday"


@dataclass(frozen=True)
class TimeStat:
    seen: int          # days on which it happened
    days: int          # days observed
    earliest: int      # minutes since midnight
    median: int
    latest: int
    low: int           # the middle 80 %: 10th percentile...
    high: int          # ...to 90th (nearest rank)


@dataclass(frozen=True)
class Normal:
    status: str
    person: str
    day_type: str
    days: int
    needed: int
    times: dict[str, TimeStat] = field(default_factory=dict)
    steps: dict[str, tuple[int, int]] = field(default_factory=dict)        # "routine/item" -> (done, days)
    rooms: dict[int, dict[str, int]] = field(default_factory=dict)         # hour -> room -> days seen there
    home_all_day: int = 0                                                  # days with no "left"


def _rank(values: list[int], fraction: float) -> int:
    return values[min(len(values) - 1, max(0, round(fraction * (len(values) - 1))))]


class Twin:
    manifest = {"skill": "twin", "reads": {"presence": "private", "routine_step": "private"}}

    def __init__(self, window_days: int = WINDOW_DAYS) -> None:
        # person -> day -> fact name -> (value, seq of the event it came from)
        self.days: dict[str, dict[str, dict[str, tuple]]] = {}
        self.window_days = window_days

    # ---- learning: one reduced fact per event -------------------------------------------------
    def on_event(self, ev: Event, ctx) -> None:
        if ev.kind is Kind.FORGET:
            self._forget(ev)
        elif ev.visibility.value == "private" and ev.subject:
            self._observe(ev)

    def _observe(self, ev: Event) -> None:
        day, minute = day_of(ev.ts)
        facts = self.days.setdefault(ev.subject, {}).setdefault(day, {})
        p = ev.payload
        if ev.kind is Kind.PRESENCE:
            state = p.get("state")
            if state in TIMES:
                facts.setdefault(state, (minute, ev.seq))              # the first of the day
            if p.get("room"):
                facts[f"room@{(minute // 60) % 24}"] = (p["room"], ev.seq)
        elif ev.kind is Kind.ROUTINE_STEP:
            facts[f"step:{p['routine']}/{p['item']}"] = (bool(p["done"]), ev.seq)      # the last word
        self.expire(day)

    def expire(self, today: str) -> int:
        """Drop every day that is ``window_days`` or more behind ``today``, for everyone.
        Returns how many person-days went."""
        cut = (datetime.fromisoformat(today) - timedelta(days=self.window_days)).date().isoformat()
        gone = 0
        for person in list(self.days):
            for old in [d for d in self.days[person] if d <= cut]:
                del self.days[person][old]
                gone += 1
            if not self.days[person]:
                del self.days[person]
        return gone

    def _forget(self, notice: Event) -> None:
        person, seqs = notice.payload.get("subject"), notice.payload.get("seqs")
        if seqs is None:
            self.days.pop(person, None)                                # everything about them
            return
        gone = set(seqs)
        for day, facts in list(self.days.get(person, {}).items()):
            self.days[person][day] = {k: v for k, v in facts.items() if v[1] not in gone}
            if not self.days[person][day]:
                del self.days[person][day]

    # ---- the baseline --------------------------------------------------------------------------
    def normal(self, person: str, kind: str = "workday", *, before: str | None = None) -> Normal:
        """What is usual for ``person`` on a ``kind`` of day (optionally: from days before one)."""
        if person not in self.days:
            return Normal(OFF, person, kind, 0, MIN_DAYS[kind])
        days = [facts for day, facts in sorted(self.days[person].items())
                if day_type(day) == kind and (before is None or day < before)]
        if len(days) < MIN_DAYS[kind]:
            return Normal(NOT_ENOUGH, person, kind, len(days), MIN_DAYS[kind])
        times = {}
        for name in TIMES:
            got = sorted(facts[name][0] for facts in days if name in facts)
            if got:
                times[name] = TimeStat(len(got), len(days), got[0], _rank(got, 0.5), got[-1],
                                       _rank(got, 0.1), _rank(got, 0.9))
        steps: dict[str, list[int]] = {}
        rooms: dict[int, Counter] = {}
        for facts in days:
            for key, (value, _seq) in facts.items():
                if key.startswith("step:"):
                    tally = steps.setdefault(key[5:], [0, 0])
                    tally[0] += bool(value)
                    tally[1] += 1
                elif key.startswith("room@"):
                    rooms.setdefault(int(key[5:]), Counter())[value] += 1
        return Normal(OK, person, kind, len(days), MIN_DAYS[kind], times,
                      {k: (v[0], v[1]) for k, v in sorted(steps.items())},
                      {hour: dict(sorted(c.items())) for hour, c in sorted(rooms.items())},
                      sum(1 for facts in days if "left" not in facts))


def hhmm(minute: int) -> str:
    return f"{(minute // 60) % 24:02d}:{minute % 60:02d}"


def describe(n: Normal) -> list[str]:
    """The baseline as lines a person can read. Counts only."""
    if n.status != OK:
        return [f"{n.person} ({n.day_type}): {n.status}" + (f" ({n.days} of {n.needed})" if n.status == NOT_ENOUGH else "")]
    out = [f"{n.person} ({n.day_type}), from {n.days} days:"]
    for name, s in n.times.items():
        out.append(f"  {name}: usually {hhmm(s.low)} to {hhmm(s.high)} (median {hhmm(s.median)}, "
                   f"earliest {hhmm(s.earliest)}, latest {hhmm(s.latest)}), on {s.seen} of {s.days} days")
    out += [f"  {step}: done on {done} of {days} days" for step, (done, days) in n.steps.items()]
    out += [f"  around {hour:02d}:00: " + ", ".join(f"{room} on {c} days" for room, c in seen.items())
            for hour, seen in n.rooms.items()]
    out.append(f"  at home all day on {n.home_all_day} of {n.days} days")
    return out
