"""The durable alarm model (``zoe_os.alarm_rows``) in-process, and the pure ring maths under it (``schedule.alarm_state``).

Socket-free and process-free (a "restart" is a new kernel on the same file; the real-process kill test is in ``test_authority.py``).
Synthetic members and labels only.
"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from authority import AUTH_ACTS, AUTHORITY_BRAIN, ADAPTERS, DurableKeeper, DurableLists
from zoe_os import schedule as sch
from zoe_os.alarm_rows import MAX_SNOOZES, DurableAlarms
from zoe_os.capability import Person
from zoe_os.events import Event, Kind, Visibility
from zoe_os.kernel import Kernel
from zoe_os.spine import Refused

A, B = "member-a", "member-b"
UTC, LON = timezone.utc, ZoneInfo("Europe/London")


def at(y, mo, d, h=0, mi=0, tz=UTC) -> float:
    return datetime(y, mo, d, h, mi, tzinfo=tz).timestamp()


T0 = at(2030, 1, 7, 6, 0)                     # Monday 06:00 UTC


def seq_n(owner) -> int:
    """A fresh utterance text per request: forgotten words are shielded for every publisher."""
    owner._n = getattr(owner, "_n", 0) + 1
    return owner._n


class House:
    def __init__(self, path, clock):
        self.k = Kernel(str(path), [Person(A), Person(B)], clock=lambda: clock[0])
        self.lists = DurableLists(self.k.spine).register(self.k)
        self.al = DurableAlarms(self.k.spine, lambda: clock[0]).register(self.k)
        self.al.rebuild()
        for m in (*ADAPTERS, AUTHORITY_BRAIN):
            self.k.grants.install(m)
        self.k.install(DurableKeeper(self.lists, durable=(self.al,)))
        for p in (A, B):
            self.k.grants.grant(p, "brain", acts=list(AUTH_ACTS))

    def act(self, person, action, **args):
        seq = self.k.spine.publish(Event(Kind.UTTERANCE, "voice", person, Visibility.PRIVATE, {"text": f"synthetic alarm words {seq_n(self)}"})).seq
        return self.k.actions.act("brain", action, person, {**args, "asked": seq}, caused_by=(seq,))

    def state(self, rid):
        return self.al.state(self.al.rows[rid])[0]


@pytest.fixture
def clock():
    return [T0]


@pytest.fixture
def house(tmp_path, clock):
    h = House(tmp_path / "k.db", clock)
    yield h
    h.k.close()


def rid(receipt):
    return receipt.proof["id"]


# ---- the pure ring maths -----------------------------------------------------------------------------------------------------
def row(**kw):
    return {"time": "07:00", "repeat": "once", "date": "2030-01-07", "enabled": 1, "after": T0, "snoozed_until": None,
            "snooze_of": None, "snooze_count": 0} | kw


def test_an_alarm_rings_for_ten_minutes_then_stops_by_itself_and_a_one_off_is_spent():
    r = row()
    assert sch.alarm_state(r, at(2030, 1, 7, 6, 59), UTC) == ("idle", None, at(2030, 1, 7, 7))
    assert sch.alarm_state(r, at(2030, 1, 7, 7, 5), UTC)[:2] == ("ringing", at(2030, 1, 7, 7))
    assert sch.alarm_state(r, at(2030, 1, 7, 7, 10), UTC) == ("idle", None, None)          # RING_MAX_S: stopped, and spent


def test_repeat_rules_mirror_production_and_a_weekday_alarm_skips_the_weekend():
    assert [sch.alarm_repeat(x) for x in ("once", "Weekdays", "days:mon,wed", "days:3,1", "days:1,2,3,4,5,6,7", "days:8", "hourly")] \
        == ["once", "weekdays", "days:1,3", "days:1,3", "daily", None, None]
    fri_after = at(2030, 1, 11, 8)                                                           # Friday 08:00
    assert sch.alarm_occurrence("07:00", "weekdays", None, fri_after, UTC) == at(2030, 1, 14, 7)


def test_an_alarm_keeps_its_wall_time_across_a_daylight_change():
    assert sch.alarm_occurrence("07:00", "daily", None, at(2027, 3, 27, 8), LON) == at(2027, 3, 28, 6)       # 07:00 BST
    assert sch.alarm_occurrence("01:30", "daily", None, at(2027, 3, 27, 8), LON) == at(2027, 3, 28, 1, 30)   # in the gap: shifted


def test_a_snooze_rings_again_after_it_and_dismiss_ends_the_occurrence_not_the_alarm():
    r = row(repeat="daily", date=None, snoozed_until=at(2030, 1, 7, 7, 9), snooze_of=at(2030, 1, 7, 7), after=at(2030, 1, 7, 7))
    assert sch.alarm_state(r, at(2030, 1, 7, 7, 5), UTC) == ("snoozed", at(2030, 1, 7, 7), at(2030, 1, 7, 7, 9))
    assert sch.alarm_state(r, at(2030, 1, 7, 7, 10), UTC)[0] == "ringing"
    assert sch.alarm_state(r | {"snoozed_until": None}, at(2030, 1, 7, 7, 10), UTC) == ("idle", None, at(2030, 1, 8, 7))


def test_control_a_ring_window_that_never_closes_is_caught():
    """Without the RING_MAX_S window a missed occurrence would ring hours late: the first test would see it."""
    late = at(2030, 1, 7, 11)
    assert sch.alarm_state(row(repeat="daily", date=None), late, UTC)[0] == "idle"
    assert sch.alarm_occurrence("07:00", "daily", None, T0, UTC) <= late             # what a window-free rule would ring


# ---- the actions -------------------------------------------------------------------------------------------------------------
def test_several_alarms_per_person_and_todays_argument_still_works(house):
    first = house.act(A, "alarm.set", time="07:00")
    second = house.act(A, "alarm.set", time="06:30", repeat="weekdays", label="gym")
    assert first.verified and first.proof["alarm"] == "07:00" and second.verified
    assert sorted(r["time"] for r in house.al.visible_to(A)) == ["06:30", "07:00"] and house.al.visible_to(B) == []
    assert house.al.rows[rid(first)]["date"] == "2030-01-07"                         # 07:00 is still ahead today


@pytest.mark.parametrize("args,why", [({"time": "05:00", "date": "2030-01-07"}, "already passed"),
                                      ({"time": "07:00", "repeat": "hourly"}, "not a repeat")])
def test_what_production_refuses_is_refused_before_anything_is_written(house, args, why):
    with pytest.raises(Refused, match=why):
        house.act(A, "alarm.set", **args)
    assert house.al.rows == {}


def test_only_the_owner_may_touch_an_alarm(house):
    mine = rid(house.act(A, "alarm.set", time="07:00"))
    for action, extra in (("alarm.edit", {"label": "x"}), ("alarm.toggle", {"enabled": 0}), ("alarm.delete", {}),
                          ("alarm.dismiss", {})):
        with pytest.raises(Refused, match="no such alarm"):
            house.act(B, action, id=mine, **extra)


def test_snooze_and_dismiss_follow_the_ring_and_each_is_undone(house, clock):
    a = rid(house.act(A, "alarm.set", time="07:00", repeat="daily", snooze_minutes=5))
    with pytest.raises(Refused, match="not ringing"):
        house.act(A, "alarm.snooze", id=a)
    clock[0] = at(2030, 1, 7, 7, 1)
    assert house.state(a) == "ringing"
    assert house.act(A, "alarm.snooze", id=a).verified and house.state(a) == "snoozed"
    assert house.k.actions.undo(A, actor=A).verified and house.state(a) == "ringing"
    house.act(A, "alarm.snooze", id=a)
    clock[0] = at(2030, 1, 7, 7, 6)
    assert house.state(a) == "ringing"                                               # the snooze ran out
    assert house.act(A, "alarm.dismiss", id=a).verified and house.state(a) == "idle"
    assert house.al.visible_to(A)[0]["next_fire"] == at(2030, 1, 8, 7)
    # ...and the dismiss is undone too: the alarm is ringing again (a ringing alarm's NEXT fire is still tomorrow's).
    assert house.k.actions.undo(A, actor=A).verified and house.state(a) == "ringing"


def test_a_dismiss_undo_restores_the_row_itself(house, clock):
    """A dismiss straight from the first ring: the undo changes the stored row back, not only the computed state."""
    a = rid(house.act(A, "alarm.set", time="07:00", repeat="daily", snooze_minutes=5))
    clock[0] = at(2030, 1, 7, 7, 1)
    house.act(A, "alarm.dismiss", id=a)
    idle = dict(house.al.rows[a])
    house.k.actions.undo(A, actor=A)
    assert house.al.rows[a] != idle and house.state(a) == "ringing"


def test_the_snooze_budget_is_per_occurrence(house, clock):
    a = rid(house.act(A, "alarm.set", time="07:00", repeat="daily", snooze_minutes=1))
    clock[0] = at(2030, 1, 7, 7, 0)
    for i in range(MAX_SNOOZES):
        house.act(A, "alarm.snooze", id=a)
        clock[0] += 60
    with pytest.raises(Refused, match="as much as it can be"):
        house.act(A, "alarm.snooze", id=a)
    clock[0] = at(2030, 1, 8, 7, 0)                                                  # tomorrow's ring has its own budget
    assert house.act(A, "alarm.snooze", id=a).verified


def test_edit_toggle_delete_each_verified_undone_and_an_edit_stops_a_ring(house, clock):
    a = rid(house.act(A, "alarm.set", time="07:00"))
    clock[0] = at(2030, 1, 7, 7, 1)
    house.act(A, "alarm.edit", id=a, time="07:30")
    assert house.state(a) == "idle" and house.al.rows[a]["date"] == "2030-01-07"      # re-picked: 07:30 is still ahead today
    for action, extra in (("alarm.toggle", {"enabled": 0}), ("alarm.delete", {}), ("alarm.edit", {"label": "work"})):
        before = dict(house.al.rows[a])
        assert house.act(A, action, id=a, **extra).verified and house.al.rows.get(a) != before
        assert house.k.actions.undo(A, actor=A).verified and house.al.rows[a] == before


def test_a_spent_one_off_turned_back_on_rings_at_the_next_such_time(house, clock):
    a = rid(house.act(A, "alarm.set", time="07:00"))
    house.act(A, "alarm.toggle", id=a, enabled=0)
    clock[0] = at(2030, 1, 7, 9)
    assert house.act(A, "alarm.toggle", id=a, enabled=1).verified and house.al.rows[a]["date"] == "2030-01-08"


# ---- durability and forget ----------------------------------------------------------------------------------------------------
def test_a_restart_rebuilds_the_alarms_and_a_forget_takes_them_for_good(house, tmp_path, clock):
    a = rid(house.act(A, "alarm.set", time="07:00", label="gym"))
    rid(house.act(A, "alarm.set", time="08:00", label="work"))
    b = rid(house.act(B, "alarm.set", time="06:00", repeat="daily"))
    house.act(A, "alarm.toggle", id=a, enabled=0)
    rows = {k: dict(v) for k, v in house.al.rows.items()}
    house.k.close()
    again = House(tmp_path / "k.db", clock)
    assert again.al.rows == rows
    again.k.spine.forget(A, actor=A, entity="gym")
    again.k.pump()
    assert {r["label"] for r in again.al.rows.values()} == {"work", ""}
    again.k.spine.forget(A, actor=A)
    again.k.pump()
    assert set(again.al.rows) == {b}
    again.k.close()
    assert set(House(tmp_path / "k.db", clock).al.rows) == {b}


@pytest.mark.parametrize("day", ["2030-02-30", "2030-99-99", "2030-00-10"])
def test_a_day_that_is_not_a_day_is_refused_and_never_raises(house, clock, day):
    """The argument shape lets an impossible day through; the alarm's own rule refuses it with a fixed line, on set and on edit."""
    with pytest.raises(Refused, match="not a day I can use"):
        house.act(A, "alarm.set", time="07:00", date=day)
    a = rid(house.act(A, "alarm.set", time="07:00"))
    before = dict(house.al.rows[a])
    with pytest.raises(Refused, match="not a day I can use"):
        house.act(A, "alarm.edit", id=a, date=day)
    assert house.al.rows[a] == before


