"""Present self-assessment of a time-critical condition ("am i having a heart attack?", "is this a stroke?") gets the call-now floor
behind ``ZOE_MEDICAL_SELF_ASSESS`` = off | shadow (DEFAULT) | enforce. off and shadow change no reply (shadow logs a fixed code);
enforce gives the module's existing reply for the condition's class. ``detect`` (and so memory filing) is unchanged in every mode.
Synthetic phrasings only; negative control per claim (ci_safe)."""
from __future__ import annotations

import asyncio
import logging

import pytest

import medical_safety

pytestmark = pytest.mark.ci_safe

FIRE = [
 "am i having a heart attack?", "is this a stroke?", "am i having a heart attack", "is this a heart attack", "is she having a stroke",
 "is he having a heart attack", "is my dad having a stroke", "is grandma having a heart attack", "could this be a heart attack?",
 "could it be a stroke", "is it a stroke if my face is drooping?", "i think im having a heart attack", "i think i'm having a stroke",
 "do you think im having a heart attack", "do you think hes having a stroke", "do you think this is a heart attack",
 "hey zoe am i having a heart attack", "zoe is this a stroke", "ok zoe i think my wife is having a stroke",
 "im having a heart attack", "i am having a heart attack", "she is having a stroke", "my husband is having a heart attack",
 "is this the start of a heart attack", "are these the signs of a heart attack", "is this the signs of a stroke",
 "i might be having a heart attack", "he could be having a stroke", "my mum might be having a stroke",
 "am i having a mini stroke", "is this a tia", "is she going into cardiac arrest", "is he in cardiac arrest" ,
 "is this anaphylaxis", "is she having an anaphylactic reaction", "is he going into anaphylactic shock", "is this heat stroke",
 "am i overdosing", "is this an overdose", "i think ive overdosed", "has he overdosed", "she has just overdosed",
 "did i just have a stroke", "have i had a heart attack today", "did i have a stroke its back", "i reckon its a heart attack",
 "im worried its a stroke", "how do i know if im having a heart attack", "is this a massive heart attack",
 "am i having a heartattack", "is my baby having a stroke",
]
NOT = [
 "what is a stroke", "what are the signs of a heart attack", "how do you treat a stroke", "what causes a heart attack",
 "tell me about strokes", "what is anaphylaxis", "what does a heart attack feel like", "how common are strokes",
 "my grandad had a stroke in 2019", "my dad had a heart attack last year", "she had a stroke when she was younger",
 "did i have a heart attack last year", "has my dad had a stroke before", "have i ever had a heart attack",
 "did my grandma have a stroke in march", "had i had a stroke before",
 "i nearly had a heart attack when i saw the bill", "you nearly gave me a heart attack", "that was a stroke of luck",
 "what a stroke of genius", "my backstroke is getting better", "his breaststroke is fast", "i need to work on my golf stroke",
 "is this a good stroke for the pool", "can you stroke the cat", "she gave the dog a stroke",
 "the character is having a heart attack in the movie", "is he having a heart attack in this film",
 "write a story about a heart attack", "how do you spell anaphylaxis", "what is an overdose", "how many people overdose each year",
 "my friend overdosed in 2015", "the patient in the show had a stroke", "what should i eat after a heart attack",
 "how long is recovery from a stroke", "my uncle survived a heart attack", "is heart attack the same as cardiac arrest",
 "what is heat stroke", "how do you prevent heat stroke", "im not having a heart attack",
]
FIRE += ["did grandma have a stroke in march",   # a bare relation word is not in the closed recall who-slot (2491): floor
         "could i be having a heart attack", "could my dad be having a stroke", "hey zoe could this be anaphylaxis",
         "is this a heart attack or anxiety", "what if im having a heart attack", "im having a heart attack lol", "is it heatstroke",
         "are you having a heart attack"]
NOT += ["am i having a stroke of luck", "is this a stroke in the pool", "is he having a heart attack in the movie",
        "is that a stroke on the water"]


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    import distress_handoff

    async def no(*_a, **_k):
        return False
    monkeypatch.setattr(distress_handoff, "_is_minor", no)
    monkeypatch.setattr(medical_safety, "_intercom_reachable", no)
    monkeypatch.delenv("ZOE_MEDICAL_SAFETY", raising=False)
    monkeypatch.delenv(medical_safety.SELF_ASSESS_ENV, raising=False)


def test_the_lists_are_big_enough():
    assert len(FIRE) >= 40 and len(NOT) >= 40


@pytest.mark.parametrize("text", FIRE)
def test_a_present_self_assessment_fires_in_enforce(text, monkeypatch):
    monkeypatch.setenv(medical_safety.SELF_ASSESS_ENV, "enforce")
    reply = run(medical_safety.handle(text, "demo_bar_00000001"))
    assert reply and ("Ring" in reply)


