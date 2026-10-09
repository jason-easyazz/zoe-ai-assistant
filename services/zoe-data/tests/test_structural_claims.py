"""Unit tests of the structural floor's building blocks (``structural_claims`` + ``lexicons``): the mode switch, the claim row's
validation, the own-words wall (quote is a substring of the owner's turn), value matching by edit distance, polarity from the lexicon
(negation scope, denied ends, accounted contrasts), the decision's reasons, retirement by key. No model, no store, no network.

The labelled-set harness (``test_structural_floors_labelled.py``) drives these functions with 337 rows; this file pins their edges one by one.
"""
from __future__ import annotations

import pytest

import lexicons as lex
import memory_digest
import structural_claims as sc

pytestmark = pytest.mark.ci_safe


def C(**kw):
    d = dict(subj="user", pred="residence", obj="Perth", pol="affirm", mod="asserted", tense="current", quote="I live in Perth", lang="en")
    d.update(kw)
    claim, why = sc.parse_claim(d)
    assert claim is not None, why
    return claim


# -- the switch ------------------------------------------------------------------------------------------

@pytest.mark.parametrize("raw,want", [
    (None, "shadow"), ("", "shadow"), ("shadow", "shadow"), ("SHADOW", "shadow"), ("enforce", "enforce"), (" Enforce ", "enforce"),
    ("on", "shadow"), ("1", "shadow"), ("true", "shadow"), ("enforced", "shadow"), ("yes", "shadow"),   # only the word enforce enforces
    ("off", "off"), ("0", "off"), ("false", "off"), ("no", "off"), ("disabled", "off"),
])
def test_mode_default_is_shadow_and_only_the_word_enforce_enforces(monkeypatch, raw, want):
    if raw is None:
        monkeypatch.delenv(sc.ENV, raising=False)
    else:
        monkeypatch.setenv(sc.ENV, raw)
    assert sc.mode() == want and sc.enforcing() == (want == "enforce") and sc.active() == (want != "off")


# -- the claim row ---------------------------------------------------------------------------------------

def test_parse_claim_validates_the_closed_vocabularies():
    ok, why = sc.parse_claim({"subj": "The User", "pred": "Residence", "obj": " Perth ", "pol": "AFFIRM", "mod": "asserted",
                              "tense": "present", "quote": " I live in Perth. ", "lang": "en-AU"})
    assert why == "" and ok.subj == "user" and ok.pred == "residence" and ok.obj == "Perth" and ok.tense == "current"
    assert ok.quote == "I live in Perth" and ok.lang == "en"
    for bad, reason in (({"pol": "maybe"}, "bad_polarity"), ({"mod": "sure"}, "bad_modality"), ({"tense": "someday"}, "bad_tense"),
                        ({"quote": ""}, "no_quote"), ("{not json", "unparseable"), (None, "not_an_object"), ([], "not_an_object")):
        base = {"subj": "user", "pred": "residence", "obj": "x", "pol": "affirm", "mod": "asserted", "tense": "current", "quote": "q q q"}
        raw = {**base, **bad} if isinstance(bad, dict) else bad
        assert sc.parse_claim(raw) == (None, reason), bad


@pytest.mark.parametrize("raw,want", [
    ("user", "user"), ("USER", "user"), ("rel:mum", "rel:mother"), ("rel:madre", "rel:mother"), ("mother", "rel:mother"),
    ("person:Dana", "person:Dana"), ("Dana", "person:Dana"), ("dana", "other"), ("", "other"), ("rel:", "other")])
def test_subjects_normalise_to_language_neutral_kin_codes(raw, want):
    assert sc.normalise_subject(raw) == want


def test_the_claim_round_trips_through_its_stored_json():
    c = C(quote="I live in Perth", lang="es")
    again = sc.claim_from_metadata({"claim": c.to_json()})
    assert again == c and sc.claim_from_metadata({}) is None and sc.claim_from_metadata({"claim": "garbage"}) is None


# -- the own-words wall -----------------------------------------------------------------------------------

def test_the_quote_must_be_a_substring_of_the_owners_turn():
    turn = "Hi!  My mum   lives in Bendigo, not Ballarat."
    assert sc.quote_basis("my mum lives in bendigo", turn) == "raw"               # case + whitespace folded
    assert sc.quote_basis("My mum lives in Bendigo.", turn) == "raw"             # edge punctuation tolerated
    assert sc.quote_basis("Mi mamá vive en Bendigo", "mi mama vive en bendigo") == "raw"        # accents folded
    assert sc.quote_basis("My mother resides in Bendigo", turn) == ""            # a paraphrase is not the owner's words
    assert sc.quote_basis("ok", "ok then") == ""                                  # too short to be a quote
    assert sc.quote_basis("我妈妈住在", "我妈妈住在本迪戈。") == "raw"                  # CJK: two characters are a quote
    assert sc.quote_basis("my birthday is 15 March 1980", "my birthday is 15/03/1980") == "normalised"   # the model read the dated turn
    assert sc.quote_basis("my birthday is 15/03/1980", "my birthday is 15/03/1980") == "raw"