def test_a_label_or_snooze_edit_leaves_a_spent_one_off_spent(house, clock):
    """Only an edit that asks for a new ring re-picks a spent one-off's day: a label or snooze-length edit must not bring it back."""
    a = rid(house.act(A, "alarm.set", time="07:00"))
    day = house.al.rows[a]["date"]
    clock[0] = at(2030, 1, 9, 12)                                    # well past its one ring
    assert house.al.visible_to(A)[0]["next_fire"] is None
    for extra in ({"label": "work"}, {"snooze_minutes": 3}):
        assert house.act(A, "alarm.edit", id=a, **extra).verified
        assert house.al.rows[a]["date"] == day and house.al.visible_to(A)[0]["next_fire"] is None, extra
    assert house.al.rows[a]["label"] == "work" and house.al.rows[a]["snooze_minutes"] == 3


def test_a_time_edit_or_a_switch_back_on_reschedules_a_spent_one_off(house, clock):
    a = rid(house.act(A, "alarm.set", time="07:00"))
    clock[0] = at(2030, 1, 9, 12)
    assert house.act(A, "alarm.edit", id=a, enabled=1).verified
    assert house.al.visible_to(A)[0]["next_fire"] == at(2030, 1, 10, 7)
    clock[0] = at(2030, 1, 11, 12)
    assert house.act(A, "alarm.edit", id=a, time="08:00").verified
    assert house.al.visible_to(A)[0]["next_fire"] == at(2030, 1, 12, 8)


