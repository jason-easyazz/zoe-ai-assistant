"""The one pure next-fire function (``schedule.reminder_next_fire``) and the recurrence maths it mirrors from production.

No sockets, no processes, no clock: every instant is written out. Each rule has a case at its edge (month ends, the leap day, a
daylight change forward and back, the end of a repeat), and the mirror is checked against production's own
``services/zoe-data/reminder_recurrence.py`` on a grid whenever that file is in the tree. A deliberately wrong rule run through the
same grid must disagree: the comparison can see a drift.
"""
from __future__ import annotations

import importlib.util
import itertools
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from zoe_os import schedule as sch

UTC, LON, SYD = timezone.utc, ZoneInfo("Europe/London"), ZoneInfo("Australia/Sydney")
NINE = (9, 0)
PROD = Path(__file__).resolve().parents[3] / "services" / "zoe-data" / "reminder_recurrence.py"


def at(y, mo, d, h=0, mi=0, tz=UTC) -> float:
    return datetime(y, mo, d, h, mi, tzinfo=tz).timestamp()


def fire(row, after, tz=UTC, default=NINE):
    base = {"active": 1, "done": 0, "after": 0.0, "at": None, "date": None, "time": None, "repeat": None, "until": None}
    return sch.reminder_next_fire(base | row, after, tz, default)


def series(row, after, n, tz=UTC):
    out = []
    for _ in range(n):
        after = fire(row, after, tz)
        if after is None:
            break
        out.append(datetime.fromtimestamp(after, tz).strftime("%Y-%m-%d %H:%M"))
    return out


# ---- the forms ---------------------------------------------------------------------------------------------------------------
def test_an_instant_fires_once_then_never():
    assert fire({"at": at(2030, 1, 1, 8)}, at(2030, 1, 1, 7)) == at(2030, 1, 1, 8)
    assert fire({"at": at(2030, 1, 1, 8)}, at(2030, 1, 1, 8)) is None                  # strictly after


def test_a_day_and_time_fires_at_that_wall_time_any_horizon():
    assert fire({"date": "2033-06-15", "time": "10:00"}, at(2030, 1, 1)) == at(2033, 6, 15, 10)      # no 7-day horizon
    assert fire({"date": "2033-06-15", "time": "10:00"}, at(2030, 1, 1), LON) == at(2033, 6, 15, 9)      # BST is UTC+1


def test_a_day_with_no_time_fires_at_the_household_default_which_is_a_setting():
    row = {"date": "2030-02-01"}
    assert fire(row, at(2030, 1, 1)) == at(2030, 2, 1, 9)
    assert sch.reminder_next_fire(row | {"active": 1}, at(2030, 1, 1), UTC, (7, 15)) == at(2030, 2, 1, 7, 15)


def test_a_day_set_for_today_after_the_default_time_fires_at_its_grace_moment_once():
    row = {"date": "2030-01-01", "grace_at": at(2030, 1, 1, 15, 2)}
    assert fire(row, at(2030, 1, 1, 15)) == at(2030, 1, 1, 15, 2)
    assert fire(row, at(2030, 1, 1, 15, 2)) is None


def test_a_time_with_no_day_is_daily_until_done():
    row = {"time": "07:30"}
    assert series(row, at(2030, 1, 1, 8), 3) == ["2030-01-02 07:30", "2030-01-03 07:30", "2030-01-04 07:30"]
    assert fire(row, at(2030, 1, 1, 7)) == at(2030, 1, 1, 7, 30)                        # today, still ahead
    assert fire(row | {"done": 1}, at(2030, 1, 1, 7)) is None


def test_snoozed_wins_and_the_floor_holds_and_off_is_off():
    row = {"time": "07:30", "snoozed_until": at(2030, 1, 1, 7, 45), "after": at(2030, 1, 1, 7, 45)}
    assert fire(row, at(2030, 1, 1, 7, 31)) == at(2030, 1, 1, 7, 45)
    assert fire(row, at(2030, 1, 1, 7, 45)) == at(2030, 1, 2, 7, 30)
    assert fire({"at": at(2030, 1, 1, 8), "after": at(2030, 1, 1, 9)}, at(2030, 1, 1)) is None
    assert fire({"time": "07:30", "active": 0}, at(2030, 1, 1)) is None


# ---- recurrence edges ---------------------------------------------------------------------------------------------------------
def rrule(rule, anchor, after, n=4, tz=UTC, **row):
    return series({"repeat": rule, "date": anchor, "time": row.pop("time", "08:00"), **row}, after, n, tz)


