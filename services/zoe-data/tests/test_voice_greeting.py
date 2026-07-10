"""Tests for the first-turn-of-day voice greeting (voice_greeting.py)."""
import datetime
import importlib

import pytest

pytestmark = pytest.mark.ci_safe  # GitHub-CI opt-in: runs in validate.yml's `-m ci_safe` lane


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


def test_in_memory_guard_blocks_regreet_when_disk_unwritable(vg, monkeypatch):
    # Simulate an unwritable store: the persist silently fails, but the in-memory
    # mirror must still prevent a re-greet on the next turn (not greet every turn).
    monkeypatch.setattr(vg, "_save_state", lambda state: None)  # write is a no-op
    monkeypatch.setattr(vg, "_load_state", lambda: {})          # disk always empty
    assert vg.greeting_prefix("jason", now=_at(7)) == "Good morning"
    assert vg.greeting_prefix("jason", now=_at(8)) == ""  # in-memory guard holds


def test_missing_user_no_greeting(vg):
    assert vg.greeting_prefix("", now=_at(7)) == ""


def test_state_survives_reload(vg, monkeypatch):
    assert vg.greeting_prefix("jason", now=_at(7)) == "Good morning"
    import voice_greeting
    importlib.reload(voice_greeting)  # simulate process restart; state file persists
    assert voice_greeting.greeting_prefix("jason", now=_at(9)) == ""
