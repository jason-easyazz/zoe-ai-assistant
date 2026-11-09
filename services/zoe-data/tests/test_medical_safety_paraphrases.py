"""Medical safety floor, PARAPHRASE coverage (QA round 7). A generated set - templates x synonyms - not a phrase list: the class of wording the floor must read the same
way, with a precision / recall report in the test output (run with ``-s``, or read the "paraphrase report" section of the pytest summary). Synthetic text only. ``ci_safe``.

Positives: every row must land on its expected (class, key). Negatives: every row must come back with NO verdict (or, for the pet rows, a vet answer, never a human one).
Held-out hand-written paraphrases are reported separately and are NOT asserted: they show what is deliberately left to the brain.
"""
from __future__ import annotations

import itertools
from collections import defaultdict

import pytest

import medical_safety as ms

pytestmark = pytest.mark.ci_safe

@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for k in (ms.ENV, "ZOE_HOUSEHOLD_COUNTRY", "ZOE_LOCATION_COUNTRY", "ZOE_DISTRESS_LINES_FILE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ZOE_HOUSEHOLD_COUNTRY", "AU")


# -- positives ---------------------------------------------------------------------------------------------------------------------
PEOPLE = ["my son", "my daughter", "the baby", "my toddler", "wren", "max", "my little girl", "my nephew"]
VERBS = ["just swallowed", "has swallowed", "swallowed", "just ate", "has eaten", "is chewing and swallowed"]
URGENT = ["a button battery", "a magnet", "two magnets", "a little magnet", "a watch battery", "a needle", "a safety pin", "a screw"]
MINOR = ["a coin", "a lego bit", "a lego piece", "a small toy part", "a marble", "a bead", "a bottle cap", "a pebble", "a tiny plastic piece", "a button"]
POISON = ["bleach", "dishwasher powder", "a laundry pod", "some paracetamol tablets", "antifreeze", "a handful of vitamins tablets", "nail polish remover cleaner",
          "weed killer", "some of the oven cleaner", "a cleaning tablet"]
CHEST_SYM = ["tight", "tightness", "pressure", "pain", "squeezing", "heavy", "crushing", "constricted"]
CHEST_T = ["my chest feels {s}", "my chest is {s}", "my chest has gone {s}", "i have a {s} feeling in my chest", "got a {s} feeling in my chest, been going an hour",
           "{s} in my chest", "there is {s} across my chest", "he says his chest is {s}"]
CHEST_N = ["a tight feeling in my chest", "a pressure in my chest", "chest tightness", "chest pressure", "chest pain", "tightness in my chest", "my chest is tight",
           "chest feels tight", "got a tight feeling in my chest, it wont stop", "my chest feels constricted and i cant stop shaking", "chest pain and i cant stop sweating", "my chest is tight and my eyes are watering from the topic", "chest is really tight", "a squeezing pain in my chest", "pressure on my chest", "my chest hurts"]
VISION_S = ["blurry", "blurred", "foggy", "hazy", "double", "fuzzy", "cloudy"]
VISION_T = ["my eyes are {s}", "my vision is {s}", "my vision has gone {s}", "{s} vision", "i have {s} vision since this morning", "my eyes went {s}",
            "bad headache for 3 days and now my eyes are {s}", "her vision keeps going {s}"]
VISION_N = ["i can't see properly", "cant see clearly", "i cant see straight", "everything is blurry", "everything looks double", "i lost vision in one eye", "i have lost my sight",
            "i cant see out of my left eye", "i am seeing double", "seeing floaters", "my eyesight is blurry"]
STROKE = ["his face is drooping", "her face is drooping on one side", "her smile is crooked and numb", "slurred speech", "his speech is slurred", "her speech went garbled",
          "he is slurring his words", "my arm suddenly went numb", "his leg suddenly went weak", "her face went numb", "my mouth is drooping", "my face is dropping"]
BREATH = ["i can't breathe properly", "i cant breathe", "my breathing is difficult", "his breathing is really hard", "her breathing became laboured", "hard to breathe",
          "i am struggling to breathe", "she is wheezing badly", "his lips are turning blue", "i am short of breath", "she can't get any air", "my throat is closing"]
