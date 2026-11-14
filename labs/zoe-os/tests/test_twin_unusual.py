"""What is unusual today: quiet on an ordinary day, specific on an awkward one, never a reason."""
from __future__ import annotations

import pytest
from twin_helpers import learn

from zoe_os.twin.baseline import NOT_ENOUGH, OFF
from zoe_os.twin.days import AWKWARD, GUEST_ID, SAMPLE, generate
from zoe_os.twin.unusual import unusual

TODAY = "2030-03-25"                       # a Monday, after three ordinary weeks
NO_REASONS = ("sick", "ill", "unwell", "tired", "stress", "mood", "sad", "depress", "anxi", "health",
              "because", "probabl", "maybe", "might", "perhaps", "likely", "seems", "suggest",
              "relationship", "argu", "drunk", "lazy", "should", "worr", "concern", "normal for")


def scenario(tmp_path, kind: str, *, seed: int = 7, off=None):
    """Three ordinary weeks (15 working days, 6 weekend days), then one day of ``kind``."""
    days = generate(SAMPLE, days=22, seed=seed, awkward={i: "" for i in range(21)} | {21: kind})
    return learn(tmp_path, days, off=off)


def texts(result) -> list[str]:
    return [d.text for d in result.departures]


@pytest.mark.parametrize("seed", [1, 7, 23, 404, 9001])
def test_it_stays_silent_on_an_ordinary_day(tmp_path, seed):
    k, twin, _ = scenario(tmp_path, "", seed=seed)
    result = unusual(twin, TODAY)
    assert result.departures == [] and result.silent == {}
    k.close()


def test_it_stays_silent_across_a_whole_ordinary_month_once_it_has_a_baseline(tmp_path):
    days = generate(SAMPLE, days=42, seed=7, awkward={i: "" for i in range(42)})
    k, twin, _ = learn(tmp_path, days)
    said = [t for day in days[21:] for t in texts(unusual(twin, day.meta["day"]))]
    assert said == []                      # 21 days, 3 people, working days and weekends: nothing
    k.close()


def test_each_awkward_day_is_named_by_what_differs_and_only_that(tmp_path):
    expected = {
        "forgotten_phone": ["alex: take-with-you/phone was not done. It was done on 15 of the last 15 working days."],
        "sick_child": [
            "kid: no 'arrived' so far today. It happened on 15 of the last 15 working days.",
            "kid: no 'left' so far today. It happened on 15 of the last 15 working days.",
            "kid: no record of take-with-you/school bag today. It came up on 15 of the last 15 working days.",
            "sam: no 'arrived' so far today. It happened on 15 of the last 15 working days.",
            "sam: no 'left' so far today. It happened on 15 of the last 15 working days.",
            "sam: no record of take-with-you/gym bag today. It came up on 15 of the last 15 working days.",
            "sam: no record of take-with-you/phone today. It came up on 15 of the last 15 working days."],
        "missed_step": ["kid: morning/teeth was not done. It was done on 15 of the last 15 working days."],
        "guest": [],
    }
    for kind, want in expected.items():
        k, twin, _ = scenario(tmp_path / kind, kind)
        assert texts(unusual(twin, TODAY)) == want, kind
        k.close()


def test_a_late_night_and_a_flight_are_times_beyond_every_baseline_day(tmp_path):
    k, twin, _ = scenario(tmp_path / "late", "late_night")
    (late,) = unusual(twin, TODAY).departures
    assert late.person == "alex" and late.what == "bed" and (late.like_today, late.days) == (0, 15)
    assert late.text.startswith("alex: 'bed' at 0") and "Later than on any of the last 15 working days" in late.text
    k.close()
    k, twin, _ = scenario(tmp_path / "flight", "flight")
    said = texts(unusual(twin, TODAY))
    assert any(t.startswith("alex: 'up' at 04:") and "Earlier than on any of the last 15 working days" in t
               for t in said)
    assert "alex: no 'bed' so far today. It happened on 15 of the last 15 working days." in said
    assert all(t.startswith("alex: ") for t in said)
    k.close()


def test_it_never_gives_a_reason_or_a_diagnosis_on_any_kind_of_day(tmp_path):
    said = []
    for kind in AWKWARD:
        k, twin, _ = scenario(tmp_path / kind, kind)
        said += texts(unusual(twin, TODAY))
        k.close()
    assert len(said) >= 12
    for text in said:
        assert not [w for w in NO_REASONS if w in text.lower()], text
        assert "the last 15 working days" in text            # always: differs from the last N days


def test_what_has_not_had_time_to_happen_is_not_reported_missing(tmp_path):
    k, twin, _ = scenario(tmp_path, "sick_child")
    assert unusual(twin, TODAY, as_of=7 * 60).departures == []             # 07:00: nobody has left yet anyway
    morning = texts(unusual(twin, TODAY, as_of=10 * 60))
    assert morning == ["kid: no 'left' so far today. It happened on 15 of the last 15 working days.",
                       "sam: no 'left' so far today. It happened on 15 of the last 15 working days."]
    k.close()


def test_it_does_not_fire_on_a_person_who_has_tracking_switched_off(tmp_path):
    k, twin, _ = scenario(tmp_path, "sick_child", off={"kid": ["twin"]})
    result = unusual(twin, TODAY)
    assert result.departures and all(d.person == "sam" for d in result.departures)
    assert "kid" not in str(texts(result)) and "kid" not in result.silent and "kid" not in twin.days
    k.close()


def test_nothing_is_ever_said_about_a_guest_and_nothing_before_there_are_enough_days(tmp_path):
    k, twin, _ = scenario(tmp_path / "guest", "guest")
    assert GUEST_ID not in str(unusual(twin, TODAY).__dict__)
    k.close()
    k, twin, _ = learn(tmp_path / "few", generate(SAMPLE, days=8, seed=7, awkward={7: "sick_child"}))
    early = unusual(twin, "2030-03-11")
    assert early.departures == [] and early.silent == dict.fromkeys(("alex", "kid", "sam"), NOT_ENOUGH)
    assert OFF not in early.silent.values()
    k.close()


def test_something_that_almost_never_happens_is_reported_as_rare_with_its_count(tmp_path):
    k, twin, _ = learn(tmp_path, generate(SAMPLE, days=28, seed=7, awkward={i: "" for i in range(28)}))
    saturday = "2030-03-30"
    twin.days["alex"][saturday]["left"] = (10 * 60 + 2, 0)                  # ALEX went out on a Saturday
    (rare,) = unusual(twin, saturday).departures
    assert rare.text == "alex: 'left' at 10:02. That happened on 0 of the last 6 weekend days."
    k.close()