def test_monthly_on_the_31st_skips_short_months_and_minus_one_is_always_the_last_day():
    assert rrule("FREQ=MONTHLY;BYMONTHDAY=31", "2030-01-31", at(2030, 1, 1)) == [
        "2030-01-31 08:00", "2030-03-31 08:00", "2030-05-31 08:00", "2030-07-31 08:00"]
    assert rrule("FREQ=MONTHLY;BYMONTHDAY=-1", "2028-01-31", at(2028, 1, 1)) == [
        "2028-01-31 08:00", "2028-02-29 08:00", "2028-03-31 08:00", "2028-04-30 08:00"]


def test_a_leap_day_yearly_fires_in_leap_years_only():
    assert rrule("FREQ=YEARLY", "2028-02-29", at(2028, 1, 1), 3) == ["2028-02-29 08:00", "2032-02-29 08:00", "2036-02-29 08:00"]


def test_every_n_weekdays_and_the_nth_weekday_of_the_month():
    assert rrule("FREQ=DAILY;INTERVAL=3", "2030-01-01", at(2030, 1, 1), 3) == [
        "2030-01-01 08:00", "2030-01-04 08:00", "2030-01-07 08:00"]
    assert rrule("FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR", "2030-01-04", at(2030, 1, 4, 9), 2) == [   # a Friday, after 08:00
        "2030-01-07 08:00", "2030-01-08 08:00"]
    assert rrule("FREQ=WEEKLY;INTERVAL=2;BYDAY=TU", "2030-01-01", at(2030, 1, 1), 3) == [
        "2030-01-01 08:00", "2030-01-15 08:00", "2030-01-29 08:00"]
    assert rrule("FREQ=MONTHLY;BYDAY=-1FR", "2030-01-25", at(2030, 1, 1), 2) == ["2030-01-25 08:00", "2030-02-22 08:00"]


def test_the_end_of_a_repeat_is_the_last_day_it_may_fire():
    assert rrule("FREQ=DAILY", "2030-01-01", at(2030, 1, 1), 9, until="2030-01-03") == [
        "2030-01-01 08:00", "2030-01-02 08:00", "2030-01-03 08:00"]


def test_daylight_saving_forward_shifts_a_wall_time_in_the_gap_and_keeps_the_others():
    # London 2027-03-28: 01:00 GMT -> 02:00 BST. 01:30 does not exist: read with the offset before the gap, so 02:30 BST.
    assert fire({"repeat": "FREQ=DAILY", "date": "2027-03-27", "time": "01:30"}, at(2027, 3, 27, 12), LON) == at(2027, 3, 28, 1, 30)
    assert rrule("FREQ=DAILY", "2027-03-27", at(2027, 3, 27), 3, LON, time="09:00") == [
        "2027-03-27 09:00", "2027-03-28 09:00", "2027-03-29 09:00"]                         # 09:00 local either side
    assert fire({"repeat": "FREQ=DAILY", "date": "2027-03-27", "time": "09:00"}, at(2027, 3, 27, 10), LON) == at(2027, 3, 28, 8)


def test_daylight_saving_back_fires_an_ambiguous_wall_time_once_at_its_first_instance():
    # London 2027-10-31: 02:00 BST -> 01:00 GMT. 01:30 happens twice: the first (BST, 00:30 UTC), and only once that day.
    row = {"repeat": "FREQ=DAILY", "date": "2027-10-30", "time": "01:30"}
    assert fire(row, at(2027, 10, 30, 12), LON) == at(2027, 10, 31, 0, 30)
    assert fire(row, at(2027, 10, 31, 0, 30), LON) == at(2027, 11, 1, 1, 30)


def test_the_first_occurrence_ignores_interval_and_never_lands_in_the_past():
    rule = sch.parse_rrule("FREQ=WEEKLY;INTERVAL=2;BYDAY=TU")
    now = datetime(2030, 1, 2, 12, tzinfo=UTC)                                              # a Wednesday
    assert sch.first_occurrence_date(rule, now.date(), 8, 0, now, UTC) == date(2030, 1, 8)
    assert sch.first_occurrence_date(sch.parse_rrule("FREQ=DAILY"), now.date(), 8, 0, now, UTC) == date(2030, 1, 3)


@pytest.mark.parametrize("bad", ["FREQ=HOURLY", "FREQ=DAILY;COUNT=3", "FREQ=DAILY;UNTIL=20300101", "FREQ=WEEKLY;BYDAY=1MO",
                                 "FREQ=MONTHLY;BYDAY=MO,TU", "FREQ=DAILY;INTERVAL=53", "FREQ=WEEKLY;BYDAY=MO,,TU", "every day", ""])
def test_outside_the_subset_is_refused_never_half_honoured(bad):
    assert sch.parse_rrule(bad) is None


