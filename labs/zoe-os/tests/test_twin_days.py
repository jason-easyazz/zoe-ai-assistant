"""The day generator: deterministic for a seed, plausible on ordinary days, awkward on purpose."""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from zoe_os.replay import Recording, replay
from zoe_os.skills.calendar_headsup import CalendarHeadsUp
from zoe_os.skills.leaving_catch import LeavingCatch
from zoe_os.twin.days import AWKWARD, GUEST_ID, SAMPLE, Habit, generate, household_from

LAB = Path(__file__).parent.parent
DAYS = generate(SAMPLE, days=25, seed=7)


def minute(row) -> int:
    t = datetime.fromtimestamp(row["t"], timezone.utc)
    return t.hour * 60 + t.minute


def first(rec, person, state):
    return next((minute(r) for r in rec.events
                 if r["subject"] == person and r["payload"].get("state") == state), None)


def day_with(kind: str) -> Recording:
    return next(d for d in DAYS if d.meta["awkward"] == kind)


def test_the_same_seed_gives_the_same_bytes_here_and_in_another_process():
    again = generate(SAMPLE, days=25, seed=7)
    assert [d.dumps() for d in again] == [d.dumps() for d in DAYS]
    assert generate(SAMPLE, days=25, seed=8)[0].dumps() != DAYS[0].dumps()
    assert generate(SAMPLE, days=3, seed=7)[2].dumps() == DAYS[2].dumps()      # day 3 of 3 = day 3 of 25
    code = ("import hashlib; from zoe_os.twin.days import SAMPLE, generate; "
            "print(hashlib.sha256(''.join(d.dumps() for d in generate(SAMPLE, days=25, seed=7)).encode()).hexdigest())")
    outs = {subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True, cwd=LAB,
                           env={"PATH": os.environ["PATH"], "PYTHONHASHSEED": seed}).stdout for seed in ("0", "77")}
    import hashlib
    assert outs == {hashlib.sha256("".join(d.dumps() for d in DAYS).encode()).hexdigest() + "\n"}


def test_a_day_survives_a_round_trip_and_says_where_it_came_from():
    rec = Recording.loads(DAYS[2].dumps())
    assert rec.dumps() == DAYS[2].dumps()
    assert rec.meta == {"generator": 1, "seed": 7, "day": "2030-03-06", "weekday": "wed",
                        "awkward": "forgotten_phone"}
    assert [e["t"] for e in rec.events] == sorted(e["t"] for e in rec.events)
    assert {p.id for p in rec.people} == {"alex", "sam", "kid", GUEST_ID}       # invented placeholders only


def test_25_days_hold_every_awkward_kind_and_never_on_a_weekend():
    kinds = [d.meta["awkward"] for d in DAYS]
    assert set(kinds) == set(AWKWARD) | {""} and kinds.count("") == 19
    assert all(not d.meta["awkward"] for d in DAYS if d.meta["weekday"] in ("sat", "sun"))


def test_ordinary_working_days_follow_the_habits_within_their_spread():
    ordinary = [d for d in DAYS if not d.meta["awkward"] and d.meta["weekday"] not in ("sat", "sun")]
    assert len(ordinary) == 13
    for member in SAMPLE.members:
        ups = [first(d, member.id, "up") for d in ordinary]
        assert all(abs(u - member.wake.minute) <= member.wake.spread for u in ups)
        assert len(set(ups)) > 3                                               # spread, not a constant
        assert all(first(d, member.id, "left") > first(d, member.id, "up") for d in ordinary)
    weekend = [d for d in DAYS if d.meta["weekday"] in ("sat", "sun")]
    assert all(first(d, "alex", "left") is None for d in weekend)
    assert min(first(d, "alex", "up") for d in weekend) > max(first(d, "alex", "up") for d in ordinary)


def test_the_forgotten_phone_day_is_caught_by_the_leaving_catch_skill():
    report = replay(day_with("forgotten_phone"), [LeavingCatch()])
    assert report.counts["offers"] == 1 and '"You left without: phone"' in report.text
    assert replay(DAYS[0], [LeavingCatch()]).counts["offers"] == 0             # an ordinary day: silence


def test_the_sick_child_day_keeps_the_child_and_one_adult_at_home():
    rec = day_with("sick_child")
    assert first(rec, "kid", "left") is None and first(rec, "sam", "left") is None
    assert first(rec, "alex", "left") is not None
    assert not [e for e in rec.events if e["subject"] == "kid" and e["payload"].get("routine") == "take-with-you"]


def test_the_guest_day_has_a_guest_everyone_may_see_and_a_later_dinner():
    rec = day_with("guest")
    visits = [e for e in rec.events if e["subject"] == GUEST_ID]
    assert [e["payload"]["state"] for e in visits] == ["arrived", "left"]
    assert {e["visibility"] for e in visits} == {"household"}
    assert not [e for d in DAYS if d.meta["awkward"] != "guest" for e in d.events if e["subject"] == GUEST_ID]


def test_the_late_night_the_flight_and_the_missed_step():
    late = day_with("late_night")
    assert late.events[-1]["subject"] == "alex" and late.events[-1]["payload"]["state"] == "bed"
    assert late.events[-1]["t"] - late.events[0]["t"] > 17.5 * 3600            # past midnight
    flight = day_with("flight")
    assert first(flight, "alex", "up") < 4 * 60 + 20 and first(flight, "alex", "arrived") is None
    assert [e["payload"]["title"] for e in flight.events if e["kind"] == "calendar_change"][0] == "synthetic flight"
    missed = day_with("missed_step")
    steps = {e["payload"]["item"]: e["payload"]["done"] for e in missed.events
             if e["subject"] == "kid" and e["payload"].get("routine") == "morning"}
    assert steps == {"teeth": False, "shoes": True}


def test_a_weekly_event_lands_on_the_calendar_the_evening_before_and_gets_a_heads_up():
    thursday = next(d for d in DAYS if d.meta["weekday"] == "thu" and not d.meta["awkward"])
    (change,) = [e for e in thursday.events if e["kind"] == "calendar_change"]
    assert change["subject"] == "sam" and change["payload"]["title"] == "synthetic early class"
    report = replay(thursday, [CalendarHeadsUp()])
    assert report.counts["offers"] == 1 and "Tomorrow starts at 06:30" in report.text


def test_every_generated_day_replays_cleanly_and_a_person_can_be_left_out():
    for rec in DAYS:
        assert replay(rec, [LeavingCatch(), CalendarHeadsUp()]).counts["refusals"] == 0
    rec = day_with("forgotten_phone")
    rec.off = {"alex": ["*"]}                                                  # ALEX enabled nothing
    assert replay(rec, [LeavingCatch()]).counts["offers"] == 0


def test_a_household_can_be_described_in_plain_data_and_bad_input_is_refused():
    house = household_from({
        "members": [{"id": "pat", "wake": "05:30", "leave": ["06:15", 5], "take": ["keys"]},
                    {"id": "robin", "leave": None}],
        "weekly": [{"person": "pat", "weekday": 0, "at": "09:00", "label": "synthetic meeting"}]})
    assert house.members[0].leave == Habit("06:15", 5) and house.members[1].leave is None
    (day,) = generate(house, days=1, seed=1, awkward={0: ""})
    assert first(day, "robin", "left") is None and abs(first(day, "pat", "up") - 330) <= 15
    with pytest.raises(ValueError, match="unknown awkward day"):
        generate(house, days=1, seed=1, awkward={0: "earthquake"})
