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
    assert set(conditions) == {"heart", "stroke", "breathing", "poison_human"}
    for key, terms in conditions.items():
        assert key in replies and terms
        for term in terms:                                                    # each term and variant reaches its own reply
            assert medical_safety.self_assessment(f"is this {term}") == _expected(key), term
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


VERB_SLOTS = ("ING", "ED", "BASE")


def _expand(frame: str, term: str, choice: dict) -> str:
    slots = medical_safety._data()["self_assess"]["slots"]
    out = frame.replace("{C}", f"a {term}" if term[0] not in "aeiou" else f"an {term}")
    for v in VERB_SLOTS:
        out = out.replace("{" + v + "}", term)
    for name in re.findall(r"\{([A-Z]+)\}", out):
        out = out.replace("{" + name + "}", slots[name][choice.get(name, 0)], 1)
    return out.replace("{who}", choice.get("who", WHO_SAMPLES[0]))


def _expected(key: str) -> "medical_safety.Verdict":
    cls = medical_safety._data()["self_assess"].get("verdicts", {}).get(key, "red_call")
    return medical_safety.Verdict(cls, key)


def _terms_for(frame: str) -> list:
    """(term, key) pairs a frame takes: the condition nouns for {C}, the condition's own verb forms for {ING} / {ED} / {BASE}."""
    d = medical_safety._data()["self_assess"]
    verb = next((v for v in VERB_SLOTS if "{" + v + "}" in frame), None)
    if verb:
        return [(t, key) for key, forms in d.get("verb_conditions", {}).items() for t in forms.get(verb, [])]
    return [(t, key) for key, ts in d["conditions"].items() for t in ts]


def _generated_fire() -> list:
    """(sentence, expected verdict) for every frame x every condition term (nouns and verb forms) x every speaker / slot form."""
    d = medical_safety._data()["self_assess"]
    out = {}
    for frame in d["frames"]:
        pairs = _terms_for(frame)
        for term, key in pairs:
            out[_expand(frame, term, {})] = _expected(key)
        term, key = pairs[0]
        for name in [n for n in re.findall(r"\{([A-Z]+)\}", frame) if n != "C" and n not in VERB_SLOTS]:
            for i in range(len(d["slots"][name])):
                for who in WHO_SAMPLES:
                    out[_expand(frame, term, {name: i, "who": who})] = _expected(key)
    return sorted(out.items())


NOT_FRAMES = ["what is {C}", "what are the signs of {C}", "how do you treat {C}", "tell me about {C}", "how common is {C}",
              "{P} had {C} in 2019", "{P} had {C} last year", "{P} had {C} years ago", "{P} nearly had {C} when i saw the bill",
              "{P} is having {C} in the movie", "a man in the news had {C}", "{P} read about {C} online",
              "did my dad have {C} last year", "has my mum ever had {C} before", "what happens to someone who has {C}"]
#: the verb forms' own must-not shapes: an idiom ("overdosed on chocolate / netflix"), a past narrative, fiction, a far-away third party
NOT_VERB_FRAMES = ["{P} {ED} on chocolate", "{P} {ED} on netflix last night", "i think {P} {ED} on chocolate", "ive {ED} on netflix",
                   "my friend {ED} in 2015", "{P} is {ING} in the movie", "a man in the news {ED}", "what happens if you {BASE}",
                   "how many people {BASE} each year", "{P} {ED} years ago"]
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
    for f in NOT_VERB_FRAMES:
        for term, _key in _terms_for(f):
            for p in (PEOPLE if "{P}" in f else [""]):
                out.add(_expand(f.replace("{P}", p), term, {}))
    return sorted(out)


def test_generated_must_fire_set_is_large_and_every_one_fires():
    fire = _generated_fire()
    assert len(fire) > 1000 and any(v.cls == "ingest_human" for _t, v in fire)       # overdose is in it automatically
    missed = [t for t, want in fire if medical_safety.self_assessment(t) != want]
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


# ── overdose: which floor answers first (pinned), and what it never grabs ──────────────────────────────────────────────────────
def test_the_seam_runs_the_distress_hand_off_before_the_medical_floor():
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "zoe_flue_client.py").read_text(encoding="utf-8")
    assert src.index("await distress_handoff.handle(") < src.index("await medical_safety.handle(")


