"""anticipation_situations: one person's next 36 hours of facts -> typed situations. Pure; placeholder household only
(two adults ``ava`` and ``ben``, a child ``kit``, a guest ``remy``).

Negative controls (run when written; each turns the named test red):
  * make ``view`` return ``facts`` unchanged -> test_another_members_private_event_never_reaches_a_situation,
    test_a_busy_event_is_a_bare_block, test_a_guest_sees_nothing;
  * drop ``and person in o.attendees`` in the clash loop -> test_clash_needs_the_person_to_be_asked;
  * drop the ``CHILD_EVENT_MAX_MIN`` test -> test_child_uncovered_only_when_every_adult_is_visibly_busy;
  * drop ``not covered and`` in the birthday loop -> test_birthday_without_a_reminder;
  * replace ``now_m < spans[e.id][0] <= end_m`` with ``True`` -> test_only_the_next_36_hours.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import anticipation_situations as S  # noqa: E402

pytestmark = pytest.mark.ci_safe

TZ = ZoneInfo("Australia/Perth")
NOW = datetime(2026, 11, 9, 20, 0, tzinfo=TZ)              # Monday evening
TUE, WED = "2026-11-10", "2026-11-11"
HOUSE = (S.Member("ava", name="Ava"), S.Member("ben", name="Ben"), S.Member("kit", adult=False, name="Kit"),
         S.Member("remy", guest=True, name="Remy"))
WEEKDAYS = frozenset({1, 2, 3, 4, 5})


def ev(id, owner, title, day=TUE, start="", **kw):
    return S.Ev(id, owner, title, day, start, **kw)


def facts(*events, now=NOW, **kw):
    return S.Facts(now=now, members=HOUSE, events=tuple(events), **kw)


def kinds(f, person):
    return sorted(s.kind for s in S.situations(f, person))


def one(f, person, kind):
    hits = [s for s in S.situations(f, person) if s.kind == kind]
    assert len(hits) == 1, [s.kind for s in S.situations(f, person)]
    return hits[0]


# -- the clamp -------------------------------------------------------------------------------------------------------------------
def test_another_members_private_event_never_reaches_a_situation():
    # Ben is asked to Ava's private appointment slot only in the sense that the times overlap: he must learn nothing
    private = ev("p1", "ava", "Specialist review", start="15:00", place="Clinic", visibility="personal", attendees=("ben",))
    kid = ev("k1", "kit", "Swimming", start="15:30", end="16:15")
    bens = ev("b1", "ben", "Site visit", start="15:00", end="17:00")
    f = facts(private, kid, bens)
    for s in S.situations(f, "ben"):
        assert "event:p1" not in s.sources, s
    assert "child_uncovered" not in kinds(f, "ben")       # from Ben's side Ava looks free: no situation, no hint
    assert "clash" not in kinds(f, "ben")
    assert "child_uncovered" in kinds(f, "ava")            # Ava sees her own event and Ben's family one
    assert all(e.id != "p1" for e in S.view(f, "ben").events)


def test_a_busy_event_is_a_bare_block():
    busy = ev("p1", "ava", "Specialist review", start="15:00", place="Clinic", visibility="busy")
    f = facts(busy, ev("k1", "kit", "Swimming", start="15:30", end="16:15"), ev("b1", "ben", "Site visit", start="15:00", end="17:00"))
    seen = next(e for e in S.view(f, "ben").events if e.id == "p1")
    assert (seen.title, seen.place) == ("", "")
    s = one(f, "ben", "child_uncovered")                  # Ben may know she is busy, and that is all the situation holds
    assert "event:p1" in s.sources and "Specialist" not in repr(s)


def test_a_guest_sees_nothing():
    f = facts(ev("e1", "remy", "Flight home", start="09:00", place="Airport"), ev("e2", "ava", "Dinner", start="19:00"))
    assert S.situations(f, "remy") == [] and S.situations(f, "nobody") == []
    assert S.view(f, "remy").events == ()


def test_alarms_reminders_and_birthdays_are_the_persons_own():
    f = facts(alarms=(S.Alarm("a1", "ava", "07:00", WEEKDAYS),), reminders=(S.Reminder("r1", "ava", "Call the bank"),),
              birthdays=(S.Birthday("bd1", "ava", "Noa Example", 11, 11),))
    v = S.view(f, "ben")
    assert (v.alarms, v.reminders, v.birthdays) == ((), (), ())
    assert kinds(f, "ben") == [] and kinds(f, "ava") == ["birthday"]


# -- each kind ---------------------------------------------------------------------------------------------------------------------
def test_travel_day_and_early_start():
    f = facts(ev("e1", "ava", "Flight to the coast", start="09:40", place="Airport"), alarms=(S.Alarm("a1", "ava", "07:00", WEEKDAYS),))
    t = one(f, "ava", "travel_day")
    assert t.data["travel"] == "flight" and t.data["leave_by"] == "07:10" and t.sources == ("event:e1",)
    e = one(f, "ava", "early_start")
    assert e.data == {"start": "09:40", "wake": "06:10", "usual": "07:00"} and e.sources == ("event:e1", "alarm:a1")
    assert "no_alarm" not in kinds(f, "ava")


def test_no_alarm_before_a_morning_event():
    f = facts(ev("e1", "ava", "Breakfast meeting", start="07:30"))
    s = one(f, "ava", "no_alarm")
    assert s.data == {"start": "07:30", "wake": "06:00"}
    assert kinds(facts(ev("e1", "ava", "Lunch", start="12:30")), "ava") == []            # not a morning start
    late_alarm = facts(ev("e1", "ava", "Breakfast meeting", start="07:30"), alarms=(S.Alarm("a1", "ava", "05:30", WEEKDAYS),))
    assert kinds(late_alarm, "ava") == []                                                  # an alarm already early enough


def test_double_booked_and_tight_gap():
    f = facts(ev("e1", "ava", "Review", start="10:00", end="11:00"), ev("e2", "ava", "Call", start="10:30"))
    assert one(f, "ava", "double_booked").sources == ("event:e1", "event:e2")
    g = facts(ev("e1", "ava", "Review", start="10:00", end="11:00", place="Office"),
              ev("e2", "ava", "Check-up", start="11:15", place="Clinic"))
    assert one(g, "ava", "tight_gap").data == {"gap": 15, "leave_at": "11:00", "next_start": "11:15"}
    same_place = facts(ev("e1", "ava", "Review", start="10:00", end="11:00", place="Office"),
                       ev("e2", "ava", "Stand-up", start="11:15", place="office"))
    assert "tight_gap" not in kinds(same_place, "ava")


def test_clash_needs_the_person_to_be_asked():
    mine = ev("e1", "ava", "Review", start="15:00", end="16:00")
    theirs = ev("e2", "ben", "School meeting", start="15:30", attendees=("ava",))
    assert one(facts(mine, theirs), "ava", "clash").sources == ("event:e1", "event:e2")
    # two members merely busy at the same hour is not a clash (the default visibility is family: it would fire all day)
    assert "clash" not in kinds(facts(mine, ev("e2", "ben", "Gym", start="15:30")), "ava")
    assert "clash" not in kinds(facts(mine, theirs), "ben")


def test_child_uncovered_only_when_every_adult_is_visibly_busy():
    kid = ev("k1", "kit", "Swimming", start="15:30", end="16:15")
    a, b = ev("a1", "ava", "Review", start="15:00", end="16:00"), ev("b1", "ben", "Site visit", start="15:00", end="17:00")
    s = one(facts(kid, a, b), "ava", "child_uncovered")
    assert s.data == {"child": "kit", "child_name": "Kit", "start": "15:30"} and set(s.sources) == {"event:k1", "event:a1", "event:b1"}
    assert "child_uncovered" not in kinds(facts(kid, a), "ava")                            # Ben is free
    assert "child_uncovered" not in kinds(facts(kid, a, b), "kit")                         # never the child's own problem
    school = ev("k1", "kit", "School", start="08:30", end="15:00")
    assert "child_uncovered" not in kinds(facts(school, ev("a1", "ava", "Work", start="09:00", end="17:00"),
                                                ev("b1", "ben", "Work", start="09:00", end="17:00")), "ava")


def test_birthday_without_a_reminder():
    bd = (S.Birthday("bd1", "ava", "Noa Example", 11, 12),)
    s = one(facts(birthdays=bd), "ava", "birthday")
    assert s.data == {"name": "Noa Example", "days_until": 3} and s.sources == ("birthday:bd1",)
    assert kinds(facts(birthdays=bd, reminders=(S.Reminder("r1", "ava", "Card for Noa", "2026-11-11"),)), "ava") == []
    assert kinds(facts(birthdays=(S.Birthday("bd1", "ava", "Noa Example", 11, 20),)), "ava") == []   # past the week


def test_chore_due_with_nothing_to_prompt_it():
    bins = ev("e1", "ava", "Bins out", day=TUE, repeats=True)
    f = facts(bins, now=datetime(2026, 11, 9, 3, 0, tzinfo=TZ))
    assert one(f, "ava", "chore_due").data == {"evening_before": "2026-11-09"}
    assert kinds(f, "ben") == []                                                           # the owner's chore, one offer
    covered = facts(bins, now=datetime(2026, 11, 9, 3, 0, tzinfo=TZ), reminders=(S.Reminder("r1", "ava", "Put the bins out", "2026-11-09", "19:00"),))
    assert kinds(covered, "ava") == []
    assert kinds(facts(bins), "ava") == []                                                 # 8 pm: the evening before has gone


def test_full_day_and_empty_day_are_against_the_persons_usual():
    four = [ev(f"e{i}", "ava", f"Thing {i}", start=f"{9 + 2 * i:02d}:00") for i in range(4)]
    s = one(facts(*four), "ava", "full_day")
    assert s.data == {"count": 4, "first": "09:00"} and len(s.sources) == 4
    assert "full_day" not in kinds(facts(*four, typical={"ava": 4.0}), "ava")            # four is her normal day
    e = one(facts(typical={"ava": 3.0}, reminders=(S.Reminder("r1", "ava", "Sort the shed"),)), "ava", "empty_day")
    assert e.data == {"undated": 1} and e.sources == ("reminder:r1",)
    assert kinds(facts(), "ava") == []                                                     # an empty day is normal for her
    assert kinds(facts(ev("h", "ava", "Day off", day=TUE), typical={"ava": 3.0}), "ava") == []   # an all-day event: not "empty"


def test_late_night_before_an_early_start():
    f = facts(ev("e1", "ava", "Concert", start="20:30", end="23:30"), ev("e2", "ava", "Stand-up", day=WED, start="07:00"),
              alarms=(S.Alarm("a1", "ava", "05:00", WEEKDAYS),))
    s = one(f, "ava", "late_then_early")
    assert s.data == {"late_end": "23:30", "early_start": "07:00"} and s.sources == ("event:e1", "event:e2")
    assert "late_then_early" not in kinds(facts(ev("e1", "ava", "Dinner", start="19:00", end="21:00"),
                                                ev("e2", "ava", "Stand-up", day=WED, start="07:00")), "ava")


# -- the frame ---------------------------------------------------------------------------------------------------------------------
def test_only_the_next_36_hours():
    f = facts(ev("past", "ava", "Flight", day="2026-11-09", start="08:00", place="Airport"),
              ev("far", "ava", "Flight", day="2026-11-12", start="08:00", place="Airport"),
              ev("edge", "ava", "Train", day=WED, start="08:00"))
    assert [s.sources for s in S.situations(f, "ava") if s.kind == "travel_day"] == [("event:edge",)]


def test_deterministic_and_every_situation_names_its_facts():
    f = facts(ev("e1", "ava", "Flight to the coast", start="06:40", place="Airport", end_day=WED),
              ev("e2", "ava", "Call", start="06:50"), ev("k1", "kit", "Swimming", start="06:45", end="07:30"),
              ev("b1", "ben", "Run club", start="06:00", end="08:00"), alarms=(S.Alarm("a1", "ava", "07:00", WEEKDAYS),))
    first = S.situations(f, "ava")
    assert first == S.situations(f, "ava") and len(first) >= 4
    ids = {f"event:{e.id}" for e in f.events} | {"alarm:a1"}
    for s in first:
        assert s.kind in S.KINDS and s.person == "ava" and s.sources and set(s.sources) <= ids