# ---- the mirror: production's own module on the same grid ---------------------------------------------------------------------
RULES = ["FREQ=DAILY", "FREQ=DAILY;INTERVAL=3", "FREQ=WEEKLY", "FREQ=WEEKLY;BYDAY=MO,WE,FR", "FREQ=WEEKLY;INTERVAL=2;BYDAY=TU",
         "FREQ=MONTHLY", "FREQ=MONTHLY;BYMONTHDAY=31", "FREQ=MONTHLY;BYMONTHDAY=-1", "FREQ=MONTHLY;BYDAY=2TU",
         "FREQ=MONTHLY;INTERVAL=3;BYDAY=-1FR", "FREQ=YEARLY", "FREQ=YEARLY;INTERVAL=2"]
ANCHORS = [date(2027, 1, 31), date(2028, 2, 29), date(2027, 3, 27), date(2027, 10, 30)]
TIMES = [(1, 30), (2, 30), (9, 0)]


def _grid(mod, tz):
    out = []
    for raw, anchor, (h, m), days in itertools.product(RULES, ANCHORS, TIMES, (0, 1, 40, 400)):
        after = datetime(anchor.year, anchor.month, anchor.day, 12, tzinfo=tz) + timedelta(days=days)
        nxt = mod.next_occurrence(mod.parse_rrule(raw), anchor, h, m, after, tz)
        out.append(None if nxt is None else nxt.astimezone(UTC).isoformat())
    return out


@pytest.fixture(scope="module")
def production():
    if not PROD.is_file():
        pytest.skip("services/zoe-data/reminder_recurrence.py is not in this tree: the mirror is unproven here")
    spec = importlib.util.spec_from_file_location("prod_recurrence", PROD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)                     # stdlib only: no zoe-data import chain
    return mod


@pytest.mark.parametrize("tz", [UTC, LON, SYD], ids=["utc", "london", "sydney"])
def test_the_mirror_agrees_with_production_on_every_point_of_the_grid(production, tz):
    assert _grid(sch, tz) == _grid(production, tz)
    for raw in RULES + ["FREQ=HOURLY", "FREQ=DAILY;COUNT=2", "RRULE:FREQ=weekly;byday=mo"]:
        assert sch.parse_rrule(raw) == production.parse_rrule(raw)


def test_control_a_deliberately_wrong_rule_disagrees_with_production_on_the_grid(production, monkeypatch):
    """Day 31 that rolls into a short month's last day (a plausible bug) must be visible to the grid."""
    real = sch._month_candidate

    def rolls_over(rule, anchor, year, month):
        hit = real(rule, anchor, year, month)
        return hit if hit or rule["byday"] else date(year, month, 28)
    monkeypatch.setattr(sch, "_month_candidate", rolls_over)
    assert _grid(sch, UTC) != _grid(production, UTC)


def test_control_a_fixed_24_hour_step_is_caught_by_the_daylight_test():
    """The plausible wrong rule "next = last + 24 h" lands an hour off across the change: the daylight test would fail on it."""
    first = at(2027, 3, 27, 9, tz=LON)
    right = fire({"repeat": "FREQ=DAILY", "date": "2027-03-27", "time": "09:00"}, first, LON)
    assert first + 86400 != right and right - first == 23 * 3600


def test_a_snooze_at_or_before_the_rows_floor_never_fires():
    """``after`` is the row's floor for EVERY form, a snooze included: a snooze at or before it is not a fire time."""
    row = {"date": "2030-01-08", "time": "09:00", "after": at(2030, 1, 7, 12)}
    assert fire(row | {"snoozed_until": at(2030, 1, 7, 13)}, at(2030, 1, 7, 10)) == at(2030, 1, 7, 13)     # past the floor: wins
    assert fire(row | {"snoozed_until": at(2030, 1, 7, 12)}, at(2030, 1, 7, 10)) == at(2030, 1, 8, 9)     # AT the floor: ignored
    assert fire(row | {"snoozed_until": at(2030, 1, 7, 11)}, at(2030, 1, 7, 10)) == at(2030, 1, 8, 9)     # before it: ignored


def test_control_a_snooze_that_ignores_the_floor_is_caught(monkeypatch):
    """The old order (snooze compared with the caller's ``after`` only) returns the forbidden time."""
    row = {"active": 1, "done": 0, "after": at(2030, 1, 7, 12), "snoozed_until": at(2030, 1, 7, 11), "at": None,
           "date": "2030-01-08", "time": "09:00", "repeat": None, "until": None}
    old = row["snoozed_until"] if row["snoozed_until"] > at(2030, 1, 7, 10) else None
    assert old == at(2030, 1, 7, 11) != sch.reminder_next_fire(row, at(2030, 1, 7, 10), UTC, NINE)
