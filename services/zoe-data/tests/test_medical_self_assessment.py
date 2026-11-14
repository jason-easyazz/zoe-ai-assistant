"""Present self-assessment of a time-critical condition ("am i having a heart attack?", "is this a stroke?") gets the call-now floor
behind ``ZOE_MEDICAL_SELF_ASSESS`` = off | shadow (DEFAULT) | enforce. off and shadow change no reply (shadow logs a fixed code);
enforce gives the module's existing reply for the condition's class. ``detect`` (and so memory filing) is unchanged in every mode.
Synthetic phrasings only; negative control per claim (ci_safe)."""
from __future__ import annotations

import asyncio
import logging
import re

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


def test_every_condition_in_the_data_maps_to_an_existing_call_now_reply():
    conditions = medical_safety._data()["self_assess"]["conditions"]
    replies = medical_safety._data()["replies"]
    assert set(conditions) == {"heart", "stroke", "breathing"}
    for key, terms in conditions.items():
        assert key in replies and terms
        for term in terms:                                                    # each term and variant reaches its own reply
            assert medical_safety.self_assessment(f"is this {term}") == medical_safety.Verdict("red_call", key), term
    assert medical_safety.build_reply(medical_safety.Verdict("red_call", "heart")).startswith("Ring")


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


@pytest.mark.parametrize("value", ["off", "shadow", ""])
@pytest.mark.parametrize("text", FIRE[:12] + NOT[:6])
def test_off_and_shadow_are_byte_identical_to_today(value, text, monkeypatch):
    if value:
        monkeypatch.setenv(medical_safety.SELF_ASSESS_ENV, value)
    got = run(medical_safety.handle(text, "demo_bar_00000001"))
    monkeypatch.setattr(medical_safety, "self_assessment", lambda _t: medical_safety.NONE)     # today: the condition is never read
    assert got == run(medical_safety.handle(text, "demo_bar_00000001"))


@pytest.mark.parametrize("value, mode", [(None, "shadow"), ("", "shadow"), ("off", "off"), ("OFF", "off"), ("shadow", "shadow"),
                                         ("enforce", "enforce"), ("enforced", "enforce"), ("on", "enforce"), ("1", "enforce"),
                                         ("shadwo", "enforce"), ("garbage", "enforce")])
def test_only_unset_off_or_shadow_stay_below_enforce_and_a_typo_enforces(value, mode, monkeypatch):
    if value is None:
        monkeypatch.delenv(medical_safety.SELF_ASSESS_ENV, raising=False)
    else:
        monkeypatch.setenv(medical_safety.SELF_ASSESS_ENV, value)
    assert medical_safety.self_assess_mode() == mode
    reply = run(medical_safety.handle("am i having a heart attack", "demo_bar_00000001"))
    assert bool(reply) is (mode == "enforce")


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



# ── generated from the SAME data the detector compiles: every condition term x every frame x speaker forms ────────────────────
WHO_SAMPLES = ["my dad", "my wife", "grandma"]           # "{who}": a person present, named or by relation (placeholder words)


def _expand(frame: str, term: str, choice: dict) -> str:
    slots = medical_safety._data()["self_assess"]["slots"]
    out = frame.replace("{C}", f"a {term}" if term[0] not in "aeiou" else f"an {term}")
    for name in re.findall(r"\{([A-Z]+)\}", out):
        out = out.replace("{" + name + "}", slots[name][choice.get(name, 0)], 1)
    return out.replace("{who}", choice.get("who", WHO_SAMPLES[0]))


def _generated_fire() -> list:
    d = medical_safety._data()["self_assess"]
    terms = [t for ts in d["conditions"].values() for t in ts]
    out = set()
    for frame in d["frames"]:
        for term in terms:                                                    # every condition term and variant
            out.add(_expand(frame, term, {}))
        for name in [n for n in re.findall(r"\{([A-Z]+)\}", frame) if n != "C"]:   # every speaker / slot form
            for i in range(len(d["slots"][name])):
                for who in WHO_SAMPLES:
                    out.add(_expand(frame, "heart attack", {name: i, "who": who}))
    return sorted(out)


NOT_FRAMES = ["what is {C}", "what are the signs of {C}", "how do you treat {C}", "tell me about {C}", "how common is {C}",
              "{P} had {C} in 2019", "{P} had {C} last year", "{P} had {C} years ago", "{P} nearly had {C} when i saw the bill",
              "{P} is having {C} in the movie", "a man in the news had {C}", "{P} read about {C} online",
              "did my dad have {C} last year", "has my mum ever had {C} before", "what happens to someone who has {C}"]
PEOPLE = ["i", "we", "he", "she", "my dad"]


def _generated_not() -> list:
    d = medical_safety._data()["self_assess"]
    terms = [t for ts in d["conditions"].values() for t in ts]
    out = set()
    for f in NOT_FRAMES:
        for term in terms:
            art = f"a {term}" if term[0] not in "aeiou" else f"an {term}"
            for p in (PEOPLE if "{P}" in f else [""]):
                out.add(f.replace("{C}", art).replace("{P}", p))
    return sorted(out)


def test_generated_must_fire_set_is_large_and_every_one_fires():
    fire = _generated_fire()
    assert len(fire) > 1000
    missed = [t for t in fire if medical_safety.self_assessment(t).cls != "red_call"]
    assert missed == []


def test_generated_must_not_set_never_fires():
    nots = _generated_not()
    assert len(nots) > 500
    fired = [t for t in nots if medical_safety.self_assessment(t).cls or medical_safety.detect(t).cls]
    assert fired == []


@pytest.mark.parametrize("text", ["what if this is a heart attack", "do i have a heart attack", "could i have had a stroke",
                                  "does my dad have a stroke", "could my wife have had a heart attack", "what if im having a stroke"])
def test_the_three_review_shapes_and_their_class_fire(text):
    assert medical_safety.self_assessment(text).cls == "red_call"


@pytest.mark.parametrize("text, key", [("am i having a hart attack", "heart"), ("is he having a heart atack", "heart"),
                                       ("is this a stoke", "stroke"), ("is my dad having a strok", "stroke"),
                                       ("is it anafylaxis", "breathing"), ("could this be anaphalaxis", "breathing")])
def test_listed_speech_to_text_variants_fire(text, key):
    assert medical_safety.self_assessment(text) == medical_safety.Verdict("red_call", key)


@pytest.mark.parametrize("text", ["stoke the fire", "can you stoke the fire for me", "is this a strike", "is it the heat",
                                  "is that smoke", "is this a stroke of luck", "are you stoked", "is he having a heart to heart",
                                  "is she having a hard time", "is that a heat wave", "is it a stroll"])
def test_variants_never_fire_on_real_other_words(text):
    assert medical_safety.self_assessment(text).cls == ""


def test_control_the_previous_hand_regex_missed_the_review_shapes_and_typos():
    old = re.compile(r"(?<!\w)(?:(?:am\s+i|is\s+\w+|are\s+(?:you|they|we))\s+having\s+(?:a\s+|an\s+)?(?:heart\s?attack|stroke))")
    for text in ("what if this is a heart attack", "do i have a heart attack", "could i have had a stroke", "am i having a hart attack"):
        assert not old.search(text) and medical_safety.self_assessment(text).cls == "red_call"
