"""Synthetic household-days for the anticipation lab, HAND-LABELLED with the offers a thoughtful human assistant would make.

The household is invented: two adults (``ava``, ``ben``), a child (``kit``) and a guest (``remy``). No real person, place or
event. "Today" is a Monday (2026-03-02); a day offset of 1 is tomorrow.

LABELS are written from the human side, not the engine's: ``expect`` lists ``(person, label)`` where the label is the rule id
that would carry that offer, or a ``+word`` label for something a thoughtful assistant would offer that NO rule can make
(those are misses, by design: recall must be honest). A day with ``expect=[]`` is one where the right thing is to say nothing.

SPLIT. ``DEV`` days were visible while the rules were written. ``HELD`` days were labelled before the harness was first run
and no rule or threshold was changed after reading their results.
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "services" / "zoe-data"))

import anticipation_situations as S  # noqa: E402

TZ = ZoneInfo("Australia/Perth")
TODAY = date(2026, 3, 2)
MEMBERS = (S.Member("ava", name="Ava"), S.Member("ben", name="Ben"), S.Member("kit", adult=False, name="Kit"),
           S.Member("remy", guest=True, name="Remy"))
WEEKDAYS = frozenset({1, 2, 3, 4, 5})
_n = [0]


def _id(prefix: str) -> str:
    _n[0] += 1
    return f"{prefix}{_n[0]}"


def _day(offset: int) -> str:
    return (TODAY + timedelta(days=offset)).isoformat()


def E(owner, title, day=0, start="", end="", place="", vis="family", until=None, att=(), repeats=False):
    return S.Ev(_id("e"), owner, title, _day(day), start, end, _day(until) if until is not None else "", place, vis, tuple(att), repeats)


def A(owner, time, days=WEEKDAYS):
    return S.Alarm(_id("a"), owner, time, days)


def Rm(owner, title, day=None, time=""):
    return S.Reminder(_id("r"), owner, title, _day(day) if day is not None else "", time)


def B(owner, name, day):
    d = TODAY + timedelta(days=day)
    return S.Birthday(_id("b"), owner, name, d.month, d.day)


def day(name, why, *items, now="03:00", typical=None, expect=()):
    h, m = (int(x) for x in now.split(":"))
    pick = lambda cls: tuple(i for i in items if isinstance(i, cls))  # noqa: E731
    facts = S.Facts(now=datetime(TODAY.year, TODAY.month, TODAY.day, h, m, tzinfo=TZ), members=MEMBERS, events=pick(S.Ev),
                    alarms=pick(S.Alarm), reminders=pick(S.Reminder), birthdays=pick(S.Birthday), typical=dict(typical or {}))
    return {"name": name, "why": why, "facts": facts, "expect": [tuple(x) for x in expect]}


DEV = [
    day("flight-this-morning", "an early flight and a 7 am alarm: wake earlier, and when to leave",
        E("ava", "Flight to the coast", 0, "09:40", place="Airport"), A("ava", "07:00"),
        expect=[("ava", "wake"), ("ava", "leave")]),
    day("flight-tomorrow-away-three-nights", "check in tonight, start a packing list, and when to leave",
        E("ava", "Flight to the coast", 1, "11:00", place="Airport", until=4), A("ava", "06:30"),
        expect=[("ava", "flight_checkin"), ("ava", "packing"), ("ava", "leave")]),
    day("bins-tomorrow", "bin day tomorrow and nothing to prompt it", E("ben", "Bins out", 1, repeats=True),
        expect=[("ben", "chore_night_before")]),
    day("bins-already-reminded", "he already has a reminder: say nothing",
        E("ben", "Bins out", 1, repeats=True), Rm("ben", "Put the bins out", 0, "19:00"), expect=[]),
    day("double-booked", "two things at once", E("ava", "Budget review", 0, "14:00", "15:00"), E("ava", "Supplier call", 0, "14:30"),
        expect=[("ava", "double_booked")]),
    day("child-pickup-nobody-free", "both adults are busy when the child's lesson starts: tell both",
        E("kit", "Swimming", 0, "15:30", "16:15"), E("ava", "Budget review", 0, "15:00", "16:00"), E("ben", "Site visit", 0, "15:00", "17:00"),
        expect=[("ava", "child_cover"), ("ben", "child_cover")]),
    day("child-pickup-one-adult-privately-busy", "her appointment is private: she is told, he must learn nothing from Zoe",
        E("kit", "Swimming", 0, "15:30", "16:15"), E("ava", "Specialist", 0, "15:00", "16:00", vis="personal"),
        E("ben", "Site visit", 0, "15:00", "17:00"), expect=[("ava", "child_cover")]),
    day("birthday-thursday", "a friend's birthday in three days and no reminder", B("ava", "Noa Example", 3),
        expect=[("ava", "birthday_reminder")]),
    day("nothing-on", "an ordinary empty day for people who are rarely booked", expect=[]),
    day("dentist-this-afternoon", "an appointment across town", E("ben", "Dentist", 0, "14:00", place="12 Example Street"),
        expect=[("ben", "leave")]),
    day("lunch-with-a-friend", "a lunch needs no help", E("ava", "Lunch with a friend", 0, "12:30", place="Corner Cafe"), expect=[]),
    day("late-night-then-early-call", "out late, on a call at 7: a nudge to head home is kind; a new alarm for a call is not",
        E("ava", "Concert", 0, "20:30", "23:30"), E("ava", "Stand-up call", 1, "07:00"), A("ava", "06:00"),
        expect=[("ava", "wind_down")]),
    day("two-places-fifteen-minutes", "one nudge covers it: wrap up and go",
        E("ben", "Design review", 0, "10:00", "11:00", place="Office"), E("ben", "Check-up", 0, "11:15", place="Clinic"),
        expect=[("ben", "tight_gap_leave")]),
    day("packed-tomorrow", "four things tomorrow for someone who usually has one", now="20:30",
        *[E("ava", f"Session {i + 1}", 1, f"{9 + 2 * i:02d}:00") for i in range(4)], typical={"ava": 1.0},
        expect=[("ava", "full_day_prep")]),
    day("guest-flies-home", "a guest's own flight: Zoe holds no account for them", E("remy", "Flight home", 0, "15:00", place="Airport"),
        expect=[]),
    day("child-excursion-train", "the child's train this morning", E("kit", "Excursion train", 0, "08:30", place="Station"),
        expect=[("kit", "leave")]),
    day("early-meeting-no-alarm", "a 7:30 start and no alarm at all", E("ben", "Breakfast meeting", 0, "07:30"),
        expect=[("ben", "alarm_for_event")]),
    day("road-trip-tomorrow", "three days away by car: a packing list; nobody needs telling when to leave their own house",
        E("ava", "Trip to the hills", 1, "09:00", until=3), expect=[("ava", "packing")]),
]

HELD = [
    day("six-am-flight-private", "a 6 am flight: she must be up at half past two, and it is her private event",
        E("ava", "Flight to the coast", 1, "06:00", place="Airport", vis="personal"), A("ava", "06:30"),
        now="20:30", expect=[("ava", "wake"), ("ava", "leave")]),
    day("asked-to-a-school-meeting", "he asked her to a meeting that overlaps her own",
        E("ava", "Budget review", 0, "15:00", "16:00"), E("ben", "School meeting", 0, "15:30", att=("ava",)),
        expect=[("ava", "clash_heads_up")]),
    day("birthday-today", "it is today and nothing is set", B("ava", "Noa Example", 0), expect=[("ava", "birthday_reminder")]),
    day("birthday-already-in-hand", "she has a reminder about the card", B("ava", "Noa Example", 3), Rm("ava", "Card for Noa", 2),
        expect=[]),
    day("football-this-afternoon", "the child's game, an adult free: nothing to do", E("kit", "Football", 0, "16:00", place="Oval"), expect=[]),
    day("out-late-then-dawn-flight", "dinner until nearly midnight, a 7 am flight: all four are worth saying",
        E("ben", "Dinner out", 0, "19:00", "23:45"), E("ben", "Flight to the coast", 1, "07:00", place="Airport"), A("ben", "06:00"),
        expect=[("ben", "wake"), ("ben", "leave"), ("ben", "wind_down"), ("ben", "flight_checkin")]),
    day("both-adults-travelling-child-has-piano", "who takes the child, and each their own leaving time",
        E("ava", "Flight to the coast", 0, "14:00", place="Airport", until=1), E("ben", "Train to the city", 0, "14:30", place="Station"),
        E("kit", "Piano", 0, "15:00", "15:45"),
        expect=[("ava", "leave"), ("ava", "packing"), ("ava", "child_cover"), ("ben", "leave"), ("ben", "child_cover")]),
    day("clear-day-for-a-busy-person", "a rare clear day and two things she never dated",
        Rm("ava", "Sort the shed"), Rm("ava", "Renew the library card"), typical={"ava": 3.0}, expect=[("ava", "empty_day_catchup")]),
    day("a-day-off", "an all-day 'day off' is not an empty day to fill", E("ava", "Day off", 0), Rm("ava", "Sort the shed"),
        typical={"ava": 3.0}, expect=[]),
    day("recycling-tomorrow-asked-at-night", "8:30 pm, the evening nudge has passed: a morning one would still help",
        E("ava", "Recycling", 1, repeats=True), now="20:30", expect=[("ava", "+chore_morning_of")]),
    day("dentist-with-no-address", "no place on the event: a person would still offer a reminder", E("ben", "Dentist", 0, "10:00"),
        expect=[("ben", "+reminder_before_appointment")]),
    day("two-members-busy-at-once", "her private hour and his gym hour are nobody's clash",
        E("ava", "Counselling", 0, "10:00", "11:00", vis="personal"), E("ben", "Gym", 0, "10:00", "11:00"), expect=[]),
    day("conference-and-a-call", "an all-day conference is not a double booking", E("ava", "Conference", 0), E("ava", "Supplier call", 0, "10:00"),
        expect=[]),
    day("school-assembly", "an ordinary school morning", E("kit", "School assembly", 0, "08:45", place="School hall"),
        E("ava", "Work", 0, "09:00", "17:00"), expect=[]),
    day("a-normal-busy-day", "four things is her normal", *[E("ava", f"Session {i + 1}", 0, f"{9 + 2 * i:02d}:00") for i in range(4)],
        typical={"ava": 4.0}, expect=[]),
    day("interview-tomorrow", "an interview across town tomorrow at 8",
        E("ben", "Interview", 1, "08:00", place="Example Tower"), A("ben", "06:00"), now="20:30", expect=[("ben", "leave")]),
    day("party-then-a-slow-morning", "a late night with nothing early after it",
        E("ava", "Party", 0, "20:00", "23:59"), E("ava", "Brunch", 1, "11:00"), expect=[]),
]

DAYS = DEV + HELD
