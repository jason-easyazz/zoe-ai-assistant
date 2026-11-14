"""Too many / too much of a medicine, present or recent ("did i take too many", "i just took too many", "she has had too much of her
medicine"): the human poisoning reply behind ``ZOE_MEDICAL_TOO_MANY`` (unset = shadow; exactly off / shadow; any other value
enforces). Generated must-fire / must-not sets from the same data allow-list the detector compiles; the distress boundary pinned.
Placeholder phrasings only (ci_safe)."""
from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

import pytest

import distress_handoff
import medical_safety

pytestmark = pytest.mark.ci_safe
UID = "demo_bar_00000001"
POISON = medical_safety.Verdict("ingest_human", "poison_human")


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    async def no(*_a, **_k):
        return False
    monkeypatch.setattr(distress_handoff, "_is_minor", no)
    monkeypatch.setattr(medical_safety, "_intercom_reachable", no)
    for k in ("ZOE_MEDICAL_SAFETY", medical_safety.TOO_MANY_ENV, medical_safety.SELF_ASSESS_ENV, "ZOE_DISTRESS_HANDOFF",
              "ZOE_DISTRESS_GENTLE"):
        monkeypatch.delenv(k, raising=False)


# ── generated from the data the detector compiles ────────────────────────────────────────────────────────────────────────────
WHO = ["my dad", "my wife", "grandma"]
TAKE_OBJ = ["", " tablets", " of my pills", " paracetamol", " of his medication"]
HAD_OBJ = [" of my medicine", " tablets", " paracetamol", " of her meds"]


def _fill(frame: str, verb_word: str, amt: str, obj: str, choice: dict) -> str:
    slots = medical_safety._data()["self_assess"]["slots"]
    verb = next(v for v in ("TAKE", "HAD", "SWALLOW") if "{" + v + "}" in frame)
    out = frame.replace("{" + verb + "}", f"{verb_word} {amt}{obj}")
    for name in re.findall(r"\{([A-Z]+)\}", out):
        out = out.replace("{" + name + "}", slots[name][choice.get(name, 0)], 1)
    return out.replace("{who}", choice.get("who", WHO[0]))


def _generated_fire() -> list:
    d = medical_safety._data()["too_many"]
    out = set()
    for f in d["frames"]:
        verb = next(v for v in ("TAKE", "HAD", "SWALLOW") if "{" + v + "}" in f)
        objs = HAD_OBJ if verb == "HAD" else TAKE_OBJ
        for word in d["verbs"][verb]:
            for amt in d["amount"]:
                for obj in objs:
                    out.add(_fill(f, word, amt, obj, {}))
        for name in [n for n in re.findall(r"\{([A-Z]+)\}", f) if n not in ("TAKE", "HAD", "SWALLOW")]:
            for i in range(len(medical_safety._data()["self_assess"]["slots"][name])):
                for who in WHO:
                    out.add(_fill(f, d["verbs"][verb][0], d["amount"][0], objs[1], {name: i, "who": who}))
    return sorted(out)


NOT_TEMPLATES = [
    "{P} had too many biscuits", "{P} had too much coffee", "{P} had too much cake", "{P} had too many chips at lunch",
    "{P} took too many photos", "it took too much time", "{P} took too many steps today", "{P} took too much on at work",
    "{P} took too many pills in 2019", "{P} took too much paracetamol years ago", "{P} took too many tablets last year",
    "what happens if you take too many paracetamol", "what if {P} took too many pills", "how many paracetamol is too many",
    "how much ibuprofen is too much", "is it dangerous to take too many vitamins", "{P} took too many vitamins",
    "the character took too many pills in the movie", "a man in the news took too many pills last week",
    "{P} took too many on purpose", "{P} had too much to drink",
]
PEOPLE = ["i", "we", "he", "she", "my dad"]


def _generated_not() -> list:
    return sorted({t.replace("{P}", p) for t in NOT_TEMPLATES for p in (PEOPLE if "{P}" in t else [""])})


