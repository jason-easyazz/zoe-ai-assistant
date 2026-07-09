"""Tests for the first-turn-of-day voice greeting (voice_greeting.py)."""
import datetime
import importlib
import os

import pytest


@pytest.fixture
def vg(tmp_path, monkeypatch):
    monkeypatch.setenv("ZOE_VOICE_GREETING_ENABLED", "1")
    monkeypatch.setenv("ZOE_VOICE_GREETING_STATE_PATH", str(tmp_path / "greet.json"))
    import voice_greeting
    importlib.reload(voice_greeting)
    return voice_greeting


def _at(hour):
    return datetime.datetime(2026, 7, 10, hour, 30, 0)


def test_time_of_day_phrasing(vg):
    assert vg.greeting_for_hour(7) == "Good morning"
    assert vg.greeting_for_hour(13) == "Good afternoon"
    assert vg.greeting_for_hour(20) == "Good evening"
    assert vg.greeting_for_hour(2) == "Good evening"  # pre-dawn → evening bucket


def test_first_turn_greets_then_silent_same_day(vg):
    assert vg.greeting_prefix("jason", now=_at(7)) == "Good morning"
    # subsequent turns same local day → no greeting
    assert vg.greeting_prefix("jason", now=_at(9)) == ""
    assert vg.greeting_prefix("jason", now=_at(15)) == ""


def test_greets_again_next_day(vg):
    assert vg.greeting_prefix("jason", now=datetime.datetime(2026, 7, 10, 7, 0)) == "Good morning"
    assert vg.greeting_prefix("jason", now=datetime.datetime(2026, 7, 11, 7, 0)) == "Good morning"


def test_per_user_independent(vg):
    assert vg.greeting_prefix("jason", now=_at(7)) == "Good morning"
    assert vg.greeting_prefix("sam", now=_at(7)) == "Good morning"  # different user still greeted
    assert vg.greeting_prefix("jason", now=_at(8)) == ""


def test_disabled_flag_no_greeting(vg, monkeypatch):
    monkeypatch.setenv("ZOE_VOICE_GREETING_ENABLED", "0")
    assert vg.greeting_prefix("jason", now=_at(7)) == ""


def test_apply_greeting_prepends_as_own_sentence(vg):
    out = vg.apply_greeting("It's 14 degrees and clear in Geraldton", "jason", now=_at(7))
    assert out == "Good morning. It's 14 degrees and clear in Geraldton"
    # second turn same day: answer only, no greeting
    out2 = vg.apply_greeting("The time is 9:15 AM", "jason", now=_at(9))
    assert out2 == "The time is 9:15 AM"


def test_apply_greeting_empty_reply_untouched(vg):
    assert vg.apply_greeting("", "jason", now=_at(7)) == ""
    # an empty reply must not consume the day's greeting slot
    assert vg.greeting_prefix("jason", now=_at(8)) == "Good morning"


def test_missing_user_no_greeting(vg):
    assert vg.greeting_prefix("", now=_at(7)) == ""


def test_state_survives_reload(vg, monkeypatch):
    assert vg.greeting_prefix("jason", now=_at(7)) == "Good morning"
    import voice_greeting
    importlib.reload(voice_greeting)  # simulate process restart; state file persists
    assert voice_greeting.greeting_prefix("jason", now=_at(9)) == ""