BLEED = ["the bleeding won't stop", "the blood wont stop", "she is bleeding heavily", "he is bleeding a lot", "blood everywhere and it wont stop", "i am losing a lot of blood",
         "i keep coughing up blood", "vomiting up blood", "the cut keeps gushing blood"]
HEAD = ["the worst headache of my life", "worst headache ever", "a thunderclap headache", "headache and a stiff neck", "fever and stiff neck", "sudden severe headache"]
BLOOD = ["blood in my wee", "there is blood in my poo", "i am peeing blood", "bloody poo", "blood in the urine", "blood in my vomit", "i see blood when i wee", "passing blood"]
NUMQ = ["what number do i ring if {p} collapses", "which number do i call if {p} faints", "who do i call if {p} stops breathing", "what do i do if {p} has a seizure",
        "what is the number for an ambulance, {p} has fallen and hit their head", "what number should i dial if {p} is unconscious"]
NUMP = ["margaret", "my dad", "the baby", "nan"]
SUGAR = ["my blood sugar is {n}", "bsl {n}", "blood glucose reading of {n}", "my sugar was {n} this morning", "glucose is {n} mmol", "his blood sugar reads {n}"]
SUGARN = ["2.8", "3.4", "5.5", "8.2", "9.8", "13.4", "22"]
PETS = ["my dog", "the puppy", "my cat", "our kitten", "the budgie", "my pet rabbit"]
PET_V = ["swallowed", "ate", "has eaten"]
PET_O = ["a handful of dishwasher powder", "some bleach", "a battery", "some paracetamol tablets", "weed killer"]


def _every(items, k):   # deterministic thinning: every k-th, so the set stays a spread and not a corner of the product
    return [x for i, x in enumerate(items) if i % k == 0]


def _positives():
    rows = []
    add = lambda cat, texts, want: rows.extend((cat, t, want) for t in texts)   # noqa: E731
    add("swallowed urgent (000)", [f"{p} {v} {o}" for p, v, o in _every(list(itertools.product(PEOPLE, VERBS, URGENT)), 7)], ("ingest_human", "object_urgent"))
    add("swallowed minor (13 11 26 + watch)", [f"{p} {v} {o}" for p, v, o in _every(list(itertools.product(PEOPLE, VERBS, MINOR)), 8)], ("ingest_human", "object_minor"))
    add("swallowed poison (13 11 26)", [f"{p} {v} {o}" for p, v, o in _every(list(itertools.product(PEOPLE, VERBS, POISON)), 9)], ("ingest_human", "poison_human"))
    add("chest (000)", [t.format(s=s) for t, s in _every(list(itertools.product(CHEST_T, CHEST_SYM)), 3)] + CHEST_N, ("red_call", "heart"))
    add("vision (doctor today)", [t.format(s=s) for t, s in _every(list(itertools.product(VISION_T, VISION_S)), 3)] + VISION_N, ("red_soon", "vision"))
    add("stroke (000)", STROKE, ("red_call", "stroke"))
    add("breathing (000)", BREATH, ("red_call", "breathing"))
    add("bleeding (000)", BLEED, ("red_call", "bleeding"))
    add("worst headache (000)", HEAD, ("red_call", "worst_headache"))
    add("blood where it should not be", BLOOD, ("red_soon", "blood_out"))
    add("emergency number question", [t.format(p=p) for t, p in itertools.product(NUMQ, NUMP)], ("number", ""))
    add("blood sugar reading", [t.format(n=n) for t, n in itertools.product(SUGAR, SUGARN)], ("glucose", "glucose"))
    add("pet swallowed (vet)", [f"{p} {v} {o}" for p, v, o in _every(list(itertools.product(PETS, PET_V, PET_O)), 6)], ("ingest_animal", "poison_animal"))
    return rows


