"""The learned baseline: counts and ranges per person, silent until it has enough days."""
from __future__ import annotations

from twin_helpers import learn

from zoe_os.twin.baseline import MIN_DAYS, NOT_ENOUGH, OFF, OK, WINDOW_DAYS, describe, hhmm
from zoe_os.twin.days import GUEST_ID, SAMPLE, generate


def test_it_says_not_enough_days_yet_until_it_has_ten_working_days_and_six_weekend_days(tmp_path):
    k, twin, _ = learn(tmp_path, n=11)                       # 9 working days, 2 weekend days
    n = twin.normal("alex", "workday")
    assert (n.status, n.days, n.needed) == (NOT_ENOUGH, 9, 10) and n.times == {} and n.steps == {}
    assert describe(n) == ["alex (workday): not enough days yet (9 of 10)"]
    assert twin.normal("alex", "weekend").status == NOT_ENOUGH
    k.close()
    k, twin, _ = learn(tmp_path / "more", n=12)              # the tenth working day arrives
    assert twin.normal("alex", "workday").status == OK and twin.normal("alex", "weekend").days == 2
    assert MIN_DAYS == {"workday": 10, "weekend": 6}
    k.close()


def test_what_is_normal_is_counts_and_ranges_that_match_the_days_it_saw(tmp_path):
    k, twin, _ = learn(tmp_path)                             # 28 days: 20 working, 8 weekend
    alex = twin.normal("alex", "workday")
    assert (alex.status, alex.days) == (OK, 20)
    up = alex.times["up"]
    assert up.seen == 20 and up.earliest <= up.low <= up.median <= up.high <= up.latest
    assert hhmm(up.earliest) < "04:20" and 6 * 60 + 15 <= up.low and up.high <= 6 * 60 + 45    # one flight day
    assert alex.times["left"].seen == 20 and alex.times["arrived"].seen == 19                  # not back from it
    assert alex.steps["take-with-you/phone"] == (19, 20)     # the forgotten-phone day
    assert alex.steps["take-with-you/keys"] == (20, 20)
    assert set(alex.rooms[18]) == {"kitchen"} and alex.rooms[18]["kitchen"] + alex.rooms.get(19, {}).get("kitchen", 0) == 19
    kid = twin.normal("kid", "workday")
    assert kid.steps["morning/teeth"] == (19, 20) and kid.home_all_day == 1                     # missed; sick
    assert twin.normal("sam", "workday").home_all_day == 1   # stayed with the sick child
    weekend = twin.normal("alex", "weekend")
    assert weekend.status == OK and weekend.home_all_day == 8 and "left" not in weekend.times
    assert weekend.times["up"].median > up.median + 60
    k.close()


def test_the_description_is_counts_only_and_the_same_days_give_the_same_words(tmp_path):
    k, twin, _ = learn(tmp_path)
    lines = describe(twin.normal("kid", "workday"))
    assert lines[0] == "kid (workday), from 20 days:"
    assert "  morning/teeth: done on 19 of 20 days" in lines and "  at home all day on 1 of 20 days" in lines
    assert any(line.startswith("  up: usually 07:0") and "on 20 of 20 days" in line for line in lines)
    k2, twin2, _ = learn(tmp_path / "again")
    assert describe(twin2.normal("kid", "workday")) == lines
    kept = [v for days in twin.days.values() for facts in days.values() for v, _seq in facts.values()]
    assert all(isinstance(v, (int, bool)) or v in ("bedroom", "kitchen") for v in kept)        # never text
    k.close(), k2.close()


def test_a_person_with_tracking_off_has_no_baseline_and_a_guest_is_never_tracked(tmp_path):
    k, twin, _ = learn(tmp_path, off={"sam": ["twin"]})
    off = twin.normal("sam", "workday")
    assert off.status == OFF and describe(off) == ["sam (workday): no data: not enabled for this person"]
    assert "sam" not in twin.days and twin.normal("alex", "workday").status == OK
    assert any(e["subject"] == GUEST_ID for day in generate(SAMPLE, days=28, seed=7) for e in day.events)
    assert GUEST_ID not in twin.days                         # seen by the household, tracked by no one
    assert twin.normal(GUEST_ID).status == OFF
    k.close()


def test_it_keeps_a_bounded_window_of_reduced_days(tmp_path):
    k, twin, _ = learn(tmp_path, n=70)
    assert {person: len(days) for person, days in twin.days.items()} == dict.fromkeys(("alex", "sam", "kid"), WINDOW_DAYS)
    assert WINDOW_DAYS == 28 and min(twin.days["alex"]) == "2030-04-15"     # the first 42 days are gone
    k.close()


def test_normal_before_a_day_leaves_that_day_out(tmp_path):
    k, twin, _ = learn(tmp_path)
    assert twin.normal("alex", "workday", before="2030-03-25").days == 15
    assert twin.normal("alex", "workday", before="2030-03-11").status == NOT_ENOUGH
    k.close()