def _distress_default(monkeypatch):
    for k in ("ZOE_DISTRESS_HANDOFF", "ZOE_DISTRESS_GENTLE"):
        monkeypatch.delenv(k, raising=False)


def test_an_accidental_sounding_overdose_question_is_the_medical_floors(monkeypatch):
    import distress_handoff

    _distress_default(monkeypatch)
    text = "did i just overdose by accident"
    v = distress_handoff.detect(text)
    assert v.tier == "gentle" and distress_handoff.mode_for(v.tier) == "shadow"     # the hand-off does not answer it
    assert run(distress_handoff.handle(text, "demo_bar_00000001")) is None
    monkeypatch.setenv(medical_safety.SELF_ASSESS_ENV, "enforce")
    assert run(medical_safety.handle(text, "demo_bar_00000001")) == medical_safety.build_reply(
        medical_safety.Verdict("ingest_human", "poison_human"))


@pytest.mark.parametrize("text", ["i took all my tablets because i want to die", "i want to overdose", "i took all my tablets"])
def test_an_intentional_sounding_statement_is_the_distress_hand_offs(text, monkeypatch):
    import distress_handoff

    _distress_default(monkeypatch)
    v = distress_handoff.detect(text)
    assert v.tier == "handoff" and distress_handoff.mode_for(v.tier) == "enforce"   # answered first; the medical floor is never reached
    assert medical_safety.self_assessment(text).cls == ""                          # and this floor was not widened to grab it


def test_a_third_party_ingestion_is_the_poisoning_floors_not_this_ones(monkeypatch):
    import distress_handoff

    text = "my son swallowed my tablets"
    assert distress_handoff.detect(text).tier == "none"
    assert medical_safety.detect(text) == medical_safety.Verdict("ingest_human", "poison_human", lead="my son ")
    assert medical_safety.self_assessment(text).cls == ""


def test_a_pet_overdose_gets_the_vet_reply(monkeypatch):
    monkeypatch.setenv(medical_safety.SELF_ASSESS_ENV, "enforce")
    assert medical_safety.self_assessment("is my dog overdosing") == medical_safety.Verdict("ingest_animal", "poison_animal")
    assert medical_safety.self_assessment("am i overdosing, the dog is fine") == medical_safety.Verdict("ingest_human", "poison_human")


@pytest.mark.parametrize("text", ["what is the maximum dose of paracetamol", "can i take two more paracetamol",
                                  "how many ibuprofen can i take in a day", "what is a safe dose for a child"])
def test_a_dose_question_keeps_todays_behaviour(text, monkeypatch):
    monkeypatch.setenv(medical_safety.SELF_ASSESS_ENV, "enforce")
    assert medical_safety.self_assessment(text).cls == "" and medical_safety.detect(text).cls == ""
    assert run(medical_safety.handle(text, "demo_bar_00000001")) is None             # the brain answers it, as before


@pytest.mark.parametrize("text", ["ive overdosed on netflix", "i think i overdosed on chocolate", "we overdosed on cake"])
def test_the_overdosed_on_idiom_never_fires_but_a_substance_does(text):
    assert medical_safety.self_assessment(text).cls == ""
    assert medical_safety.self_assessment("shes overdosed on her tablets").cls == "ingest_human"


@pytest.mark.parametrize("text", ["did i just overdose", "has she just overdosed", "is he overdosing", "ive overdosed",
                                  "could this be an overdose", "am i overdozing"])
def test_overdose_verb_forms_fire_from_the_data(text):
    assert medical_safety.self_assessment(text).cls == "ingest_human"


# ── review #2525 PRRT ...ethI: the MOST urgent verdict wins, never the first computed ─────────────────────────────────────────
def test_the_urgency_order_is_explicit_and_pinned():
    assert medical_safety.URGENCY == (("ingest_human", "object_urgent"), ("ingest_human", "poison_human"), ("red_call", None),
                                      ("ingest_human", "object_minor"), ("ingest_animal", None), ("number", None),
                                      ("red_soon", None), ("glucose", None))
    assert medical_safety.urgency(medical_safety.NONE) == len(medical_safety.URGENCY)


