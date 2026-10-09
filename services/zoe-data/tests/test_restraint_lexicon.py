"""Restraint's WORDS are data (``lexicons_data/<lang>.json`` "restraint"), not English literals in ``restraint.py``.

Prerequisite (c) of ``docs/knowledge/person-half-guards.md`` / open-problems "Restraint": the classifier word list was
English-only (20 of 32 held-out sentences classed before widening) and a Spanish row about a diagnosis went out unclassed.

What this file proves, each with a break-the-fix twin:

* English is the pre-move word lists, byte for byte (``fixtures/restraint_pre_move_en.json``, captured from origin/main
  @ 9810a2da before the move): edit one word in ``en.json`` and the identity test is red.
* a FRESH held-out set (50+ sentences, written before it was first run) is measured, not assumed.
* es / fr / de / zh / ja each class their health / money / grief / conflict / feeling sentences, read their own
  "what's up?" and their own "don't bring that up", speak the acknowledgement in the owner's language, and leave a plain
  sentence alone. Take the language's entry away and every one of those goes red (the words are in the data).
* no English word list is left in the code.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

import lexicons
import restraint
import restraint_lex

FIX = json.loads((Path(__file__).parent / "fixtures" / "restraint_pre_move_en.json").read_text(encoding="utf-8"))
EN = restraint_lex.pack("en")


# ── English identity ────────────────────────────────────────────────────────────────────────────────────────────────
#: the one pre-move fragment that was NARROWED in the widening: "broke" also matched "she broke her arm" (a bone, not money)
NARROWED = {"broke": "(?:i'?m|we'?re|am|are|totally|completely|flat|stony|dead|going|gone|went) broke"}


@pytest.mark.parametrize("key", sorted(FIX["lists"]))
def test_the_english_lists_only_grew_every_pre_move_fragment_is_still_there_in_order(key):
    old = [NARROWED.get(w, w) for w in FIX["lists"][key]]
    lex = lexicons.load("en")["restraint"][key]
    assert lex[:len(old)] == old, key


def test_the_pre_move_english_patterns_still_match_what_they_matched():
    """The widening adds; it never takes a match away (bar the one NARROWED fragment, pinned below)."""
    for attr, old in (("health", "_HEALTH"), ("money", "_MONEY"), ("grief", "_GRIEF"), ("affect", "_AFFECT")):
        was, now = re.compile(FIX[old]), getattr(EN, attr)
        for s in CORPUS + [t for _w, t in FRESH_SENSITIVE + HELD2_SENSITIVE] + FRESH_PLAIN + HELD2_PLAIN:
            if attr == "money" and " broke " in s.lower():
                continue                         # the one NARROWED fragment
            if was.search(s.lower()):
                assert now.search(s.lower()), (attr, s)


def test_broke_the_adjective_is_money_and_a_broken_bone_is_not():
    assert "money" in restraint.classify("honestly I'm broke until Friday")
    assert "money" not in restraint.classify("my niece broke her arm at gymnastics")
    assert "health" in restraint.classify("my niece broke her arm at gymnastics")


@pytest.mark.parametrize("name, old", [("neg", "_MUTE_NEG"), ("want", "_MUTE_WANT"), ("enough", "_MUTE_ENOUGH"),
                                       ("leave", "_MUTE_LEAVE"), ("leave_bare", "_MUTE_LEAVE_BARE"),
                                       ("lets", "_MUTE_LETS"), ("release", "_REL"), ("filler_lead", "_FILLER_LEAD"),
                                       ("modal_lead", "_MODAL_LEAD")])
def test_the_english_mute_grammar_is_the_pre_move_grammar(name, old):
    assert EN.mute[name].pattern == FIX[old]


def test_the_english_word_sets_are_the_pre_move_sets():
    assert sorted(EN.stop) == FIX["_STOP"] and EN.kin_canon == FIX["_KIN_CANON"]
    assert sorted(EN.pull_leads) == FIX["_LEADS"] and sorted(EN.pull_phrases) == FIX["_PULL_PHRASES"]
    assert sorted(EN.pull_tail) == FIX["_PULL_TAIL"] and sorted(EN.obj_drop) == FIX["_OBJ_DROP"]
    assert sorted(EN.person_obj) == FIX["_PERSON_OBJ"] and sorted(EN.not_a_name) == FIX["_NOT_A_NAME"]
    assert EN.name_poss.pattern == FIX["_NAME_POSS"]


CORPUS = [
    "I had a big fight with my sister last night", "Mum and I haven't spoken since the wedding", "we are getting a divorce",
    "my mum Ingrid is recovering from a hip replacement", "Dana's knee surgery is on Monday", "missing my grandfather",
    "his daughter's wedding", "user's brother lives in Perth", "The kids screamed at each other all weekend",
    "the cat is on the sofa", "Teodor got suspended from school", "ex-partner", "the family BBQ is on Sunday",
]


@pytest.mark.parametrize("attr, old", [("family_conflict", "_FAMILY_CONFLICT"), ("other_kin", "_OTHER_KIN"),
                                       ("kin_poss", "_KIN_POSS")])
def test_the_composed_english_patterns_behave_as_the_pre_move_ones(attr, old):
    was, now = re.compile(FIX[old]), getattr(EN, attr)
    for s in CORPUS:
        assert bool(was.search(s.lower())) == bool(now.search(s.lower())), (attr, s)


def test_break_the_fix_one_word_dropped_from_the_english_entry_is_caught(monkeypatch):
    real = lexicons.load("en")
    cut = json.loads(json.dumps(real))
    cut["restraint"]["health"] = [w for w in cut["restraint"]["health"] if w != "dentist"]
    monkeypatch.setattr(lexicons, "load", lambda lang: cut if str(lang).startswith("en") else real)
    restraint_lex.pack.cache_clear()
    try:
        assert restraint_lex.pack("en").health.pattern != FIX["_HEALTH"]
        assert "health" not in restraint.classify("the dentist is on Friday")
    finally:
        monkeypatch.undo()
        restraint_lex.pack.cache_clear()
    assert "health" in restraint.classify("the dentist is on Friday")


# ── a FRESH held-out set (written before its first run; English) ───────────────────────────────────────────────────────
FRESH_SENSITIVE = [
    ("health", "I've had a bad cold all week and can't shake the cough"),
    ("health", "The cardiologist wants to check my heart again in March"),
    ("health", "User's back has been playing up since the gym session"),
    ("health", "I was diagnosed with type 2 last year"),
    ("health", "Need to refill my blood pressure tablets"),
    ("health", "The paediatrician says Mia's ear infection is clearing"),
    ("health", "I had a bad reaction to the new antihistamine"),
    ("health", "My dermatologist appointment is on Wednesday"),
    ("health", "I haven't been sleeping well, maybe two hours a night"),
    ("health", "User is recovering from knee surgery"),
    ("money", "We're drowning in credit card debt"),
    ("money", "I got a big tax bill from the accountant"),
    ("money", "User can't pay the electricity bill this month"),
    ("money", "The bank rejected our home loan application"),
    ("money", "I lost my job at the warehouse in August"),
    ("money", "Our savings are almost gone"),
    ("money", "User is stressed about the car repayments"),
    ("money", "I'm skint until payday"),
    ("money", "We owe the school three terms of fees"),
    ("money", "The insurance premium doubled this year"),
    ("grief", "My grandmother passed last winter and I still miss her"),
    ("grief", "We buried my uncle on Saturday"),
    ("grief", "User's husband died two years ago"),
    ("grief", "I'm going to the cemetery on Mum's birthday"),
    ("grief", "It's been a year since we lost Biscuit"),
    ("grief", "The funeral is at 11 on Thursday"),
    ("grief", "She never got over the stillbirth"),
    ("grief", "Dad's memorial service is next month"),
    ("grief", "I'm still mourning my best friend"),
    ("grief", "The wake was held at the pub"),
    ("family_conflict", "My son and I had a huge argument over his grades"),
    ("family_conflict", "My sister hasn't talked to me since the wedding"),
    ("family_conflict", "User and her mother fell out over Christmas"),
    ("family_conflict", "My parents are splitting up"),
    ("family_conflict", "My husband and I are fighting constantly"),
    ("family_conflict", "The kids screamed at each other all weekend"),
    ("family_conflict", "I'm furious with my brother about the loan"),
    ("family_conflict", "Things are really tense between my mum and my aunt"),
    ("family_conflict", "He walked out on the family in June"),
    ("family_conflict", "She cut her dad off after the funeral"),
    ("other_member", "Hamish has a hospital appointment on Monday"),
    ("other_member", "My boss Stephanie is leaving the company"),
    ("other_member", "User's flatmate Callum is moving out"),
    ("other_member", "Priya's driving test is on Tuesday"),
    ("other_member", "Our neighbour Wilfred fell and broke his hip"),
    ("other_member", "Isla has been struggling at school"),
    ("other_member", "Tomasz's visa was refused"),
    ("other_member", "My cousin is getting married in Perth"),
    ("other_member", "Her partner works for the council"),
    ("other_member", "Alejandra's mum is visiting next week"),
    ("affect", "I feel so overwhelmed with everything at work"),
    ("affect", "User is nervous about the presentation"),
    ("affect", "I've been really lonely since I moved"),
    ("affect", "I can't stop worrying about the exam"),
    ("affect", "Honestly I'm terrified of flying on Friday"),
    ("affect", "User felt humiliated in the meeting"),
    ("affect", "I'm dreading Sunday dinner"),
    ("affect", "I've been feeling flat all week"),
    ("affect", "User is anxious about the scan results"),
    ("affect", "I feel guilty about missing her party"),
]
FRESH_PLAIN = [
    "Remind me to buy oat milk", "The meeting is moved to 3pm on Thursday", "User is learning to bake sourdough",
    "I booked flights to Auckland for April", "User's favourite film is Arrival", "Can you add tomatoes to the shopping list",
    "User takes the 7:40 train to the city", "We're painting the spare room blue", "User plays chess online most evenings",
    "The garden needs mulching before summer", "I'm reading a book about Roman roads", "User prefers window seats on planes",
    "Our wifi router needs a reboot", "The dog's collar is on the hook by the door", "User has a standing desk at the office",
    "I'm making risotto for dinner tonight", "The football final is on Saturday night", "User collects vintage postcards",
    "Print the boarding pass before we leave", "I want a haircut on Friday afternoon", "User's car is due for a service in May",
    "We're hosting a barbecue for the team on the 20th", "Turn the heating down to 19 at night",
    "User listens to jazz while cooking", "I'd like to learn the ukulele this year", "The library book is due back on Monday",
    "User walks to the market on Sunday mornings", "I need new running shoes before the 10k",
    "The conference talk is about distributed systems", "User keeps a spare key under the pot plant",
    "Our team won the quiz night",
]


# HELD-OUT SET 2: written AFTER the vocabulary was widened from set 1's misses (set 1 is training data now), before its first
# run: 40 / 60 class-exact (67 %), 46 / 60 any class, 0 / 32 plain false positives.
HELD2_SENSITIVE = [
    ("health", "The oncologist is happy with how the treatment is going"),
    ("health", "I've got a stomach bug and can't keep anything down"),
    ("health", "User has been getting dizzy spells when standing up"),
    ("health", "My eyes have been really sore from the screen"),
    ("health", "The ENT wants me to have my tonsils removed"),
    ("health", "I need a repeat prescription before the weekend"),
    ("health", "User broke their wrist skiing in July"),
    ("health", "My asthma flares up when it's cold"),
    ("health", "The ultrasound showed everything is normal"),
    ("health", "User snores badly and might have sleep apnoea"),
    ("money", "We're two months behind on the mortgage"),
    ("money", "I can't make the minimum payments on my cards"),
    ("money", "User got a speeding fine and can't afford it"),
    ("money", "My landlord is putting the rent up by two hundred a month"),
    ("money", "The business is losing money and I might have to close"),
    ("money", "User owes the tax office about nine thousand dollars"),
    ("money", "We have no emergency fund at all"),
    ("money", "I got knocked back for a personal loan"),
    ("money", "User's wages haven't been paid for three weeks"),
    ("money", "The car repair bill wiped out our savings"),
    ("grief", "It's been six months since my father died"),
    ("grief", "We're scattering Nana's ashes this weekend"),
    ("grief", "User lost their brother in a car accident"),
    ("grief", "I keep thinking about the day we put our old dog down"),
    ("grief", "My mother-in-law's funeral is on Friday"),
    ("grief", "User is grieving after the loss of a close friend"),
    ("grief", "She passed away peacefully in her sleep"),
    ("grief", "I visit his grave every Sunday"),
    ("grief", "The anniversary of Pop's death is coming up"),
    ("grief", "User's wife died of cancer last year"),
    ("family_conflict", "My dad and I got into a shouting match at dinner"),
    ("family_conflict", "User's brother hasn't spoken to them in months"),
    ("family_conflict", "Mum and my sister are not on speaking terms"),
    ("family_conflict", "We had a terrible row about who gets the house"),
    ("family_conflict", "My husband wants a divorce"),
    ("family_conflict", "The kids have been fighting non-stop"),
    ("family_conflict", "User is estranged from their father"),
    ("family_conflict", "My in-laws keep criticising how I raise the children"),
    ("family_conflict", "My daughter slammed the door and said she hates me"),
    ("family_conflict", "User and their partner are on a break"),
    ("other_member", "My neighbour's husband just lost his job"),
    ("other_member", "User's housemate is having a hard time at work"),
    ("other_member", "My brother-in-law is getting out of hospital"),
    ("other_member", "Her ex keeps texting her"),
    ("other_member", "My best friend is going through chemo"),
    ("other_member", "User's grandson started school this week"),
    ("other_member", "My niece broke her arm at gymnastics"),
    ("other_member", "Our daughter's teacher called about her behaviour"),
    ("other_member", "User's colleague is on stress leave"),
    ("other_member", "My uncle was arrested last night"),
    ("affect", "I'm so embarrassed about what I said at the party"),
    ("affect", "User feels trapped in their job"),
    ("affect", "I feel like a failure lately"),
    ("affect", "User is panicking about the deadline"),
    ("affect", "I've been crying a lot this week"),
    ("affect", "I'm really worried about my results"),
    ("affect", "User is feeling stressed and exhausted"),
    ("affect", "I'm so angry I can't think straight"),
    ("affect", "I don't feel like myself these days"),
    ("affect", "User is scared of being alone at night"),
]
HELD2_PLAIN = [
    "Add milk and bread to the list", "The plumber is coming at nine on Tuesday", "User is learning to play the cello",
    "I've booked a table for six on Friday", "User's flight lands at 6:15 in the morning", "Can you set an alarm for 5:30",
    "We're repainting the fence this weekend", "User usually runs 5k before work", "The new phone arrives next Wednesday",
    "I'm thinking of getting a puppy next year", "User prefers audiobooks on long drives", "The car park by the station is free after six",
    "I need to renew my library card", "User likes spicy food", "Water the ferns on Thursday",
    "The kids' school concert is on the 12th", "User is saving up for a bike", "We're having pasta for dinner",
    "Book a table at the Thai place", "User drinks green tea in the afternoon", "The meeting notes are in the shared folder",
    "I watched a documentary about octopuses", "User's office is on the fourth floor", "The grocery delivery comes between ten and twelve",
    "Please text me when the parcel arrives", "I'd like to go hiking in the hills on Sunday", "User takes the bus on rainy days",
    "Our team lunch is at the Italian place", "User practices guitar for half an hour each night", "The tennis court is booked for 4pm",
    "My laptop battery is dying quickly", "User bought a new jacket for winter",
]


# HELD-OUT SET 3: written after the vocabulary was FINAL (set 2 is training data too, once its misses were read), before its
# first run. This is THE measurement; sets 1 and 2 are the regression floor. The ledger's bar was ">= 90 % class-exact with 0
# plain false positives": a hand-built word list does NOT reach it (63 %); the structured signals (type / entity / tags /
# captured affect), the night mind's stage-2 kind and the contact names carry the rest. 0 plain false positives holds.
HELD3_SENSITIVE = [
    ("health", "The GP thinks my headaches might be stress-related"),
    ("health", "User twisted their ankle on the stairs yesterday"),
    ("health", "I'm on antidepressants and the dose is being changed"),
    ("health", "She has to wear a brace on her knee for six weeks"),
    ("health", "User gets chest pains when climbing hills"),
    ("health", "My blood sugar was high at the last check"),
    ("health", "The baby has had a fever since Tuesday"),
    ("health", "I'm going for a colonoscopy next week"),
    ("health", "User had a mole removed from their back"),
    ("health", "I keep getting these awful migraines with auras"),
    ("money", "The rent went up again and we're really struggling"),
    ("money", "I owe the ATO a lot and I'm on a payment plan"),
    ("money", "User is worried the redundancy payout won't last"),
    ("money", "We maxed out the credit card on the holiday"),
    ("money", "The mortgage rate is going to jump next month"),
    ("money", "I can't afford to fix the roof this year"),
    ("money", "User is in debt to their brother"),
    ("money", "We've been living off our savings since March"),
    ("money", "The bank is chasing us for the overdraft"),
    ("money", "I'm only just keeping up with the bills"),
    ("grief", "Today would have been my mum's seventieth birthday"),
    ("grief", "User attended their uncle's funeral on Monday"),
    ("grief", "I lost my best mate to cancer in the spring"),
    ("grief", "We had to say goodbye to our old labrador last week"),
    ("grief", "User's father passed away when they were twelve"),
    ("grief", "I still can't believe she's gone"),
    ("grief", "The service for Grandpa is at the church on Saturday"),
    ("grief", "User lights a candle on the anniversary of the accident"),
    ("grief", "She's been in mourning since January"),
    ("grief", "I found my dad's old letters and cried"),
    ("family_conflict", "My mother and I had a bitter argument about the will"),
    ("family_conflict", "User's sons are barely speaking to each other"),
    ("family_conflict", "Me and my wife had a massive fight last night"),
    ("family_conflict", "My sister blames me for what happened with Dad"),
    ("family_conflict", "User is thinking about leaving their husband"),
    ("family_conflict", "My parents argue constantly and it's exhausting"),
    ("family_conflict", "The relationship with my stepdad is really strained"),
    ("family_conflict", "My brother won't come to the wedding because of the row"),
    ("other_member", "My sister is pregnant with her second"),
    ("other_member", "User's mum is moving into a care home"),
    ("other_member", "My husband starts his new job on Monday"),
    ("other_member", "Our son has football training on Thursdays"),
    ("other_member", "User's girlfriend is visiting from Melbourne"),
    ("other_member", "My dad is having his hip replaced"),
    ("other_member", "User's best mate is moving to Canada"),
    ("other_member", "My wife's mother is staying for two weeks"),
    ("affect", "I'm feeling really down about the whole situation"),
    ("affect", "User is terrified of the biopsy"),
    ("affect", "I feel incredibly lonely in this city"),
    ("affect", "User is ashamed of how they reacted"),
    ("affect", "I'm so anxious I can barely eat"),
    ("affect", "User felt rejected after the call"),
    ("affect", "I've been feeling hopeless about the job search"),
    ("affect", "User is overwhelmed by the paperwork"),
]
HELD3_PLAIN = [
    "Set a timer for ten minutes", "The bins go out on Wednesday night", "User likes strong black coffee in the morning",
    "I've signed up for the pottery class", "User's favourite team is Fremantle", "What's the weather like on Saturday",
    "We're replacing the old couch next month", "User is reading a thriller at the moment", "The dishwasher needs descaling",
    "Call the electrician about the new socket", "User cycles to work when it's dry", "I'm learning to say hello in Italian",
    "The school holidays start on the 3rd", "User keeps a vegetable garden out the back", "Pick up flowers on the way home",
    "The tickets for the gig are on my phone", "User's birthday is in late June", "I'd like a recipe for lamb curry",
    "Reschedule the team meeting to Friday", "User plays squash on Wednesdays", "We're driving to Albany for the long weekend",
    "Put the lasagne in at 6", "User is subscribed to a few podcasts", "The printer is out of ink again",
    "User wants to repaint the shed",
]


def test_held_out_set_3_is_the_measurement():
    exact = [w for w, t in HELD3_SENSITIVE if w in restraint.classify(t)]
    anyc = [t for _w, t in HELD3_SENSITIVE if restraint.classify(t)]
    false_pos = [t for t in HELD3_PLAIN if restraint.classify(t)]
    assert false_pos == []
    assert len(exact) >= HELD3_EXACT and len(anyc) >= HELD3_ANY, (len(exact), len(anyc), len(HELD3_SENSITIVE))


HELD3_EXACT, HELD3_ANY = 34, 41   # THE blind measurement: 34 / 54 class-exact (63 %), 41 / 54 any class (76 %), 0 / 25 plain false positives


def test_the_second_set_is_the_regression_floor_and_nothing_plain_is_withheld():
    exact = [w for w, t in HELD2_SENSITIVE if w in restraint.classify(t)]
    false_pos = [t for t in HELD2_PLAIN if restraint.classify(t)]
    assert false_pos == []                                    # 0 plain false positives
    assert len(exact) / len(HELD2_SENSITIVE) >= HELD2_FLOOR, (len(exact), len(HELD2_SENSITIVE))


HELD2_FLOOR = 59 / 60   # 40 / 60 class-exact on its first run; the vocabulary was then widened from its misses (training data now)


def test_the_first_fresh_set_is_the_regression_floor():
    """Set 1 (written before its first run: 35 / 60 class-exact then). A floor now, not a measurement."""
    exact = [w for w, t in FRESH_SENSITIVE if w in restraint.classify(t)]
    anyc = [t for _w, t in FRESH_SENSITIVE if restraint.classify(t)]
    false_pos = [t for t in FRESH_PLAIN if restraint.classify(t)]
    assert false_pos == []
    assert len(exact) >= MIN_EXACT and len(anyc) >= MIN_ANY, (len(exact), len(anyc), len(FRESH_SENSITIVE))


MIN_EXACT, MIN_ANY = 56, 58   # set 1 after the first widening (57 / 59 before the over-matching words were removed again)


# ── the other five languages (author-written, reviewed: false) ──────────────────────────────────────────────────────────
LABELLED = {
    "es": [("health", "Mi madre tiene cita con el dentista el viernes"), ("money", "Debo mucho dinero del préstamo"),
           ("grief", "Mi abuelo falleció el mes pasado"), ("family_conflict", "Mi hermana y yo tuvimos una pelea horrible"),
           ("affect", "Me siento muy ansioso por la entrevista"), ("other_member", "Mi hermana vive en Valencia")],
    "fr": [("health", "Ma mère a rendez-vous chez le dentiste vendredi"), ("money", "Le loyer est trop cher et j'ai des dettes"),
           ("grief", "Mon grand-père est décédé le mois dernier"), ("family_conflict", "Ma sœur et moi nous sommes disputées hier"),
           ("affect", "Je me sens très anxieux pour l'entretien"), ("other_member", "Mon frère habite à Lyon")],
    "de": [("health", "Meine Mutter hat am Freitag einen Termin beim Zahnarzt"), ("money", "Ich habe Schulden und die Miete ist zu hoch"),
           ("grief", "Mein Opa ist letzten Monat gestorben"), ("family_conflict", "Meine Schwester und ich hatten einen furchtbaren Streit"),
           ("affect", "Ich bin sehr nervös wegen des Gesprächs"), ("other_member", "Mein Bruder wohnt in Hamburg")],
    "zh": [("health", "我妈妈周五要去看牙医"), ("money", "我欠了很多房贷"), ("grief", "我爷爷上个月去世了"),
           ("family_conflict", "我姐姐和我吵架了"), ("affect", "我对面试很焦虑"), ("other_member", "我哥哥住在上海")],
    "ja": [("health", "お母さんは金曜日に歯医者に行きます"), ("money", "住宅ローンの借金が心配です"), ("grief", "祖父が先月亡くなりました"),
           ("family_conflict", "姉と喧嘩しました"), ("affect", "面接が不安です"), ("other_member", "お母さんは大阪に住んでいます")],
}
PLAIN = {
    "es": ["Quiero una pizza esta noche con mis amigos", "El viernes tengo yoga a las seis y luego la compra"],
    "fr": ["Je veux cuisiner du poisson ce soir avec mes amis", "Vendredi j'ai yoga à six heures puis les courses"],
    "de": ["Heute Abend möchte ich Fisch kochen und danach einen Film schauen", "Am Freitag habe ich Yoga und danach einkaufen"],
    "zh": ["今晚我想做鱼", "周五六点有瑜伽课"],
    "ja": ["今夜は魚を料理したい", "金曜日の六時にヨガがあります"],
}
PULL = {"es": "¿Qué tal?", "fr": "Quoi de neuf ?", "de": "Was gibt's Neues?", "zh": "最近怎么样？", "ja": "最近どう？"}
GREETING = {"es": "Hola Zoe", "fr": "Bonjour Zoe", "de": "Hallo Zoe", "zh": "你好", "ja": "こんにちは"}
MUTE = {"es": "No menciones más al dentista", "fr": "Ne parle plus du dentiste", "de": "Sprich nicht mehr über meinen Bruder",
        "zh": "不要再提牙医", "ja": "歯医者の話はもうしないで"}
RELEASE = {"es": "Ya puedes volver a mencionar al dentista", "fr": "Tu peux parler du dentiste de nouveau",
           "de": "Du kannst wieder über den Zahnarzt reden", "zh": "你可以再提牙医了", "ja": "歯医者の話をまた話してもいいよ"}
ACK = {"es": "Vale, no volveré a sacar ese tema.", "fr": "D'accord, je n'en reparlerai plus.",
       "de": "In Ordnung, ich spreche das nicht mehr an.", "zh": "好的，我不会再提这件事了。", "ja": "わかりました。もうその話はしません。"}


@pytest.mark.parametrize("lang", sorted(LABELLED))
def test_each_language_classes_its_own_sentences(lang):
    for want, text in LABELLED[lang]:
        assert restraint_lex.lang_of(text) == lang, text
        assert want in restraint.classify(text), (lang, want, text)
    for text in PLAIN[lang]:
        assert restraint.classify(text) == (), (lang, text)


@pytest.mark.parametrize("lang", sorted(PULL))
def test_each_language_reads_its_own_whats_up_and_not_a_greeting(lang):
    assert restraint.is_pull(PULL[lang]), lang
    assert not restraint.is_pull(GREETING[lang]), lang


@pytest.mark.parametrize("lang", sorted(MUTE))
def test_each_language_reads_its_own_mute_and_release(lang):
    u = restraint.parse_utterance(MUTE[lang])
    assert u is not None and u.kind == "mute", (lang, u)
    r = restraint.parse_utterance(RELEASE[lang])
    assert r is not None and r.kind == "release", (lang, r)
    assert restraint.parse_utterance(PLAIN[lang][0]) is None


@pytest.mark.parametrize("lang", sorted(ACK))
def test_the_acknowledgement_is_spoken_in_the_owners_language(lang):
    assert restraint_lex.ack("ack_mute", MUTE[lang]) == ACK[lang]
    assert ACK[lang] != restraint.ACK_MUTE


def test_a_language_with_no_entry_adds_nothing_and_is_never_guessed_from_english(monkeypatch):
    real = lexicons.load
    monkeypatch.setattr(lexicons, "load", lambda lang: {k: v for k, v in real(lang).items() if k != "restraint"})
    restraint_lex.pack.cache_clear()
    try:
        assert restraint.classify("the dentist is on Friday") == ()      # English too: the words are in the data
        assert restraint.parse_utterance("don't mention that again") is None
        assert not restraint.is_pull("what's up?")
        assert restraint_lex.ack("ack_mute", "xx") == ""
    finally:
        monkeypatch.undo()
        restraint_lex.pack.cache_clear()
    assert restraint.classify("the dentist is on Friday") == ("health",)


@pytest.mark.parametrize("lang", sorted(LABELLED))
def test_break_the_fix_without_the_languages_entry_its_sentences_are_not_classed(lang, monkeypatch):
    real = lexicons.load
    monkeypatch.setattr(lexicons, "load",
                        lambda code: {k: v for k, v in real(code).items() if k != "restraint"} if str(code).startswith(lang) else real(code))
    restraint_lex.pack.cache_clear()
    try:
        classed = [t for _w, t in LABELLED[lang] if restraint.classify(t) and "other_member" not in restraint.classify(t)]
        assert classed == []
    finally:
        monkeypatch.undo()
        restraint_lex.pack.cache_clear()


def test_structured_signals_decide_without_any_language():
    assert "health" in restraint.classify("???", tags="medical")
    assert "other_member" in restraint.classify("xyz", entity_type="person")


def test_the_cjk_topic_is_the_character_bigrams_minus_particles():
    assert restraint.stems("歯医者の話") == frozenset({"歯医", "医者"})
    assert restraint.stems("我的牙医") == frozenset({"牙医"})


def test_the_accented_text_hash_is_stable_and_ascii_text_is_unchanged():
    assert restraint.text_hash("Dentist, Friday!") == restraint.text_hash("dentist friday")
    assert restraint._norm("Don't mention Dana's _thing_") == "dont mention danas thing"
    assert restraint._norm("¿Qué tal?") == "qué tal"


# ── no English word list is left in the code ───────────────────────────────────────────────────────────────────────────
def test_no_english_word_list_is_left_in_the_code():
    src = (Path(restraint.__file__).read_text(encoding="utf-8") + Path(restraint_lex.__file__).read_text(encoding="utf-8"))
    # code (not a comment / docstring example): strip the comments and the docstrings' prose before looking
    code = re.sub(r'"""[\s\S]*?"""', "", src)
    code = "\n".join(re.sub(r"\s#.*$", "", ln) for ln in code.splitlines() if not ln.strip().startswith("#"))
    for word in ("migraine", "bankrupt", "funeral", "quarrel", "estrang", "overdraft", "mortgage", "stillborn", "heartbroken",
                 "whats up", "how are things", "bring up", "mention", "leave it", "Okay, I won"):
        assert word not in code, word


# ═════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════
# prerequisite (b): the always-present user-model card is filtered in enforce (money / grief / family trouble; a Health line that is
# not safety information)
# ═════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════
import contextlib
import json as _json

import user_model_card as umc

CARD = {"name": "Sam", "style": "", "items": [
    ["diet", "pescatarian", "m-diet"],
    ["health", "allergic to peanuts, carries an EpiPen", "m-allergy"],
    ["health", "takes medication for asthma", "m-asthma"],
    ["health", "chronic back pain since the surgery", "m-back"],
    ["health", "migraines most afternoons", "m-migraine"],
    ["work", "works night shifts", "m-work"],
    ["current", "saving for a trip to Lisbon (noted 3 Oct)", "m-trip"],
    ["people", "mother Ingrid, passed away in June", "m-mum"],
    ["current", "behind on the mortgage this month", "m-mortgage"],
    ["people", "sister Dana, they fell out over the will", "m-dana"],
    ["people", "sister Marisol, lives in Lisbon", "m-sis"],
]}


def test_the_card_withhold_rule_per_item():
    w = restraint.card_withheld
    assert w("diet", "pescatarian") == ()
    assert w("health", "allergic to peanuts, carries an EpiPen") == ()           # safety information stays
    assert w("health", "takes medication for asthma") == ()
    assert w("health", "chronic back pain since the surgery") == ("health",)    # not safety: waits for a pull
    assert w("health", "migraines most afternoons") == ("health",)
    assert w("people", "mother Ingrid, passed away in June") == ("grief",)
    assert w("current", "behind on the mortgage this month") == ("money",)
    assert "family_conflict" in w("people", "sister Dana, they fell out over the will")
    assert w("people", "sister Marisol, lives in Lisbon") == ()                  # people are the point of that line
    assert w("work", "works night shifts") == ()


def test_the_card_filter_is_enforce_only_and_shadow_only_logs(monkeypatch, caplog):
    import logging

    items = CARD["items"]
    monkeypatch.setenv("ZOE_RESTRAINT", "enforce")
    kept = [it[2] for it in restraint.filter_card_items("u1", items)]
    assert kept == ["m-diet", "m-allergy", "m-asthma", "m-work", "m-trip", "m-sis"]
    monkeypatch.setenv("ZOE_RESTRAINT", "shadow")
    with caplog.at_level(logging.INFO):
        assert restraint.filter_card_items("u1", items) is items                  # nothing removed in shadow
    assert any("surface=card mode=shadow withheld=5" in r.getMessage() for r in caplog.records)
    assert "mortgage" not in caplog.text and "Ingrid" not in caplog.text          # counts and class names, never text
    monkeypatch.setenv("ZOE_RESTRAINT", "off")
    assert restraint.filter_card_items("u1", items) is items


class _Row:
    def __init__(self, vals):
        self.vals = vals

    async def fetchone(self):
        return self.vals


class _Db:
    def __init__(self, card):
        self.card = card

    async def execute(self, *_a, **_k):
        return _Row((_json.dumps(self.card), umc.render_card(self.card), umc.card_version(umc.render_card(self.card))))


def _serve(monkeypatch, mode):
    @contextlib.asynccontextmanager
    async def ctx():
        yield _Db(CARD)

    async def live(_uid, ids):
        return set(ids)

    import db_pool

    monkeypatch.setattr(db_pool, "get_db_ctx", ctx)
    monkeypatch.setattr(umc, "_live_ids", live)
    monkeypatch.setenv("ZOE_RESTRAINT", mode)
    import asyncio

    return asyncio.run(umc.load_card_block("u1"))


def test_the_served_card_carries_no_money_grief_or_family_trouble_and_only_safety_health_in_enforce(monkeypatch):
    out = _serve(monkeypatch, "enforce")["text"]
    for gone in ("mortgage", "Ingrid", "will", "Back pain", "back pain", "migraine"):
        assert gone not in out, gone
    for kept in ("EpiPen", "asthma", "pescatarian", "Marisol", "night shifts", "Lisbon"):
        assert kept in out, kept


def test_the_served_card_is_byte_identical_to_the_stored_one_in_shadow_and_off(monkeypatch):
    stored = umc.render_card(CARD)
    assert _serve(monkeypatch, "shadow")["text"] == stored
    assert _serve(monkeypatch, "off")["text"] == stored
    assert "mortgage" in stored and "migraine" in stored.lower()


def test_break_the_fix_a_neutered_filter_leaves_the_mortgage_on_the_card(monkeypatch):
    monkeypatch.setattr(restraint, "card_withheld", lambda *_a, **_k: ())
    assert "mortgage" in _serve(monkeypatch, "enforce")["text"]


def test_the_enforced_card_is_stable_across_calls_for_the_prefix_cache(monkeypatch):
    a, b = _serve(monkeypatch, "enforce"), _serve(monkeypatch, "enforce")
    assert a == b and a["version"] == umc.card_version(a["text"]) != umc.card_version(umc.render_card(CARD))