# -- negatives: ordinary talk that must NOT be hijacked -------------------------------------------------------------------------------
NEG = []
NEG += [f"i burnt the {x} again" for x in ["toast", "garlic", "roast", "sausages", "onions", "rice", "porridge", "pancakes"]]
NEG += [f"the {x} burns if you leave it on high" for x in ["sauce", "caramel", "milk", "butter", "curry"]]
NEG += [f"i got a chest of drawers for the {r}" for r in ["bedroom", "hallway", "spare room", "nursery"]]
NEG += ["the chest freezer is full of mince", "we bought a new chest freezer", "the toy chest is overflowing", "a pirate treasure chest for the party", "he has a hairy chest",
        "chest day at the gym tomorrow", "i did a chest press and then a chest fly", "the war chest is empty", "an old wooden chest in the attic", "ice chest for the esky",
        "i had a chest infection last month and it has cleared", "chest xray results were clear", "my chest cold is nearly gone"]
NEG += [f"my {x} is too tight across the chest" for x in ["shirt", "jacket", "wetsuit", "dress", "vest", "jumper"]]
NEG += [f"the {x} is tight" for x in ["lid", "jar", "tap", "bolt", "screw", "knot", "budget", "schedule"]]
NEG += [f"i swallowed my {x}" for x in ["pride", "words", "anger", "embarrassment"]]
NEG += [f"i ate a {x}" for x in ["button mushroom", "chocolate coin", "big piece of cake", "bit of toast", "piece of chicken"]]
NEG += [f"that {x} is blurry" for x in ["photo", "picture", "video", "screenshot", "scan of the receipt", "map"]]
NEG += ["my vision board needs a refresh", "blurry vision board ideas for the new year", "the photo came out blurry", "a blurry picture of the cat",
        "the camera focus keeps going blurry", "my glasses are foggy from the soup", "the windscreen is foggy this morning", "the mirror is fogged up"]
NEG += ["i can't see properly in the dark", "i cant see properly without my glasses", "cant see the screen properly in the sun", "i can't see the stars tonight"]
NEG += [f"my eyes are {x} from the onions" for x in ["blurry", "watery", "stinging"]]
NEG += ["i double checked the door", "double dose of coffee please", "double the recipe", "my eyes are tired", "my eyes are sore after the pool"]
NEG += ["i have a headache", "headache since lunch, took a panadol the normal way", "my head hurts a bit", "i have a stiff neck from sleeping badly"]
NEG += ["how long should the chicken rest", "what number is the vet", "what is the number for the plumber", "what number do i call for the dentist", "ring the school at nine"]
NEG += ["my arm is numb from sleeping on it", "my leg went to sleep on the couch", "the baby is breathing quietly asleep", "he is out of breath after the run"]
NEG += ["i put 2 sugars in my tea", "sugar is 3 dollars", "add 5 tablespoons of sugar", "blood orange cake recipe", "the sugar level in the recipe is 8 g"]
NEG += ["the dog is barking at the postman", "my cat sleeps all day", "the budgie is singing", "my rabbit hopped out of the hutch"]
NEG += [f"my {x} is bleeding heart roses" for x in ["nan"]] + ["bleeding heart plant in the garden", "the printer ink is bleeding on the page"]
NEG += ["we ate dinner at six", "the kids drank their milk", "she took her morning tablets as usual", "he swallowed his usual medication fine",
        "i took a nap", "the baby is teething and chewing her toy", "he is chewing gum", "the toddler ate all her pasta"]
NEG += ["the coin collection is on the shelf", "lego is all over the floor again", "tidy up the marbles", "a magnet on the fridge", "the battery is flat", "button up your coat",
        "put the needles away in the sewing box", "the nails need trimming", "screw the lid on tight"]
NEG += ["the weight of the box is heavy", "heavy rain on the roof", "chest of tea from the shop", "my bag feels heavy", "pain in the neck neighbour", "pressure washing the deck"]
NEG += ["there is a pain in the chest of drawers", "the pain in the chest of drawers is the broken runner", "the pressure washer hit my chest", "pressure washing the deck splashed my chest",
        "the pressure cooker lid hit my chest"]