@pytest.mark.parametrize("text", NOT)
def test_knowledge_narrative_idiom_recall_and_fiction_never_fire(text, monkeypatch):
    monkeypatch.setenv(medical_safety.SELF_ASSESS_ENV, "enforce")
    assert medical_safety.self_assessment(text).cls == "" and medical_safety.detect(text).cls == ""
    assert run(medical_safety.handle(text, "demo_bar_00000001")) is None


@pytest.mark.parametrize("value", ["off", "shadow", "", "typo"])
@pytest.mark.parametrize("text", FIRE[:12] + NOT[:6])
def test_off_and_shadow_are_byte_identical_to_today(value, text, monkeypatch):
    if value:
        monkeypatch.setenv(medical_safety.SELF_ASSESS_ENV, value)
    got = run(medical_safety.handle(text, "demo_bar_00000001"))
    monkeypatch.setattr(medical_safety, "self_assessment", lambda _t: medical_safety.NONE)     # today: the condition is never read
    assert got == run(medical_safety.handle(text, "demo_bar_00000001"))


def test_the_default_and_an_unlisted_value_are_shadow():
    assert medical_safety.self_assess_mode() == "shadow"
    import os

    os.environ[medical_safety.SELF_ASSESS_ENV] = "ENFORCE-ish"
    try:
        assert medical_safety.self_assess_mode() == "shadow"
    finally:
        del os.environ[medical_safety.SELF_ASSESS_ENV]


def test_shadow_logs_a_fixed_code_and_never_the_words(monkeypatch, caplog):
    with caplog.at_level(logging.INFO, logger="medical_safety"):
        assert run(medical_safety.handle("am i having a heart attack", "demo_bar_00000001")) is None
    assert "MEDICAL_SELF_ASSESS" in caplog.text and "key=heart mode=shadow" in caplog.text and "attack" not in caplog.text


def test_off_logs_nothing(monkeypatch, caplog):
    monkeypatch.setenv(medical_safety.SELF_ASSESS_ENV, "off")
    with caplog.at_level(logging.INFO, logger="medical_safety"):
        run(medical_safety.handle("is this a stroke", "demo_bar_00000001"))
    assert "MEDICAL_SELF_ASSESS" not in caplog.text


@pytest.mark.parametrize("text, key", [("am i having a heart attack", "heart"), ("is she going into cardiac arrest", "heart"),
                                       ("is this a stroke", "stroke"), ("is this heat stroke", "stroke"), ("is this a tia", "stroke"),
                                       ("is this anaphylaxis", "breathing")])
def test_each_condition_gets_its_classs_existing_reply(text, key, monkeypatch):
    monkeypatch.setenv(medical_safety.SELF_ASSESS_ENV, "enforce")
    assert medical_safety.self_assessment(text) == medical_safety.Verdict("red_call", key)
    assert run(medical_safety.handle(text, "demo_bar_00000001")) == medical_safety.build_reply(medical_safety.Verdict("red_call", key))


def test_an_overdose_gets_the_human_poisoning_reply(monkeypatch):
    monkeypatch.setenv(medical_safety.SELF_ASSESS_ENV, "enforce")
    assert medical_safety.self_assessment("am i overdosing") == medical_safety.Verdict("ingest_human", "poison_human")


@pytest.mark.parametrize("text", ["am i having a heart attack", "is this a stroke", "is this anaphylaxis", "am i overdosing"])
def test_control_without_the_self_assessment_these_reach_the_brain(text, monkeypatch):
    monkeypatch.setenv(medical_safety.SELF_ASSESS_ENV, "enforce")
    monkeypatch.setattr(medical_safety, "self_assessment", lambda _t: medical_safety.NONE)
    assert run(medical_safety.handle(text, "demo_bar_00000001")) is None              # the gap this closes


@pytest.mark.parametrize("value", ["off", "shadow", "enforce"])
def test_detect_and_so_memory_filing_are_unchanged_in_every_mode(value, monkeypatch):
    monkeypatch.setenv(medical_safety.SELF_ASSESS_ENV, value)
    for text in ("am i having a heart attack", "is this a stroke", "i think im having a stroke"):
        assert medical_safety.detect(text).cls == ""
        assert medical_safety.emergency_event("User is having a heart attack", text) is None


def test_the_flag_is_registered_with_its_modes():
    import flag_registry

    s = flag_registry.spec(medical_safety.SELF_ASSESS_ENV)
    assert s.default == "shadow" and tuple(s.allowed) == ("off", "shadow", "enforce")
