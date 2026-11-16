"""The day generator: a small household description in, synthetic days of kernel events out.

    days = generate(SAMPLE, days=21, seed=7)        # a list of ``replay.Recording``, one per day

* DETERMINISTIC for a seed: the same description, start date, count and seed give the same
  bytes. Only ``random.Random(seed).random()`` is used, each day from its own sub-seed, so
  day 9 is the same whether 10 or 100 days are generated.
* A person's day is their habits with spread: up, a morning routine, things to take, out,
  back, a meal, bed. Weekends are later and at home. Weekly events land on the calendar the
  evening before.
* AWKWARD DAYS ON PURPOSE. Every third WORKING day is one of six kinds, in rotation, so 18
  working days (25 calendar days from a Monday) hold all of them: a forgotten phone, a sick child at home, a guest, a late night, a flight, a
  missed routine step. ``meta["awkward"]`` on each recording says which, so a test or a
  rehearsal report can name the day.

Clock times are treated as UTC (the lab has no time zone). All ids and labels are invented.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from ..capability import ADULT, CHILD, GUEST, Person
from ..replay import Recording

GENERATOR = 1
AWKWARD = ("forgotten_phone", "sick_child", "guest", "late_night", "flight", "missed_step")
GUEST_ID = "visitor"


@dataclass(frozen=True)
class Habit:
    at: str             # "HH:MM", the usual time
    spread: int = 15    # minutes either side

    @property
    def minute(self) -> int:
        hours, minutes = self.at.split(":")
        return int(hours) * 60 + int(minutes)


@dataclass(frozen=True)
class Member:
    id: str
    role: str = ADULT
    guardians: tuple[str, ...] = ()
    wake: Habit = Habit("06:45")
    leave: Habit | None = Habit("08:00")        # None: usually at home
    back: Habit = Habit("17:30", 30)
    bed: Habit = Habit("22:30", 20)
    routine: tuple[str, ...] = ()               # morning routine steps
    take: tuple[str, ...] = ()                  # things to take when leaving


@dataclass(frozen=True)
class Weekly:
    person: str
    weekday: int        # 0 = Monday
    at: str             # "HH:MM"
    label: str          # an invented label, e.g. "synthetic club"


@dataclass(frozen=True)
class Household:
    members: tuple[Member, ...]
    dinner: Habit = Habit("18:45", 20)
    weekly: tuple[Weekly, ...] = ()
    answers: dict = field(default_factory=dict)       # how each person answers offers, per skill

    @property
    def people(self) -> list[Person]:
        return [Person(m.id, m.role, m.guardians) for m in self.members] + [Person(GUEST_ID, GUEST)]


SAMPLE = Household(
    members=(
        Member("alex", wake=Habit("06:30"), leave=Habit("07:50"), take=("phone", "keys", "laptop")),
        Member("sam", wake=Habit("07:00"), leave=Habit("08:20"), back=Habit("18:10", 30),
               bed=Habit("23:00", 25), take=("phone", "gym bag")),
        Member("kid", CHILD, ("alex", "sam"), wake=Habit("07:10", 10), leave=Habit("08:15", 10),
               back=Habit("15:30", 15), bed=Habit("20:15", 15), routine=("teeth", "shoes"),
               take=("school bag",)),
    ),
    weekly=(Weekly("kid", 2, "16:00", "synthetic club"), Weekly("sam", 4, "06:30", "synthetic early class")),
)


class _Day:
    def __init__(self, house: Household, day: date, rng: random.Random, awkward: str) -> None:
        self.house, self.day, self.rng, self.awkward = house, day, rng, awkward
        self.rows: list[tuple[float, int, dict]] = []
        self.weekend = day.weekday() >= 5
        adults = [m for m in house.members if m.role == ADULT]
        children = [m for m in house.members if m.role == CHILD]
        self.star = (children or adults)[0].id if awkward in ("sick_child", "missed_step") else adults[0].id
        self.carer = adults[-1].id

    def at(self, habit: Habit, shift: int = 0) -> int:
        """The habit's time with its spread: two draws, so the middle is likelier than the ends."""
        offset = (self.rng.random() + self.rng.random() - 1.0) * habit.spread
        return habit.minute + shift + round(offset)

    def add(self, minute: int, kind: str, source: str, person: str | None, payload: dict,
            visibility: str = "private") -> None:
        t = datetime(self.day.year, self.day.month, self.day.day, tzinfo=timezone.utc) + timedelta(minutes=minute)
        self.rows.append((t.timestamp(), len(self.rows), {
            "t": t.timestamp(), "kind": kind, "source": source, "subject": person,
            "visibility": visibility, "payload": payload, "mentions": []}))

    def presence(self, minute: int, person: str, state: str, room: str | None = None, **more) -> None:
        payload = {"state": state, "confidence": 0.95} | ({"room": room} if room else {})
        self.add(minute, "presence", "presence", person, payload, **more)

    def step(self, minute: int, person: str, routine: str, item: str, done: bool) -> None:
        self.add(minute, "routine_step", "routines", person, {"routine": routine, "item": item, "done": done})

    def member(self, m: Member) -> None:
        star, odd = m.id == self.star, self.awkward
        late_wake = 75 if self.weekend else 0
        wake = self.at(m.wake, late_wake)
        if odd == "flight" and star:
            wake = 4 * 60 + int(self.rng.random() * 20)
        self.presence(wake, m.id, "up", "bedroom")
        for i, item in enumerate(m.routine):
            missed = odd == "missed_step" and star and i == 0
            self.step(wake + 10 + 6 * i, m.id, "morning", item, done=not missed)
        self.presence(wake + 25, m.id, "room", "kitchen")
        sick = odd == "sick_child" and m.id in (self.star, self.carer)
        out = m.leave is not None and not self.weekend and not sick
        if odd == "flight" and star:
            out = True
        if out:
            leave = wake + 35 if odd == "flight" and star else max(self.at(m.leave), wake + 30)
            for i, item in enumerate(m.take):
                self.step(wake + 12 + i, m.id, "take-with-you", item, done=False)
                if not (odd == "forgotten_phone" and star and item == "phone"):
                    self.step(leave - 4 + i, m.id, "take-with-you", item, done=True)
            self.presence(leave, m.id, "left")
            if odd == "flight" and star:
                return                              # away overnight: not back, no bed here
            self.presence(max(self.at(m.back), leave + 60), m.id, "arrived")
        elif sick and m.id == self.star:
            for minute in (11 * 60, 14 * 60):
                self.presence(minute + int(self.rng.random() * 30), m.id, "room", "bedroom")
        dinner = self.at(self.house.dinner, 45 if odd == "guest" else 0)
        self.presence(dinner, m.id, "room", "kitchen")
        bed = self.at(m.bed, 150 if odd == "late_night" and star else 0)
        self.presence(max(bed, dinner + 30), m.id, "bed", "bedroom")

    def household(self) -> None:
        if self.awkward == "guest":
            self.presence(17 * 60 + 30 + int(self.rng.random() * 20), GUEST_ID, "arrived", visibility="household")
            self.presence(22 * 60 + int(self.rng.random() * 20), GUEST_ID, "left", visibility="household")
        tomorrow = self.day + timedelta(days=1)
        planned = [(19 * 60, tomorrow, w.person, w.at, w.label)          # on the calendar the evening before
                   for w in self.house.weekly if w.weekday == tomorrow.weekday()]
        if self.awkward == "flight":                                     # booked in the small hours, for today
            planned.append((3 * 60, self.day, self.star, "06:10", "synthetic flight"))
        for n, (minute, when, person, at, label) in enumerate(planned):
            hours, minutes = at.split(":")
            starts = datetime(when.year, when.month, when.day, int(hours), int(minutes), tzinfo=timezone.utc)
            self.add(minute + n, "calendar_change", "calendar", person, {
                "event_id": f"{self.day.isoformat()}-{n}", "change": "created",
                "starts_at": starts.timestamp(), "title": label})