NEG += ["i ate a little bit of toast", "my toddler ate a small piece of apple", "i ate onion rings for dinner", "the baby ate a small piece of cake", "i ate a tiny bit of chicken"]
NEG += ["i chewed my nails again", "she bites her nails when she is nervous", "he was chewing his fingernails", "i cut my toenails"]
NEG += ["my chest is fine but my knee hurts", "my chest is fine but my back hurts", "chest is clear though my head is pounding and my arm aches", "my eyes are fine but my hand is a bit double jointed"]
NEG += ["i lost sight of my keys", "i lost sight of him in the crowd", "we lost sight of the kids at the beach", "she is losing sight of her goals"]
NEG += ["everything is double the price now", "everything costs double what it did", "everything looks double the size in this mirror shop", "i cant see right now, someone turned the light off",
        "i cant see well enough to read the menu from here", "everything is blurry about last night"]
NEG += ["i feel a bit tired", "my back is sore", "my knee hurts", "my tummy hurts a bit", "i have a sore throat", "my ear aches"]
NEG += ["tell me a joke", "what is the weather like", "play some music", "add milk to the list"]

PET_NEG_VET = [f"{p} {v} {o}" for p, v, o in _every(list(itertools.product(PETS, PET_V, PET_O)), 4)]

# -- held-out, hand-written; REPORTED, not asserted ---------------------------------------------------------------------------------
HELD_OUT = [("my chest feels kind of heavy and weird", ("red_call", "heart")), ("theres a weight sitting on my chest", ("red_call", "heart")),
            ("my chest is sore after coughing all week", ("", "")), ("the room is spinning and my sight has gone grey", ("", "")),
            ("my kid swallowed a lolly", ("", "")), ("she put a coin in her mouth and went quiet", ("ingest_human", "object_minor")),
            ("he swallowed a fish hook", ("", "")), ("my hand has gone limp and my mouth is crooked", ("red_call", "stroke"))]


def _verdict(text):
    v = ms.detect(text)
    return (v.cls, v.key) if v.cls else None


def _matches(got, want):
    if got is None:
        return False
    cls, key = want
    return got[0] == cls and (not key or got[1] == key)


def test_paraphrase_set_is_big_enough():
    assert len(_positives()) >= 100
    assert len(NEG) >= 100


def test_generated_paraphrases_precision_and_recall(capsys):
    pos = _positives()
    per = defaultdict(lambda: [0, 0])
    misses, tp = [], 0
    for cat, text, want in pos:
        per[cat][1] += 1
        if _matches(_verdict(text), want):
            per[cat][0] += 1
            tp += 1
        else:
            misses.append((cat, text, _verdict(text)))
    fp = [(t, _verdict(t)) for t in NEG if _verdict(t)]
    wrong_human = [(t, _verdict(t)) for t in PET_NEG_VET if (_verdict(t) or ("", ""))[0] != "ingest_animal"]
    wrong_class = [m for m in misses if m[2] is not None]   # a positive answered with the WRONG class is also a false positive of that class
    precision = tp / (tp + len(fp) + len(wrong_human) + len(wrong_class)) if tp else 0.0
    recall = tp / len(pos)
    held = [(t, _verdict(t), w) for t, w in HELD_OUT]
    with capsys.disabled():
        print("\n== medical safety paraphrase report ==")
        for cat, (ok, n) in sorted(per.items()):
            print(f"  recall {ok:3d}/{n:<3d} {ok / n:6.1%}  {cat}")
        print(f"  positives {len(pos)}  true-positive {tp}  RECALL {recall:.1%}")
        print(f"  negatives {len(NEG)} (+{len(PET_NEG_VET)} pet rows)  false-positive {len(fp)}  pet-misrouted {len(wrong_human)}  PRECISION {precision:.1%}")
        print("  held-out (not asserted; what is left to the brain):")
        for t, got, w in held:
            ok = "ok  " if (got is None and not w[0]) or (got and _matches(got, w)) else "MISS"
            print(f"    {ok} {t!r} -> {got}")
    assert not misses, misses
    assert not fp, fp
    assert not wrong_human, wrong_human


