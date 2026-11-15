"""Presence fusion (``presence_fusion.py``, flag ``ZOE_PRESENCE_FUSION``): pure, in memory, explainable, private by default.

Negative controls live next to the rule they guard (``*_negative_control``): each one mutates the module in-process
(monkeypatch, restored after the test) and shows the same assertion would FAIL without the rule.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import presence_fusion as pf  # noqa: E402
from presence_fusion import Observation as O  # noqa: E402

pytestmark = pytest.mark.ci_safe

H = 3600.0
DAY0 = 1_700_000_000.0 - (1_700_000_000.0 % 86400)  # a UTC midnight; tz_offset 0 = local midnight


def _house(**kw) -> pf.PresenceFusion:
    f = pf.PresenceFusion(start=DAY0, **kw)
    f.register("adult_a")
    f.register("adult_b")
    f.register("child_c", role="child", guardians=("adult_a", "adult_b"))
    return f


def _voice(f, t, who, room="kitchen"):
    return f.observe(O(t, "voice_verified", who, room=room))


def _phone(f, t0, t1, who, value, every=60.0):
    out = []
    t = t0
    while t <= t1:
        out += f.observe(O(t, "phone_tracker", who, value=value))
        out += f.step(t)
        t += every
    return out


# -- unknown by default, decay to unknown, never to away -------------------------------------------------------------------

def test_a_person_with_no_evidence_is_unknown():
    f = _house()
    assert f.belief("adult_a", DAY0 + 10 * H).state == pf.UNKNOWN


def _stale_voice_state(f):
    _voice(f, DAY0 + 8 * H, "adult_a")
    assert f.belief("adult_a", DAY0 + 8 * H + 60).state == pf.HOME
    return f.belief("adult_a", DAY0 + 32 * H).state


def test_stale_evidence_decays_to_unknown_never_away():
    assert _stale_voice_state(_house()) == pf.UNKNOWN


def test_decay_negative_control(monkeypatch):
    monkeypatch.setattr(pf, "_decayed", lambda bits, age, hold, half: bits)
    assert _stale_voice_state(_house()) == pf.HOME          # without decay a person stays "home" forever


def test_only_a_positive_away_reading_makes_away():
    f = _house()
    _phone(f, DAY0 + 7 * H, DAY0 + 7 * H + 600, "adult_a", "home")
    _phone(f, DAY0 + 8 * H, DAY0 + 8 * H + 600, "adult_a", "away")
    assert f.belief("adult_a", DAY0 + 8 * H + 600).state == pf.AWAY
    assert f.belief("adult_a", DAY0 + 40 * H).state == pf.UNKNOWN   # the tracker stopped reporting: unknown, not away


# -- explainable ---------------------------------------------------------------------------------------------------------

def test_why_do_you_think_i_am_home_lists_the_observations():
    f = _house()
    t = DAY0 + 9 * H
    _voice(f, t, "adult_a")
    f.observe(O(t + 30, "phone_tracker", "adult_a", value="home"))
    why = f.explain("adult_a", t + 60)
    assert [w["signal"] for w in why] == ["voice_verified", "phone_tracker"]
    assert all(w["supports"] == pf.HOME and w["age_s"] >= 0 for w in why)


def test_a_falsely_accepted_tv_voice_cannot_outweigh_a_phone_that_left():
    f = _house()
    _phone(f, DAY0 + 7 * H, DAY0 + 7 * H + 300, "adult_a", "home")
    _phone(f, DAY0 + 8 * H, DAY0 + 9 * H, "adult_a", "away")
    _voice(f, DAY0 + 9 * H + 10, "adult_a", room="living")
    assert f.belief("adult_a", DAY0 + 9 * H + 20).state != pf.HOME


# -- events with hysteresis ------------------------------------------------------------------------------------------------

def test_left_needs_the_dwell_and_carries_confidence_and_evidence():
    f = _house()
    _phone(f, DAY0 + 7 * H, DAY0 + 7 * H + 300, "adult_a", "home")
    _voice(f, DAY0 + 7.5 * H, "adult_a")
    short = _phone(f, DAY0 + 8 * H, DAY0 + 8 * H + 300, "adult_a", "away")      # a 5-minute Wi-Fi drop
    assert not [e for e in short if e.kind == "left"]
    _phone(f, DAY0 + 8 * H + 360, DAY0 + 8 * H + 900, "adult_a", "home")
    _voice(f, DAY0 + 8.8 * H, "adult_a")                                             # active just before leaving
    evs = _phone(f, DAY0 + 9 * H, DAY0 + 9 * H + 1200, "adult_a", "away")
    left = [e for e in evs if e.kind == "left"]
    assert len(left) == 1 and left[0].confidence >= 0.88 and "phone_tracker" in left[0].evidence


def test_a_door_event_shortens_the_left_dwell():
    f = _house()
    _phone(f, DAY0 + 7 * H, DAY0 + 7 * H + 300, "adult_a", "home")
    _voice(f, DAY0 + 7.9 * H, "adult_a")
    f.observe(O(DAY0 + 8 * H - 120, "door_contact"))
    evs = _phone(f, DAY0 + 8 * H, DAY0 + 8 * H + 600, "adult_a", "away")
    left = [e for e in evs if e.kind == "left"]
    assert left and "door_contact" in left[0].evidence and left[0].t - left[0].since <= 120
    assert left[0].t <= DAY0 + 8 * H + 480                    # without the door the dwell alone is 600 s after the change


def test_a_phone_drop_in_a_sleeping_house_is_not_a_left():
    f = _house()
    _phone(f, DAY0 + 1 * H, DAY0 + 1 * H + 300, "adult_a", "home")
    evs = _phone(f, DAY0 + 3 * H, DAY0 + 4 * H, "adult_a", "away")                  # 03:00, nobody active for hours
    evs += _phone(f, DAY0 + 4 * H + 60, DAY0 + 4 * H + 900, "adult_a", "home")
    assert not [e for e in evs if e.kind in ("left", "arrived")]


def test_a_person_quiet_since_last_night_does_not_leave_when_someone_else_is_up():
    f = _house()
    _phone(f, DAY0 + 1 * H, DAY0 + 1 * H + 300, "adult_b", "home")
    _voice(f, DAY0 + 6.5 * H, "adult_a")                                            # the house is up, adult_b is not
    evs = _phone(f, DAY0 + 6.6 * H, DAY0 + 6.6 * H + 1800, "adult_b", "away")      # adult_b's phone drops off the Wi-Fi
    assert not [e for e in evs if e.kind == "left"]
    g = _house(left_own_activity_s=0.0)                                             # negative control: rule off
    _phone(g, DAY0 + 1 * H, DAY0 + 1 * H + 300, "adult_b", "home")
    _voice(g, DAY0 + 6.5 * H, "adult_a")
    evs = _phone(g, DAY0 + 6.6 * H, DAY0 + 6.6 * H + 1800, "adult_b", "away")
    assert [e.kind for e in evs if e.kind == "left"] == ["left"]


def test_arrived_after_away():
    f = _house()
    _phone(f, DAY0 + 7 * H, DAY0 + 7 * H + 300, "adult_a", "home")
    _voice(f, DAY0 + 7.9 * H, "adult_a")
    _phone(f, DAY0 + 8 * H, DAY0 + 8 * H + 1200, "adult_a", "away")
    evs = _phone(f, DAY0 + 17 * H, DAY0 + 17 * H + 600, "adult_a", "home")
    assert [e.kind for e in evs if e.kind in ("left", "arrived")] == ["arrived"]


def test_woke_is_the_first_activity_after_a_quiet_night_in_the_morning_band():
    f = _house()
    _voice(f, DAY0 - 2 * H, "adult_a")                                              # 22:00 the night before
    assert not _voice(f, DAY0 + 3 * H, "adult_a")                                   # 03:00 is outside the band
    f2 = _house()
    _voice(f2, DAY0 - 2 * H, "adult_a")
    woke = f2.observe(O(DAY0 + 6.5 * H, "alarm_dismissed", "adult_a", room="kitchen"))
    assert [e.kind for e in woke] == ["woke"]
    assert not _voice(f2, DAY0 + 6.6 * H, "adult_a")                                # once a day


def test_room_change_event():
    f = _house()
    _voice(f, DAY0 + 18 * H, "adult_a", room="kitchen")
    f.step(DAY0 + 18 * H + 10)
    _voice(f, DAY0 + 18 * H + 300, "adult_a", room="living")
    evs = f.step(DAY0 + 18 * H + 310)
    assert [(e.kind, e.room) for e in evs] == [("room_change", "living")]


# -- people: tracking off, child, guest ------------------------------------------------------------------------------------

def test_tracking_off_means_always_unknown_and_erases():
    f = _house()
    _voice(f, DAY0 + 9 * H, "adult_b")
    f.set_tracking("adult_b", False, actor="adult_b")
    assert f.belief("adult_b", DAY0 + 9 * H + 1).state == pf.UNKNOWN
    for k in ("voice_verified", "pin_unlock", "phone_tracker"):
        f.observe(O(DAY0 + 9 * H + 5, k, "adult_b", room="kitchen"))
    assert f.belief("adult_b", DAY0 + 9 * H + 10).state == pf.UNKNOWN
    assert f.room_of("adult_b", DAY0 + 9 * H + 10)[0] is None
    assert f.view("adult_b", "adult_b", DAY0 + 9 * H + 10)["state"] == pf.UNKNOWN
    assert not f.step(DAY0 + 9 * H + 20) and not f.events_for("adult_b", "adult_b")
    with pytest.raises(PermissionError):
        f.set_tracking("adult_b", True, actor="adult_a")                            # nobody else turns it back on


def test_a_childs_settings_are_the_guardians_and_never_below_balanced():
    f = _house()
    with pytest.raises(PermissionError):
        f.set_tracking("child_c", False, actor="child_c")
    with pytest.raises(ValueError):
        f.set_strictness("child_c", "relaxed", actor="adult_a")
    f.set_strictness("child_c", "strict", actor="adult_b")


def test_a_guest_is_never_a_tracked_person():
    f = _house()
    with pytest.raises(ValueError):
        f.register("guest_g", role="guest")
    f.observe(O(DAY0 + 19 * H, "voice_verified", "guest_g", room="living"))
    assert not f.holds("guest_g")
    f.observe(O(DAY0 + 19 * H, "someone_voice", None, room="living"))
    assert f.others_present("living", "adult_a", DAY0 + 19 * H + 60) == "yes"


def test_occupancy_that_cannot_count_never_says_someone_else_is_here():
    f = _house()
    t = DAY0 + 9 * H
    _voice(f, t, "adult_a")
    f.observe(O(t, "mmwave", None, room="kitchen"))
    f.observe(O(t, "room_toggle", None, room="kitchen"))
    f.observe(O(t, "camera_person", None, room="kitchen", count=1))
    assert f.others_present("kitchen", "adult_a", t + 10) == "none_observed"
    f.observe(O(t + 20, "camera_person", None, room="kitchen", count=2))
    assert f.others_present("kitchen", "adult_a", t + 30) == "yes"
    assert f.others_present("kitchen", "adult_a", t + 200) == "none_observed"       # a camera count is live, not sticky
    g = pf.PresenceFusion(start=t)
    assert g.others_present("kitchen", "adult_a", t + 10) == "unknown"              # just started: unknown is crowded


# -- privacy ---------------------------------------------------------------------------------------------------------------

def _where_is_a(f):
    _voice(f, DAY0 + 9 * H, "adult_a", room="bedroom")
    return f.view("adult_b", "adult_a", DAY0 + 9 * H + 30)


def test_one_member_never_sees_another_without_a_rule():
    f = _house()
    assert _where_is_a(f) is None
    assert f.view("adult_a", "adult_a", DAY0 + 9 * H + 30)["room"] == "bedroom"
    assert f.events_for("adult_b", "adult_a") == []


def test_visibility_clamp_negative_control(monkeypatch):
    monkeypatch.setattr(pf, "_visible", lambda *a, **k: True)
    assert _where_is_a(_house()) is not None                                        # without the clamp it is exposed


def test_a_declared_rule_shares_home_state_only():
    f = _house()
    _voice(f, DAY0 + 9 * H, "adult_a", room="bedroom")
    rules = [pf.HouseholdRule("share_home", "adult_a")]
    assert f.view("adult_b", "adult_a", DAY0 + 9 * H + 30, rules) == {"state": pf.HOME}
    _voice(f, DAY0 + 9 * H, "child_c", room="bedroom")
    g = [pf.HouseholdRule("guardian_home", "child_c")]
    assert f.view("adult_a", "child_c", DAY0 + 9 * H + 30, g) == {"state": pf.HOME}
    assert f.view("adult_a", "adult_b", DAY0 + 9 * H + 30, g) is None               # the rule names its subject


def test_strictness_decides_a_privacy_use():
    f = _house()
    f.observe(O(DAY0 + 9 * H, "room_toggle", None, room="kitchen"))
    f.observe(O(DAY0 + 9 * H, "pin_unlock", "adult_a", room="kitchen"))
    t = DAY0 + 9 * H + 30
    assert f.sure_enough("adult_a", "kitchen", t)                                   # 7 bits = 0.99
    f.set_strictness("adult_a", "strict", actor="adult_a")
    assert f.sure_enough("adult_a", "kitchen", t)
    assert not f.sure_enough("adult_a", "kitchen", t + 600)                          # 3.5 bits = 0.92 < strict
    f.set_strictness("adult_a", "balanced", actor="adult_a")
    assert f.sure_enough("adult_a", "kitchen", t + 600)
    assert not f.sure_enough("adult_a", "living", t)


# -- forget and the flag ---------------------------------------------------------------------------------------------------

def test_forget_erases_every_trace(monkeypatch):
    store = _house()
    monkeypatch.setattr(pf, "_STORE", store)
    _voice(store, DAY0 + 9 * H, "adult_a")
    store.step(DAY0 + 9 * H + 1)
    assert store.holds("adult_a")
    assert pf.forget_user("adult_a") > 0
    assert not store.holds("adult_a") and store.belief("adult_a", DAY0 + 9 * H + 2).state == pf.UNKNOWN
    assert pf.forget_user("") == 0


def test_flag_off_records_nothing(monkeypatch):
    store = _house()
    monkeypatch.setattr(pf, "_STORE", store)
    monkeypatch.delenv("ZOE_PRESENCE_FUSION", raising=False)
    pf.record(O(DAY0 + 9 * H, "voice_verified", "adult_a", room="kitchen"))
    assert store.belief("adult_a", DAY0 + 9 * H + 1).state == pf.UNKNOWN
    monkeypatch.setenv("ZOE_PRESENCE_FUSION", "shadow")
    pf.record(O(DAY0 + 9 * H, "voice_verified", "adult_a", room="kitchen"))
    assert store.belief("adult_a", DAY0 + 9 * H + 1).state == pf.HOME


def test_every_signal_is_declared_consistently():
    for s in pf.SIGNALS.values():
        assert s.away_bits <= 0 and s.home_bits >= 0
        assert s.identifies in ("person", "someone")
        if s.identifies == "someone":
            assert s.home_bits == 0 and s.away_bits == 0       # "someone" never moves a person's home/away
        assert s.group in pf.GROUP_CAPS or s.home_bits == s.away_bits == 0