# -- values: edit distance, never stemming ------------------------------------------------------------------

@pytest.mark.parametrize("value,text,want", [
    ("Bendigo", "my mum lives in Bendigo", True),
    ("Marisol", "my sister Marisal came over", True),             # 7 letters: 2 edits allowed (the forgetting ledger's rule)
    ("Dana", "Dani came over", False),                           # 4 letters: exact only
    ("Sarah", "Sara came over", True), ("Sarah", "Sam came over", False),    # 5 letters: 1 edit
    ("smoking", "User no longer smokes", True),                  # an inflection of the same word
    ("Ballarat", "lives in Ballina", False),                     # two different places
    ("15 March 1980", "birthday is 15 March 1980", True), ("15 March 1980", "birthday is 16 March 1980", False),   # a number is never fuzzy
    ("4th of May", "Casey's birthday is 4 May", True),           # ordinal suffix and a function word carry no value
    ("本迪戈", "我妈妈住在本迪戈", True), ("本迪戈", "我妈妈住在巴拉瑞特", False),
    ("", "anything", False), ("x", "", False)])
def test_value_in(value, text, want):
    assert sc.value_in(value, text) is want


def test_edit_distance_caps():
    assert sc.edit_distance("kitten", "sitting", 5) == 3 and sc.edit_distance("a", "abcdef", 2) == 3 and sc.edit_distance("same", "same") == 0


# -- polarity from the lexicon -----------------------------------------------------------------------------

@pytest.mark.parametrize("lang,text,obj,cls,positive", [
    ("en", "I don't live in Perth", "Perth", "not_hold", True),
    ("en", "I live in Perth", "Perth", "hold", False),
    ("en", "I stopped playing squash", "squash", "not_hold", True),
    ("en", "I haven't dropped the Harbourtown half-marathon", "Harbourtown", "hold", True),          # a DENIED end
    ("en", "I did not drop it", "it", "hold", True),
    ("en", "User no longer plays squash", "squash", "not_hold", True),
    ("en", "I did not enjoy the marathon, so I dropped it", "marathon", "not_hold", True),            # the negation is another clause's
    ("en", "My mum lives in Bendigo, but not at the moment", "Bendigo", "not_hold", True),            # a temporal scope denies it for now
    ("en", "My mum lives near Bendigo, but not there", "Bendigo", "not_hold", True),                 # anaphora points back at the value
    ("es", "No he abandonado la media maratón", "maratón", "hold", True),
    ("es", "Dejé de jugar al squash", "squash", "not_hold", True),
    ("de", "Ich wohne nicht in Perth", "Perth", "not_hold", True),
    ("de", "Ich habe den Halbmarathon nicht abgesagt", "Halbmarathon", "hold", True),
    ("fr", "Je n'habite pas à Perth", "Perth", "not_hold", True),
    ("zh", "我不住在珀斯", "珀斯", "not_hold", True), ("zh", "我没有放弃马拉松", "马拉松", "hold", True),
    ("ja", "私はパースに住んでいません", "パース", "not_hold", True), ("ja", "ハーフマラソンはやめていません", "ハーフマラソン", "hold", True),
    ("ja", "スカッシュはやめました", "スカッシュ", "not_hold", True),
])
def test_text_polarity(lang, text, obj, cls, positive):
    got = sc.text_polarity(text, lang, obj)
    assert (got.cls, got.positive) == (cls, positive), got


def test_a_contrast_is_accounted_for_by_a_sibling_claim_or_it_is_ambiguous():
    quote, obj = "My mum lives in Bendigo, not Ballarat", "Bendigo"
    assert sc.text_polarity(quote, "en", obj).flags == ("unaccounted_negation",)
    sib = C(subj="rel:mother", obj="Ballarat", pol="negate", quote="not Ballarat")
    assert sc.text_polarity(quote, "en", obj, [sib]).flags == () and sc.text_polarity(quote, "en", obj, [sib]).cls == "hold"
    # "not in the town itself": nothing accounts for it - the declined case of the review thread is now an explicit ambiguity
    assert sc.text_polarity("My mum lives in Bendigo, but not in the town itself", "en", obj).flags == ("unaccounted_negation",)
    # a hedge after the negation ("not sure about it") negates the claim itself
    hedge = sc.text_polarity("I live in Perth, not sure about it", "en", "Perth")
    assert hedge.cls == "not_hold" and "hedge_negation" in hedge.flags