#: one phrase per LOWER-urgency class detect returns; each must be outranked by every self-assessment condition
LOWER = {("red_soon", "vision"): "my vision is blurry", ("red_soon", "blood_out"): "there is blood in my wee",
         ("glucose", "glucose"): "my blood sugar is 6.5", ("number", "emergency_number"): "what number do i ring",
         ("ingest_human", "object_minor"): "my son swallowed a coin"}
CONDITION_ASKS = {"heart": "am i having a heart attack", "stroke": "is this a stroke", "breathing": "is this anaphylaxis",
                  "poison_human": "am i overdosing"}


def _pairings() -> list:
    out = []
    for (cls, key), lower in LOWER.items():
        for cond, ask in CONDITION_ASKS.items():
            for text in (f"{lower}, {ask}?", f"{ask}? {lower}"):
                out.append((text, (cls, key), cond))
    return out


@pytest.mark.parametrize("text, lower, cond", _pairings())
def test_a_lower_urgency_class_never_hides_the_self_assessment_in_either_order(text, lower, cond, monkeypatch):
    monkeypatch.setenv(medical_safety.SELF_ASSESS_ENV, "enforce")
    assert medical_safety.detect(text).cls == lower[0]                       # detect alone reads the lesser class
    want = _expected(cond)
    assert medical_safety.urgency(want) < medical_safety.urgency(medical_safety.detect(text))
    assert run(medical_safety.handle(text, "demo_bar_00000001")) == medical_safety.build_reply(want)


def test_control_first_verdict_wins_gives_the_lesser_reply(monkeypatch):
    monkeypatch.setenv(medical_safety.SELF_ASSESS_ENV, "enforce")
    monkeypatch.setattr(medical_safety, "urgency", lambda v: 0 if v.cls else 99)     # a43fd2235's rule: detect first, whatever it is
    text = "my vision is blurry, is this a stroke?"
    assert run(medical_safety.handle(text, "demo_bar_00000001")) == medical_safety.build_reply(medical_safety.detect(text))


def test_shadow_still_changes_no_reply_when_a_lower_class_is_present():
    text = "my vision is blurry, is this a stroke?"
    assert run(medical_safety.handle(text, "demo_bar_00000001")) == medical_safety.build_reply(medical_safety.detect(text))


# ── review #2525 PRRT ...ethM: the condition comes from its named slot, never from re-searching the match ─────────────────────
def _name_like_terms() -> list:
    """Every single-word condition term or verb form in the data: each could be someone's name or a word in the subject slot."""
    d = medical_safety._data()["self_assess"]
    words = [t for ts in d["conditions"].values() for t in ts if " " not in t and "-" not in t]
    words += [t for forms in d.get("verb_conditions", {}).values() for ts in forms.values() for t in ts]
    return sorted(set(words))


def _collisions() -> list:
    d = medical_safety._data()["self_assess"]
    term_key = {t: k for k, ts in d["conditions"].items() for t in ts}
    out = []
    for frame in d["frames"]:
        who_slots = [n for n in re.findall(r"\{([A-Z]+)\}", frame) if any("{who}" in p for p in d["slots"].get(n, []))]
        if not who_slots:
            continue
        slot = who_slots[0]
        idx = next(i for i, p in enumerate(d["slots"][slot]) if "{who}" in p)
        for name in _name_like_terms():
            for term, key in _terms_for(frame):
                if term_key.get(name) == key or (key == "poison_human" and name in [t for ts in d["verb_conditions"]["poison_human"].values() for t in ts]):
                    continue                                                  # the name must differ from the condition's own class
                out.append((_expand(frame, term, {slot: idx, "who": name}), key))
                break
    return out


def test_a_subject_named_like_a_condition_never_becomes_the_condition():
    pairs = _collisions()
    assert len(pairs) > 100 and any(" tia " in f" {t} " for t, _k in pairs)
    wrong = [(t, k, medical_safety.self_assessment(t).key) for t, k in pairs if medical_safety.self_assessment(t) != _expected(k)]
    assert wrong == []