def test_generated_must_fire_set_is_large_and_every_one_fires():
    fire = _generated_fire()
    assert len(fire) > 900                                                   # the generator did not collapse
    assert [t for t in fire if medical_safety.too_many_ingestion(t) != POISON] == []


def test_generated_must_not_set_never_fires():
    nots = _generated_not()
    assert len(nots) > 70
    assert [t for t in nots if medical_safety.too_many_ingestion(t).cls] == []


@pytest.mark.parametrize("text", ["did i take too many", "i think i took too many tablets", "she has had too much of her medicine",
                                  "i just took too many", "have i taken too many", "i might have taken too many", "i swallowed too many",
                                  "did i take too much paracetamol?", "he just took too much of his medication"])
def test_the_briefs_shapes_fire(text):
    assert medical_safety.too_many_ingestion(text) == POISON


def test_a_pet_gets_the_vet_reply():
    assert medical_safety.too_many_ingestion("my dog has had too many of my tablets") == medical_safety.Verdict(
        "ingest_animal", "poison_animal")


# ── the mode ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("value, mode", [(None, "shadow"), ("", "shadow"), ("off", "off"), ("shadow", "shadow"), ("enforce", "enforce"),
                                         ("enforced", "enforce"), ("on", "enforce"), ("1", "enforce"), ("garbage", "enforce")])
def test_only_unset_off_or_shadow_stay_below_enforce(value, mode, monkeypatch):
    if value is not None:
        monkeypatch.setenv(medical_safety.TOO_MANY_ENV, value)
    assert medical_safety.too_many_mode() == mode
    reply = run(medical_safety.handle("did i take too many", UID))
    assert (reply == medical_safety.build_reply(POISON)) is (mode == "enforce")
    if mode != "enforce":
        assert reply is None                                                 # off and shadow: today's reply (none), byte for byte


def test_shadow_logs_a_fixed_code_and_never_the_words(caplog):
    with caplog.at_level(logging.INFO, logger="medical_safety"):
        assert run(medical_safety.handle("i just took too many", UID)) is None
    assert "MEDICAL_TOO_MANY" in caplog.text and "mode=shadow" in caplog.text and "took" not in caplog.text


def test_detect_and_filing_are_unchanged():
    assert medical_safety.detect("did i take too many").cls == ""
    assert medical_safety.emergency_event("User took too many tablets", "did i take too many") is None


def test_the_flag_is_registered():
    import flag_registry

    s = flag_registry.spec(medical_safety.TOO_MANY_ENV)
    assert s.default == "shadow" and tuple(s.allowed) == ("off", "shadow", "enforce")


def test_control_without_this_detector_these_reach_the_brain(monkeypatch):
    monkeypatch.setenv(medical_safety.TOO_MANY_ENV, "enforce")
    monkeypatch.setattr(medical_safety, "too_many_ingestion", lambda _t: medical_safety.NONE)
    for text in ("did i take too many", "i just took too many", "she has had too much of her medicine"):
        assert run(medical_safety.handle(text, UID)) is None                  # the gap this closes


# ── the distress boundary and the other floors (the seam runs the hand-off first) ───────────────────────────────────────────
def test_the_seam_runs_the_distress_hand_off_first():
    src = (Path(__file__).resolve().parents[1] / "zoe_flue_client.py").read_text(encoding="utf-8")
    assert src.index("await distress_handoff.handle(") < src.index("await medical_safety.handle(")


def test_an_accidental_one_reaches_this_floor(monkeypatch):
    for text in ("did i just take too many by accident", "i took too many by mistake"):
        assert distress_handoff.detect(text).tier in ("none", "gentle")
        assert run(distress_handoff.handle(text, UID)) is None                  # the hand-off does not answer it
        monkeypatch.setenv(medical_safety.TOO_MANY_ENV, "enforce")
        assert medical_safety.too_many_ingestion(text) == POISON
        assert run(medical_safety.handle(text, UID)) == medical_safety.build_reply(POISON)