def test_the_row_rules_hold_for_alarms_undo_refused_once_changed_and_an_unrelated_forget_resurrects_nothing(house):
    from zoe_os.spine import Refused
    gone = rid(house.act(A, "alarm.set", time="07:00", repeat="daily", label="synthetic undone"))
    house.k.actions.undo(A, actor=A)                                   # the alarm is undone
    kept = rid(house.act(A, "alarm.set", time="08:00", repeat="daily", label="synthetic kept"))
    house.act(A, "alarm.edit", id=kept, label="synthetic kept, edited")
    house.act(A, "alarm.toggle", id=kept, enabled=0)
    house.k.actions.undo(A, actor=A)                                   # the toggle is undone: on again
    house.act(B, "alarm.set", time="09:00", label="synthetic other")   # (B's: A's last action stays the undo)
    other = house.k.spine.publish(Event(Kind.UTTERANCE, "voice", A, Visibility.PRIVATE, {"text": "synthetic unrelated words"})).seq
    house.k.spine.forget(A, actor=A, seqs=[other])
    house.k.pump()
    assert gone not in house.al.rows and house.al.rows[kept]["enabled"] == 1 and house.al.rows[kept]["label"] == "synthetic kept, edited"
    with pytest.raises(Refused):
        house.k.actions.undo(A, actor=A)                               # the last action is itself an undo
