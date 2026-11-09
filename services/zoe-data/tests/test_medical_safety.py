"""The medical safety floor (``medical_safety``): QA round 6b. Synthetic names only. Each guard has a negative control. ``ci_safe``."""
from __future__ import annotations

import asyncio
import re

import pytest

import medical_safety as ms

pytestmark = pytest.mark.ci_safe


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for k in (ms.ENV, "ZOE_HOUSEHOLD_COUNTRY", "ZOE_LOCATION_COUNTRY", "ZOE_TIMEZONE", "ZOE_DISTRESS_LINES_FILE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ZOE_HOUSEHOLD_COUNTRY", "AU")


def reply(text):
    return run(ms.handle(text, "demo-user"))


# 1. a poisoning is a human emergency unless the words clearly say animal ------------------------------------------------
def test_toddler_ingestion_gets_poisons_line_and_000_first():
    out = reply("wren just ate a handful of dishwasher tablet powder what do i do")
    assert out.startswith("Ring the Poisons Information line on 13 11 26")
    assert "000" in out and not out.lower().startswith("ring your vet")


def test_ingestion_is_human_even_when_memory_would_say_pet():
    # the name is unknown to the floor: nothing about memory is consulted, so a wrongly typed pet cannot redirect it
    assert ms.detect("biscuit-the-child swallowed a button battery").cls == "ingest_human"
    assert ms.detect("my daughter ate bleach and the dog is barking").cls == "ingest_human"


def test_negative_control_clear_pet_ingestion_stays_a_vet_answer():
    out = reply("my dog ate a handful of dishwasher tablet powder")
    assert out.startswith("Ring your vet") and "13 11 26" not in out


def test_unclear_subject_names_the_vet_only_as_the_if_clause():
    out = reply("rex ate some paracetamol tablets")
    assert out.startswith("Ring the Poisons Information line") and "vet" in out.split("If this is actually an animal")[1]


def test_everyday_tablet_talk_is_not_an_ingestion():
    assert ms.detect("my son swallowed his morning tablets fine").cls == ""
    assert ms.detect("i ate a big dinner and took a nap").cls == ""


# 2. an emergency question pre-empts the thread --------------------------------------------------------------------------
def test_collapse_number_question_mid_thread_gets_the_number_and_a_way_back():
    out = reply("how long should it rest, and hold on, what number do i ring if margaret collapses")
    assert out.startswith("Ring 000.") and "pick it straight back up" in out


def test_poison_number_question_gives_both_lines():
    out = reply("go on, 3 quick steps to carve it. wait, actually hold on, whats the number for poison info if wren eats something")
    assert "000" in out and "13 11 26" in out


def test_bare_number_question_has_no_thread_line():
    out = reply("what number do i ring if jonas collapses")
    assert out.startswith("Ring 000.") and "straight back up" not in out


@pytest.mark.parametrize("text", ["how long should it rest", "what number do i ring the plumber", "whats the number for the vet",
                                  "what do i do with the leftover chicken", "set a timer for ten minutes"])
def test_negative_control_non_emergency_questions_stay_in_the_thread(text):
    assert reply(text) is None


# 3. the locale's number, from config ------------------------------------------------------------------------------------
def test_chest_tightness_gives_the_number_plainly_first():
    out = reply("my chest feels tight and my left arm is tingly")
    assert out.startswith("Chest tightness") and "Ring 000 now" in out


def test_number_comes_from_the_household_country_not_a_constant(monkeypatch):
    monkeypatch.setenv("ZOE_HOUSEHOLD_COUNTRY", "US")
    out = reply("my chest feels tight")
    assert "911" in out and "000" not in out
    monkeypatch.delenv("ZOE_HOUSEHOLD_COUNTRY")
    out = reply("my chest feels tight")
    assert "your local emergency number" in out and not re.search(r"\b(000|911|999)\b", out)


def test_a_child_is_told_to_get_a_grown_up_first(monkeypatch):
    async def minor(_uid):
        return True

    import distress_handoff
    monkeypatch.setattr(distress_handoff, "_is_minor", minor)
    assert run(ms.handle("my chest hurts, it feels so tight", "kid")).startswith("Tell a grown-up")


def test_negative_control_chest_infection_is_not_chest_tightness():
    assert reply("i have a chest infection and a cold") is None


# 4. red flags that need a doctor today ----------------------------------------------------------------------------------
def test_headache_with_blurred_vision_gets_an_urgency_cue():
    out = reply("ive had a headache for 3 days and now my vision is blurry")
    assert "see a doctor today" in out and "000" in out and "?" not in out


def test_negative_control_plain_headache_goes_to_the_brain():
    assert reply("i have a headache, any tips for sleeping") is None


# 5. a glucose reading gets a safe, brief answer -------------------------------------------------------------------------
def test_glucose_9_8_gets_meaning_and_when_to_act():
    out = reply("my sugar was 9.8 this morning before breakfast. is that bad")
    assert "9.8 mmol/L" in out and "not an emergency on its own" in out and "GP" in out
    assert "can't give medical advice" not in out


@pytest.mark.parametrize("text,needle", [("my blood sugar was 3.2", "is low"), ("my blood sugar is 5.5", "usual range"),
                                         ("my blood glucose was 18", "is high"), ("my blood sugar is 160", "mg/dL")])
def test_glucose_bands(text, needle):
    assert needle in reply(text)


@pytest.mark.parametrize("text", ["i put 2 sugars in my tea", "this cake has 300 grams of sugar", "sugar is on the list"])
def test_negative_control_sugar_talk_is_not_a_reading(text):
    assert reply(text) is None


# the flag ---------------------------------------------------------------------------------------------------------------
def test_flag_off_and_shadow_leave_the_brain_in_charge(monkeypatch):
    for val in ("off", "shadow"):
        monkeypatch.setenv(ms.ENV, val)
        assert reply("my chest feels tight") is None
    monkeypatch.setenv(ms.ENV, "typo")
    assert reply("my chest feels tight") is not None   # a typo enforces: it is a floor


def test_reply_texts_never_diagnose_or_deflect():
    for text in ("my chest feels tight", "my sugar was 9.8", "wren swallowed bleach"):
        assert "i'm not a medical professional" not in reply(text).lower()


def test_seam_runs_it_right_after_the_distress_hand_off():
    import inspect

    import zoe_flue_client
    src = inspect.getsource(zoe_flue_client.run_flue_brain_streaming)
    assert src.index("distress_handoff.handle") < src.index("medical_safety.handle") < src.index("_resolve_clarification(message")