def awkward_for(first: date, index: int) -> str:
    """Every third working day is awkward, cycling through all six kinds. Weekends never are:
    nobody leaves on a weekend, which would hide most of what makes a day awkward."""
    day = first + timedelta(days=index)
    if day.weekday() >= 5:
        return ""
    worked = sum(1 for i in range(index) if (first + timedelta(days=i)).weekday() < 5)
    return AWKWARD[(worked // 3) % len(AWKWARD)] if worked % 3 == 2 else ""


def generate(house: Household, *, days: int, seed: int, start: str = "2030-03-04",
             awkward: dict[int, str] | None = None) -> list[Recording]:
    """``days`` consecutive recordings from ``start`` (a Monday by default). ``awkward`` maps a
    day index to a kind ("" for an ordinary day) and overrides the rotation for that day."""
    first, out = date.fromisoformat(start), []
    for index in range(days):
        day = first + timedelta(days=index)
        kind = (awkward or {}).get(index, awkward_for(first, index))
        if kind and kind not in AWKWARD:
            raise ValueError(f"unknown awkward day {kind!r}")
        maker = _Day(house, day, random.Random(f"{seed}:{day.isoformat()}"), kind)
        for m in house.members:
            maker.member(m)
        maker.household()
        rows = [row for _, _, row in sorted(maker.rows, key=lambda r: (r[0], r[1]))]
        meta = {"generator": GENERATOR, "seed": seed, "day": day.isoformat(),
                "weekday": day.strftime("%a").lower(), "awkward": kind}
        out.append(Recording(house.people, rows, dict(house.answers), meta=meta))
    return out


def household_from(raw: dict) -> Household:
    """A household description from plain data (a JSON file): the same fields as the dataclasses,
    with a habit written as ``"07:00"`` or ``["07:00", 20]``."""
    def habit(value):
        return None if value is None else Habit(value) if isinstance(value, str) else Habit(*value)

    members = tuple(Member(
        m["id"], m.get("role", ADULT), tuple(m.get("guardians", ())),
        **{key: habit(m[key]) for key in ("wake", "leave", "back", "bed") if key in m},
        routine=tuple(m.get("routine", ())), take=tuple(m.get("take", ()))) for m in raw["members"])
    return Household(members, habit(raw.get("dinner", "18:45")),
                     tuple(Weekly(**w) for w in raw.get("weekly", ())), raw.get("answers", {}))