@pytest.mark.parametrize("text", ["i took all my tablets because i want to die", "i want to overdose"])
def test_an_intentional_one_the_hand_off_catches_is_the_hand_offs(text):
    v = distress_handoff.detect(text)
    assert v.tier == "handoff" and distress_handoff.mode_for(v.tier) == "enforce"
    assert medical_safety.too_many_ingestion(text).cls == ""


@pytest.mark.parametrize("text", ["i took too many on purpose", "i took them all because i want it to stop"])
def test_an_intentional_statement_is_never_answered_by_this_detector(text, monkeypatch):
    """FINDING: today these reach no floor (distress tier none). The distress lexicon is being fixed on another branch
    (fix/distress-intentional-overdose), so this asserts only what THIS detector owns - it never answers them - and not that the
    turn reaches the brain, which flips once that lands."""
    monkeypatch.setenv(medical_safety.TOO_MANY_ENV, "enforce")
    assert medical_safety.too_many_ingestion(text).cls == "" and medical_safety.detect(text).cls == ""


def test_a_third_party_ingestion_is_still_the_poisoning_detectors():
    text = "my son took too many of my pills"
    assert distress_handoff.detect(text).tier == "none"
    assert medical_safety.detect(text).cls == "ingest_human"                    # answered before this detector is consulted


@pytest.mark.parametrize("text", ["i had too much to drink", "i drank too much", "i had too many biscuits", "i had too much coffee",
                                  "i took too many vitamins", "what is the maximum dose of paracetamol",
                                  "how many paracetamol is too many"])
def test_food_drink_vitamins_and_dose_questions_keep_todays_behaviour(text, monkeypatch):
    monkeypatch.setenv(medical_safety.TOO_MANY_ENV, "enforce")
    assert run(medical_safety.handle(text, UID)) is None                        # no floor, as before: the brain answers


# ── ranked through URGENCY: a lower-urgency class never hides it, in either order ────────────────────────────────────────────
LOWER = {"red_soon": "my vision is blurry", "glucose": "my blood sugar is 6.5"}
LOWER_EXTRA = ["there is blood in my wee"]           # a second see-today symptom
ASKS = ["did i take too many", "i just took too many", "she has had too much of her medicine"]


@pytest.mark.parametrize("text", [f"{lo}, {ask}?" for lo in [*LOWER.values(), *LOWER_EXTRA] for ask in ASKS] +
                                 [f"{ask}? {lo}" for lo in [*LOWER.values(), *LOWER_EXTRA] for ask in ASKS])
def test_a_lower_urgency_class_never_hides_it(text, monkeypatch):
    monkeypatch.setenv(medical_safety.TOO_MANY_ENV, "enforce")
    assert medical_safety.detect(text).cls in LOWER                              # detect alone reads the lesser class
    assert run(medical_safety.handle(text, UID)) == medical_safety.build_reply(POISON)


def test_beside_a_call_now_red_flag_the_urgency_list_decides_poison_first_as_detect_does(monkeypatch):
    monkeypatch.setenv(medical_safety.TOO_MANY_ENV, "enforce")
    text = "i have crushing chest pain and i just took too many"
    assert medical_safety.detect(text).cls == "red_call"
    assert medical_safety.urgency(POISON) < medical_safety.urgency(medical_safety.detect(text))
    assert run(medical_safety.handle(text, UID)) == medical_safety.build_reply(POISON)



# ── a very low blood sugar outranks a see-today symptom (ruling 2026-11-14) ──────────────────────────────────────────────────
@pytest.mark.parametrize("text", ["my vision is blurry and my blood sugar is 2.1", "my blood sugar is 2.1 and my vision is blurry",
                                  "my vision is blurry, my sugar is 2.8", "my sugar is 2.8, my vision is blurry"])