def test_control_re_searching_the_whole_match_picks_the_name():
    text = "is tia having a heart attack"
    cond_rx, _keys = medical_safety._sa_cond()
    m = medical_safety._sa_present_re().search(text)
    assert re.search(cond_rx, m.group(0)).group(0) == "tia"                     # a43fd2235's extraction: the NAME
    assert medical_safety.self_assessment(text) == _expected("heart")           # the named slot: the condition


# ── review #2525 PRRT ...e3tI: "overdos* on X" FIRES unless X is a closed idiom object (fail-safe) ─────────────────────────────
def _sa():
    return medical_safety._data()["self_assess"]


@pytest.mark.parametrize("text", ["am i overdosing on heroin", "my dad is overdosing on cocaine"])
def test_the_threads_examples_fire(text):
    assert medical_safety.self_assessment(text) == _expected("poison_human")


@pytest.mark.parametrize("text", ["am i overdosing on zorblex", "is she overdosing on my dads tablets", "has he overdosed on pain killers",
                                  "shes overdosed on something and she wont wake up", "am i overdosing", "has she overdosed on it"])
def test_an_unknown_object_a_multi_word_object_more_clause_or_none_fires(text):
    assert medical_safety.self_assessment(text) == _expected("poison_human")


def _drug_and_unknown_objects() -> list:
    return _sa()["drug_names"][::3] + ["zorblex", "something", "my dads tablets", "whatever he had", "those pills"]


def _overdose_on_fire() -> list:
    """Every verb form x every speaker form x drug names and unknown words, the third-party forms included."""
    forms = _sa()["verb_conditions"]["poison_human"]
    slots = _sa()["slots"]
    out = set()
    for x in _drug_and_unknown_objects():
        for ing in forms["ING"]:
            for q in slots["Q"]:
                out.add(f"{q} {ing} on {x}".replace("{who}", "my dad"))
        for ed in forms["ED"]:
            for h in slots["HAS"] + slots["PERF"]:
                out.add(f"{h} {ed} on {x}".replace("{who}", "my wife"))
    return sorted(out)


def test_generated_overdose_on_x_fires_for_every_form_speaker_and_object():
    fire = _overdose_on_fire()
    assert len(fire) > 400
    assert [t for t in fire if medical_safety.self_assessment(t) != _expected("poison_human")] == []


def test_firing_does_not_depend_on_the_drug_list(monkeypatch):
    monkeypatch.setitem(medical_safety._data()["self_assess"], "drug_names", [])
    assert medical_safety.self_assessment("am i overdosing on heroin") == _expected("poison_human")


def test_every_idiom_object_with_every_verb_form_never_fires():
    forms = _sa()["verb_conditions"]["poison_human"]
    nots = [f"ive {ed} on {x}" for x in _sa()["verb_idiom_objects"] for ed in forms["ED"]]
    nots += [f"am i {ing} on {x}" for x in _sa()["verb_idiom_objects"] for ing in forms["ING"]]
    nots += [f"i think i {ed} on so much {x}" for x in _sa()["verb_idiom_objects"] for ed in forms["ED"]]
    assert len(nots) > 300
    assert [t for t in nots if medical_safety.self_assessment(t).cls] == []


@pytest.mark.parametrize("text, fires", [("ive overdosed on coffee today", False), ("i overdosed on caffeine this morning lol", False),
                                         ("am i overdosing on caffeine?", True), ("could he be overdosing on energy drinks", True),
                                         ("is she overdosing on caffeine", True), ("we have overdosed on tea", False)])
def test_the_caffeine_family_is_an_idiom_unless_asked_about_now(text, fires):
    assert (medical_safety.self_assessment(text) == _expected("poison_human")) is fires


def test_control_the_old_substance_gate_missed_the_threads_examples():
    sub = medical_safety._data()["ingest"]["substance"]
    old_guard = re.compile(rf"overdosing(?!\s+on\s+(?!(?:(?:my|his|her|their|the|some|all|those|these|a|an|too|many|much)\s+)*(?:{sub})(?!\w)))")
    for text in ("am i overdosing on heroin", "my dad is overdosing on cocaine"):
        assert not old_guard.search(text) and medical_safety.self_assessment(text).cls == "ingest_human"
