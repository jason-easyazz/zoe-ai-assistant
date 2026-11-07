"""warmth_probes - the four deterministic probes for the QA "half-baked robot" findings, each with a negative control.

Fixtures are neutral, written for this file (no household data): the failing reply SHAPES are the ones the QA rounds recorded.
"""
from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.ci_safe  # pure functions + a data file

import warmth_probes as wp

LONELY = "honestly I don't know anyone here yet and my partner's always at work. it's kinda lonely"
INTRO = "a bit about me: I'm a pharmacist, I work early shifts, and my husband and our daughter just moved here with me"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("ZOE_EMOTIONAL_TURN_GUARD", "ZOE_LOCALE_FILE"):
        monkeypatch.delenv(k, raising=False)


# -- follow-up present ---------------------------------------------------------------------------------------

def test_follow_up_fails_on_a_bland_acknowledgement():
    assert wp.follow_up(INTRO, "Thanks for sharing that with me. It sounds like you've got a busy household.") == "fail"
    assert wp.follow_up(LONELY, "I'm here to listen if you want to talk more about that.") == "fail"


def test_follow_up_fails_on_only_a_generic_closer():
    assert wp.follow_up(LONELY, "Moving is hard. How are you holding up?") == "fail"


def test_follow_up_passes_on_a_specific_question():
    assert wp.follow_up(INTRO, "Early shifts at a pharmacy - what time do you start?") == "pass"
    assert wp.follow_up(LONELY, "A new town with nobody yet is hard. What did you used to do on weekends back home?") == "pass"


def test_follow_up_does_not_apply_to_tasks_or_commands():
    assert wp.follow_up("set a timer for 12 minutes", "Timer set for 12 minutes.") == "na"
    assert wp.follow_up("forget that I'm a pharmacist", "Done, I've removed that.") == "na"
    assert wp.follow_up("what do you remember about my family?", "Your husband and daughter.") == "na"


# -- no weather / offers on an emotional turn ----------------------------------------------------------------

def test_no_extras_fails_on_appended_weather_and_offer():
    assert wp.no_extras(LONELY, "I'm here for you. As for the weather, it's 25 degrees and clear.") == "fail"
    assert wp.no_extras(LONELY, "That's hard. Would you like me to add Alex as a contact?") == "fail"


def test_no_extras_passes_without_them_and_skips_non_emotional_turns():
    assert wp.no_extras(LONELY, "That's a lonely stretch. What do you miss most about home?") == "pass"
    assert wp.no_extras("what's the weather?", "Sunny, 25 degrees.") == "na"


# -- metric units for AU -------------------------------------------------------------------------------------

def test_locale_fails_on_fahrenheit_and_us_spelling_for_au():
    assert wp.locale("how do I bake it?", "Bake it at 375 degrees Fahrenheit for 25 minutes.", "AU") == "fail"
    assert wp.locale("dinner idea?", "Try roasted chicken-flavored tofu.", "AU") == "fail"


def test_locale_passes_metric_and_respects_the_users_own_words():
    assert wp.locale("how do I bake it?", "Bake it at 190 degrees Celsius for 25 minutes.", "AU") == "pass"
    assert wp.locale("375F in celsius?", "375 degrees Fahrenheit is about 190 Celsius.", "AU") == "na"


def test_locale_is_data_driven_not_one_country():
    assert wp.locale("how do I bake it?", "Bake it at 375 degrees Fahrenheit.", "US") == "pass"
    assert wp.locale("how do I bake it?", "Bake it at 190 degrees Celsius.", "US") == "fail"
    assert wp.locale("how do I bake it?", "Bake it at 190 degrees Celsius.", "") == "na"


# -- no parroting --------------------------------------------------------------------------------------------

def test_parrot_fails_when_the_reply_hands_the_words_back():
    msg = "so it's me, my partner Alex and our son Kai, he's 9. oh and a dog called Pepper"
    assert wp.no_parrot(msg, "That's wonderful. So you've got Alex, Kai, and Pepper with you now.") == "fail"


def test_parrot_fails_on_a_stock_line_anywhere():
    assert wp.no_parrot("I just moved in", "Welcome to your new place! Let me know if you need anything.") == "fail"
    assert wp.no_parrot(INTRO, "Thanks for sharing that with me.") == "fail"


def test_parrot_passes_on_a_reply_that_picks_one_detail():
    msg = "so it's me, my partner Alex and our son Kai, he's 9. oh and a dog called Pepper"
    assert wp.no_parrot(msg, "Nine is such a fun age. What kind of dog is Pepper?") == "pass"


def test_parrot_ignores_task_confirmations():
    assert wp.no_parrot("set a timer for 12 minutes", "Your timer is set for 12 minutes.") == "na"


# -- the CLI -------------------------------------------------------------------------------------------------

def test_cli_prints_counts_only_and_after_guard_fixes_extras(tmp_path, capsys):
    rows = [{"in": LONELY, "out": "I'm here for you.\n\nAs for the weather, it's 25 degrees and clear."},
            {"user": INTRO, "reply": "Early shifts - what time do you start?"}]
    f = tmp_path / "t.jsonl"
    f.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    assert wp.main([str(f), "--country", "AU"]) == 0
    before = capsys.readouterr().out
    assert "no_extras  pass=  0 fail=  1" in before
    assert "weather" not in before and "pharmacist" not in before   # no text is printed
    assert wp.main([str(f), "--country", "AU", "--after-guard"]) == 0
    assert "no_extras  pass=  1 fail=  0" in capsys.readouterr().out