def test_a_very_low_blood_sugar_outranks_a_see_today_symptom_in_either_order(text):
    v = medical_safety.detect(text)
    assert v.cls == "glucose" and medical_safety._band(v.value, v.unit) == "glucose_low"


@pytest.mark.parametrize("text", ["my vision is blurry and my blood sugar is 6.5", "my blood sugar is 9.8 and my vision is blurry"])
def test_any_other_reading_stays_below_the_see_today_symptom(text):
    assert medical_safety.detect(text).cls == "red_soon"


def test_control_detect_first_found_order_gave_the_see_today_reply(monkeypatch):
    monkeypatch.setattr(medical_safety, "urgency", lambda v: {"red_soon": 0}.get(v.cls, 9))     # d0bb71d86's order: soon first
    assert medical_safety.detect("my vision is blurry and my blood sugar is 2.1").cls == "red_soon"


# ── the animal decision reads the frame's own subject (the class fixed on #2525, applied here) ──────────────────────────────────
def _human_with_animal_mention() -> list:
    out = []
    for ask in ASKS:
        out += [f"{ask}? the dog is fine", f"the cat is asleep, {ask}", f"{ask} and my dog is barking"]
    return out


@pytest.mark.parametrize("text", _human_with_animal_mention())
def test_an_animal_mentioned_elsewhere_leaves_it_human_in_either_order(text):
    assert medical_safety.too_many_ingestion(text) == POISON


@pytest.mark.parametrize("text", ["did my dog just take too many", "my dog has had too many of my tablets",
                                  "has my cat had too many of my pills, my son is fine"])
def test_mirror_an_animal_as_the_frames_own_subject_gets_the_vet_reply(text):
    assert medical_safety.too_many_ingestion(text) == medical_safety.Verdict("ingest_animal", "poison_animal")


# ── drug names (the self-assessment's list, one source) are substances here; the food-or-drink ones are not ──────────────────
@pytest.mark.parametrize("text", ["i took too much cocaine", "did i take too much heroin", "i had too much fentanyl",
                                  "she has had too many xanax"])
def test_a_named_drug_is_a_substance(text):
    assert medical_safety.too_many_ingestion(text) == POISON


@pytest.mark.parametrize("text", ["i had too much ice cream", "i had too much coke", "we had too much ice"])
def test_a_drug_word_that_is_also_food_or_drink_is_not(text):
    assert medical_safety.too_many_ingestion(text).cls == ""


# ── cases decided / deferred ─────────────────────────────────────────────────────────────────────────────────────────────────
def test_decided_a_heart_question_beside_a_stated_drug_ingestion_gets_both_lines(monkeypatch):
    """"am i having a heart attack, i took too much cocaine": on #2525 alone only the heart verdict exists (nothing read the drug
    statement). Here the statement is read (a named drug), so there is a poisoning verdict AND the speaker's own call-now: by
    #2525's cross-detector combine the reply is BOTH fixed replies - the speaker's unconditional call-now first, then the poisoning
    one with the Poisons line - so neither the call-now nor the poisons line is lost."""
    monkeypatch.setenv(medical_safety.TOO_MANY_ENV, "enforce")
    monkeypatch.setenv(medical_safety.SELF_ASSESS_ENV, "enforce")
    heart = medical_safety.build_reply(medical_safety.Verdict("red_call", "heart"))
    for text in ("am i having a heart attack, i took too much cocaine", "i took too much cocaine, am i having a heart attack"):
        assert run(medical_safety.handle(text, UID)) == f"{heart} {medical_safety.build_reply(POISON)}"


def test_deferred_a_pet_in_a_present_relevant_past_question_gets_the_human_reply_today():
    """DEFERRED (review of #2525, resolved by the coordinator): "has my dog had an overdose today" gets the HUMAN poisoning reply,
    because the past-question shape captures no subject slot. That is the safe direction; it flips to the vet reply when the
    past-question shape captures its subject. Pinned as today's behaviour."""
    assert medical_safety.self_assessment("has my dog had an overdose today") == POISON