def test_swallowed_objects_get_the_right_reply(monkeypatch):
    monkeypatch.setenv("ZOE_HOUSEHOLD_COUNTRY", "AU")
    for text in ["wren swallowed a magnet", "max swallowed a button battery"]:
        out = ms.build_reply(ms.detect(text))
        assert out.startswith("Ring 000 now") and "13 11 26" in out
    for text in ["max just swallowed a lego bit", "zara just swallowed a coin", "my kid just swallowed a coin"]:
        out = ms.build_reply(ms.detect(text))
        assert "13 11 26" in out and "000" in out and "choking" in out and "drooling" in out and "watch" in out
        assert not out.startswith("Ring 000 now.")      # not an unconditional ambulance: the number is for choking / pain / breathing
        assert "will pass" not in out and "harmless" not in out and "diagnos" not in out


@pytest.mark.parametrize("text", ["my son swallowed a coin and is drooling", "my son swallowed a coin and has stomach pain", "zara swallowed a lego bit and cant swallow",
                                  "max swallowed a bead and is gagging", "wren swallowed a coin and is vomiting"])
def test_a_small_object_with_a_stated_sign_is_the_plain_ambulance_answer(text):
    assert _verdict(text) == ("red_call", "object_signs")
    assert ms.build_reply(ms.detect(text)).startswith("Ring 000 now.")


@pytest.mark.parametrize("text", ["my son swallowed a coin, no pain", "my son swallowed a coin and is not drooling", "my dog swallowed a coin and is drooling"])
def test_negative_control_denied_or_pet_signs_do_not_flip(text):
    assert _verdict(text) in {("ingest_human", "object_minor"), ("ingest_animal", "poison_animal")}


@pytest.mark.parametrize("text", ["my son accidentally took a button battery", "my toddler has taken a battery by mistake", "she took a magnet by accident"])
def test_took_by_accident_still_catches_a_battery_or_magnet(text):
    assert _verdict(text) == ("ingest_human", "object_urgent")


@pytest.mark.parametrize("text", ["my child swallowed a coin, what signs of pain or drooling should i watch for?", "my child swallowed a coin. my back has pain",
                                  "wren swallowed a coin, should i worry if there is vomiting"])
def test_negative_control_a_question_or_unrelated_symptom_is_not_a_stated_sign(text):
    assert _verdict(text) == ("ingest_human", "object_minor")


def test_a_stated_sign_still_counts_when_the_member_also_asks_for_help():
    assert _verdict("my son swallowed a coin and is drooling, what should i do?") == ("red_call", "object_signs")
    assert _verdict("wren swallowed a bead and has pain, what do i do") == ("red_call", "object_signs")


def test_a_metal_nail_is_urgent_but_nail_biting_is_not():
    assert _verdict("my toddler swallowed a nail") == ("ingest_human", "object_urgent")


def test_the_most_urgent_ingestion_across_the_turn_wins():
    assert _verdict("my child swallowed a coin. she also swallowed a battery") == ("ingest_human", "object_urgent")
    assert _verdict("my child swallowed a bead. she also drank some bleach") == ("ingest_human", "poison_human")


def test_the_same_child_named_again_still_reports_the_sign_but_another_person_does_not():
    assert _verdict("my child swallowed a coin. my child is drooling") == ("red_call", "object_signs")
    assert _verdict("my child swallowed a coin. my mum has pain") == ("ingest_human", "object_minor")


def test_a_sign_in_the_next_sentence_about_the_same_child_counts():
    assert _verdict("my child swallowed a coin. now he is drooling") == ("red_call", "object_signs")


def test_a_coin_with_choking_is_the_ambulance_answer():
    assert _verdict("my son swallowed a coin and is choking") == ("red_call", "breathing")
    assert _verdict("zara swallowed a lego bit and cant breathe") == ("red_call", "breathing")


def test_the_original_qa7_turns():
    assert _verdict("got a tight feeling in my chest, been going an hour") == ("red_call", "heart")
    assert _verdict("bad headache for 3 days and now my eyes are blurry") == ("red_soon", "vision")


def test_negation_still_wins_on_paraphrases():
    assert _verdict("i have no tightness in my chest") is None
    assert _verdict("no pressure in my chest at all") is None
    assert _verdict("my eyes are not blurry") is None