def test_a_language_without_a_lexicon_is_unknown_never_guessed_from_english():
    assert sc.text_polarity("Ik woon niet in Perth", "nl", "Perth") == sc.TextPolarity(None)
    assert lex.markers("anything", "nl") == {k: False for k in ("negation", "ended", "hedge", "hypothetical", "question", "past")}
    assert lex.available() == ("en", "es", "fr", "de", "zh", "ja")


# -- the decision ----------------------------------------------------------------------------------------

def D(fact, claim, turn, **kw):
    return sc.decide(fact, claim, turn, **kw)


def test_a_plain_statement_is_promoted_and_a_second_language_is_no_different():
    assert D("User lives in Perth", C(), "I live in Perth.").label == "promote"
    es = C(obj="Perth", quote="Vivo en Perth", lang="es")
    assert D("El usuario vive en Perth", es, "Vivo en Perth.").label == "promote"


@pytest.mark.parametrize("name,kw,fact,turn,reason", [
    ("invented quote", dict(quote="I reside in Perth"), "User lives in Perth", "I live in Perth.", "quote_not_in_turn"),
    ("value not in quote", dict(obj="Hobart"), "User lives in Hobart", "I live in Perth.", "value_not_in_quote"),
    ("value not in fact", dict(obj="Perth"), "User lives in Hobart", "I live in Perth.", "value_not_in_fact"),
    ("a number the quote lacks", dict(pred="birthday", obj="March", quote="my birthday is in March"), "User's birthday is 15 March 1980",
     "my birthday is in March", "fact_number_not_in_quote"),
    ("no value", dict(obj=""), "User lives in Perth", "I live in Perth.", "no_value"),
    ("a question", dict(mod="question", quote="Do I live in Perth"), "User lives in Perth", "Do I live in Perth?", "modality:question"),
    ("reported speech", dict(mod="reported", quote="Dana said I live in Perth"), "User lives in Perth", "Dana said I live in Perth", "modality:reported"),
    ("someone else's fact", dict(subj="person:Dana", quote="Dana lives in Perth"), "User lives in Perth", "Dana lives in Perth", "subject_mismatch"),
    ("a relative's fact worded as the user's", dict(subj="rel:sister", quote="my sister lives in Perth"), "User lives in Perth",
     "my sister lives in Perth", "subject_mismatch"),
    ("a relative in the quote, claimed as the user's", dict(quote="my sister lives in Perth"), "User lives in Perth", "my sister lives in Perth",
     "relative_in_quote"),
    ("no first person", dict(quote="Dana lives in Perth"), "User lives in Perth", "Dana lives in Perth", "no_first_person"),
    ("polarity of the wording", dict(pol="negate", quote="I don't live in Perth"), "User lives in Perth", "I don't live in Perth.", "fact_polarity_mismatch"),
    ("polarity of the quote", dict(quote="I don't live in Perth"), "User lives in Perth", "I don't live in Perth.", "quote_polarity_mismatch"),
    ("a past state worded as current", dict(tense="past", quote="I used to live in Perth"), "User lives in Perth", "I used to live in Perth.",
     "tense_mismatch"),
])
def test_what_stops_an_anchor(name, kw, fact, turn, reason):
    d = D(fact, C(**kw), turn)
    assert not d.anchored and not d.promoted and reason in d.reasons, (name, d)


@pytest.mark.parametrize("name,kw,fact,turn,reason", [
    ("hedged", dict(mod="hedged", quote="I live in Perth, I think"), "User lives in Perth", "I live in Perth, I think.", "not_asserted"),
    ("a named third party's fact", dict(subj="person:Dana", quote="Dana's birthday is 4 May", obj="4 May", pred="birthday"),
     "Dana's birthday is 4 May", "Dana's birthday is 4 May", "subject:person"),
    ("a plan not yet true", dict(tense="future", quote="My mum is moving to Perth next month", subj="rel:mother"), "User's mum lives in Perth",
     "My mum is moving to Perth next month.", "tense:future"),
    ("the date-normalised words", dict(pred="birthday", obj="15 March 1980", quote="my birthday is 15 March 1980"),
     "User's birthday is 15 March 1980", "my birthday is 15/03/1980", "quote_not_raw"),
    ("a marker the claim row calls plain", dict(quote="I live in Perth, I think"), "User lives in Perth", "I live in Perth, I think.", "prefilter:modality"),
    ("an unaccounted negation", dict(subj="rel:mother", obj="Bendigo", quote="My mum lives in Bendigo, but not in the town itself"),
     "User's mum lives in Bendigo", "My mum lives in Bendigo, but not in the town itself.", "prefilter:unaccounted_negation"),
])
def test_what_anchors_but_does_not_promote(name, kw, fact, turn, reason):
    d = D(fact, C(**kw), turn)
    assert d.anchored and not d.promoted and reason in d.reasons, (name, d)


