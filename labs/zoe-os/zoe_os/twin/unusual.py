"""What is unusual today: departures from a person's own baseline, and nothing else.

    result = unusual(twin, "2030-03-25")
    for d in result.departures: print(d.text)

Every line has one shape: THIS differs from the last N days in THIS way, with the count that
makes it unusual. It is NEVER a diagnosis and never a guess about why. There is no code path
that produces a reason: the sentences are fixed templates filled with clock times and counts.
What a late morning means is for the person, not for this module.

It is built to be quiet:
* a time is reported only when it is beyond EVERY one of the baseline days, by more than a
  margin (the width of that person's own middle 80 %, at least ``MIN_MARGIN`` minutes). Being
  a bit early or late is not a departure;
* something missing is reported only if it happened on at least ``USUAL`` of the baseline
  days, and only once the day is far enough along that it would normally have happened;
* something present is reported as rare only if it happened on at most ``RARE`` of them;
* no baseline (not enough days yet) means silence, and a person who has not enabled the twin
  is never mentioned in a departure at all.

WHO IS SHOWN A LINE (owner decision 2026-11-14, section 12 of the record): a line about a
person is shown to THAT PERSON, and, for a child, to that child's guardians. To nobody else:
not another adult of the household, not a guest, not a skill. ``unusual`` computes; a surface
must pass its result through ``shown_to`` and show only what comes back.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..capability import CHILD, GUEST, Person
from .baseline import OK, TIMES, Twin, day_type, hhmm

MIN_MARGIN = 20        # minutes beyond the most extreme baseline day before a time is "unusual"
USUAL, RARE = 0.9, 0.1
END_OF_DAY = 28 * 60   # 04:00 the next morning: the twin's day boundary


@dataclass(frozen=True)
class Departure:
    person: str
    what: str            # "up", "left", "step:morning/teeth", ...
    like_today: int      # how many of the baseline days were like today in this respect
    days: int            # the baseline days
    text: str


@dataclass
class Unusual:
    day: str
    departures: list[Departure] = field(default_factory=list)
    silent: dict[str, str] = field(default_factory=dict)      # person -> why nothing can be said


def shown_to(result: Unusual, viewer: str, people: dict[str, Person]) -> list[Departure]:
    """The departures ``viewer`` may be shown: their own, and those of a child they are a
    guardian of. An unknown viewer, a guest or a skill gets nothing."""
    me = people.get(viewer)
    if me is None or me.role == GUEST:
        return []
    return [d for d in result.departures if _may_show(viewer, people.get(d.person))]


def _may_show(viewer: str, about: Person | None) -> bool:
    return about is not None and (about.id == viewer or (about.role == CHILD and viewer in about.guardians))


def unusual(twin: Twin, day: str, *, as_of: int = END_OF_DAY) -> Unusual:
    """Departures on ``day`` for everyone the twin has a baseline for. ``as_of`` is minutes since
    that day's midnight: what has not had time to happen yet is not reported as missing."""
    out, kind = Unusual(day), day_type(day)
    label = "working days" if kind == "workday" else "weekend days"
    for person in sorted(twin.days):
        base = twin.normal(person, kind, before=day)
        if base.status != OK:
            out.silent[person] = base.status
            continue
        today, n = twin.days[person].get(day, {}), base.days
        last = f"the last {n} {label}"

        def add(what: str, like: int, text: str) -> None:
            out.departures.append(Departure(person, what, like, n, f"{person}: {text}"))

        for name in TIMES:
            stat, mine = base.times.get(name), today.get(name)
            seen = stat.seen if stat else 0
            if mine is not None and mine[0] <= as_of:
                at = mine[0]
                if seen <= RARE * n:
                    add(name, seen, f"'{name}' at {hhmm(at)}. That happened on {seen} of {last}.")
                elif stat:
                    margin = max(MIN_MARGIN, stat.high - stat.low)
                    if at > stat.latest + margin:
                        add(name, 0, f"'{name}' at {hhmm(at)}. Later than on any of {last} "
                                     f"(the latest was {hhmm(stat.latest)}), by {at - stat.latest} minutes.")
                    elif at < stat.earliest - margin:
                        add(name, 0, f"'{name}' at {hhmm(at)}. Earlier than on any of {last} "
                                     f"(the earliest was {hhmm(stat.earliest)}), by {stat.earliest - at} minutes.")
            elif mine is None and stat and seen >= USUAL * n and as_of > stat.latest + MIN_MARGIN:
                add(name, n - seen, f"no '{name}' so far today. It happened on {seen} of {last}.")
        for step, (done, came_up) in base.steps.items():
            if done < USUAL * n:
                continue                                    # not a firm habit: say nothing
            mine = today.get(f"step:{step}")
            if mine is None and came_up >= USUAL * n and as_of >= END_OF_DAY:
                add(f"step:{step}", n - came_up, f"no record of {step} today. It came up on {came_up} of {last}.")
            elif mine is not None and mine[0] is False and as_of >= END_OF_DAY:
                add(f"step:{step}", came_up - done, f"{step} was not done. It was done on {done} of {last}.")
    out.departures.sort(key=lambda d: (d.like_today / d.days, d.person, d.what))
    return out