def test_an_unverified_speaker_anchors_but_never_promotes():
    d = D("User lives in Perth", C(), "I live in Perth.", speaker_verified=False)
    assert d.anchored and not d.promoted and d.reasons == ("speaker_not_verified",)


def test_a_denial_or_an_end_needs_a_wording_the_lexicon_can_read():
    nl = C(pred="activity", obj="squash", pol="ended", quote="Ik speel geen squash meer", lang="nl")
    d = D("Gebruiker speelt geen squash meer", nl, "Ik speel geen squash meer.")
    assert not d.anchored and "polarity_uncorroborated" in d.reasons and "polarity_uncorroborated" in d.ambiguous
    # ... while an affirmation in the same unknown language is not blocked by the missing lexicon
    ok = C(obj="Perth", quote="Ik woon in Perth", lang="nl")
    assert D("Gebruiker woont in Perth", ok, "Ik woon in Perth.").anchored


def test_the_decision_never_raises():
    assert sc.decide("x", None, "y") == sc.NO_CLAIM
    assert sc.decide(None, C(), None).label == "hold"        # type: ignore[arg-type]


def test_pick_claim_for_fact_prefers_the_claim_carrying_the_facts_value():
    a, b = C(obj="Bendigo", subj="rel:mother"), C(obj="Ballarat", subj="rel:mother", pol="negate", quote="not Ballarat")
    own, sibs = sc.pick_claim_for_fact("User's mum lives in Ballarat", [a, b])
    assert own is b and sibs == (a,)
    assert sc.pick_claim_for_fact("User likes tea", [a, b])[0] is None


# -- retirement by key -----------------------------------------------------------------------------------

def test_retirement_by_key():
    old = C(subj="rel:mother", obj="Ballarat")
    assert sc.retires(C(subj="rel:mother", obj="Ballarat", pol="negate", quote="not Ballarat"), old) == "retract:residence"
    assert sc.retires(C(subj="rel:mother", obj="Bendigo"), old) == "slot:residence"                 # a new value on a one-valued slot
    assert sc.retires(C(subj="rel:mother", obj="Bendigo", pol="negate", quote="not Bendigo"), old) == ""   # a different value: not this row
    assert sc.retires(C(subj="rel:father", obj="Bendigo"), old) == ""                               # another subject
    assert sc.retires(C(subj="rel:mother", obj="Bendigo", mod="hedged"), old) == "" and sc.retires(C(subj="rel:mother", obj="Bendigo", tense="future"), old) == ""
    act = C(pred="activity", obj="squash")
    assert sc.retires(C(pred="activity", obj="squash", pol="ended"), act) == "retract:activity"
    assert sc.retires(C(pred="activity", obj="tennis"), act) == ""                                  # activity is not a one-valued slot
    assert sc.retires(C(pred="occupation", obj="doctor"), C(pred="occupation", obj="nurse")) == ""    # a second job is not a replaced one
    assert sc.retires(C(pred="activity", obj="squash", pol="ended"), C(pred="activity", obj="squash", pol="negate")) == ""   # only an affirm is retired


def test_contrast_negations_are_found_beside_their_affirm():
    claims = [C(subj="rel:mother", obj="Bendigo"), C(subj="rel:mother", obj="Ballarat", pol="negate", quote="not Ballarat"), None,
              C(obj="Perth", pol="negate", quote="I don't live in Perth")]
    assert memory_digest._contrast_negations(claims) == {1}        # the lone denial (index 3) is a fact, not a half of a contrast


# -- language detection and kin codes (the lexicon's own contract) --------------------------------------------

@pytest.mark.parametrize("text,lang", [("I live in Perth", "en"), ("Vivo en Hobart, creo", "es"), ("Je suis allergique à la pénicilline", "fr"),
                                       ("Ich wohne nicht in Perth", "de"), ("我住在霍巴特", "zh"), ("私はパースに住んでいます", "ja")])
def test_detect(text, lang):
    assert lex.detect(text) == lang


@pytest.mark.parametrize("word,code", [("mum", "mother"), ("Mamá", "mother"), ("maman", "mother"), ("Mutter", "mother"), ("妈妈", "mother"), ("お母さん", "mother"),
                                       ("esposa", "wife"), ("femme", "wife"), ("奥さん", "wife"), ("grand-mère", "grandmother"), ("friend", "friend"), ("xyzzy", None)])
def test_kin_codes_are_language_neutral(word, code):
    assert lex.kin_code(word) == code
