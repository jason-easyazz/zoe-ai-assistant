"""The pure half of the memory claims (memory redesign step 3, ``memory_claims``): strict parsing of the model's claim JSON,
the attribute vocabulary, the value normalisers (every date / amount shape from the design note's evidence appendix) and
the name helpers. Synthetic values only. ``ci_safe``."""
from __future__ import annotations

import json
from datetime import date

import pytest

import memory_claims as mc
import structural_claims as sc

pytestmark = pytest.mark.ci_safe


# -- strict parsing -------------------------------------------------------------------------------------------------

GOOD = {"type": "correction", "turn": 1, "quote": "juno is 8 not 7", "subject": "Juno", "attribute": "age", "old": "7",
        "new": "8", "time": "current"}


def test_a_well_formed_claim_of_each_type_parses():
    c = mc.parse_claim(GOOD)
    assert c == mc.CorrectionClaim(1, "juno is 8 not 7", "Juno", "age", "8", "current", "7")
    assert mc.parse_claim({**GOOD, "old": None}).old is None
    assert mc.parse_claim({k: v for k, v in GOOD.items() if k != "old"}).old is None
    k = mc.parse_claim({"type": "kind", "turn": 0, "quote": "pip is a cat", "subject": "Pip", "kind": "animal"})
    assert isinstance(k, mc.KindClaim) and k.kind == "animal"
    e = mc.parse_claim({"type": "emergency", "turn": 0, "quote": "i fell", "subject": "I", "event": "fall",
                        "time": "current"})
    assert isinstance(e, mc.EmergencyClaim) and e.event == "fall"


@pytest.mark.parametrize("raw, reason", [
    ({**GOOD, "confidence": 0.9}, "unknown_field"),
    ({**GOOD, "turn": True}, "bad_turn"),
    ({**GOOD, "turn": "1"}, "bad_turn"),
    ({**GOOD, "turn": -1}, "bad_turn"),
    ({**GOOD, "quote": "x" * 161}, "bad_quote"),
    ({**GOOD, "quote": "   "}, "bad_quote"),
    ({**GOOD, "new": 8}, "bad_new"),
    ({**GOOD, "old": 7}, "bad_old"),
    ({**GOOD, "attribute": "height"}, "bad_attribute"),
    ({**GOOD, "attribute": "other"}, "bad_attribute"),
    ({**GOOD, "time": "hypothetical"}, "bad_time"),
    ({k: v for k, v in GOOD.items() if k != "subject"}, "bad_subject"),
    ({**GOOD, "type": "fact"}, "bad_type"),
    ({"type": "kind", "turn": 0, "quote": "pip is a cat", "subject": "Pip", "kind": "cat"}, "bad_kind"),
    ({"type": "emergency", "turn": 0, "quote": "x fell", "subject": "I", "event": "stubbed_toe", "time": "current"},
     "bad_event"),
    ({"type": "emergency", "turn": 0, "quote": "x fell", "subject": "I", "event": "fall", "time": "future"}, "bad_time"),
    (["not", "an", "object"], "not_an_object"),
])
def test_a_malformed_claim_is_an_item_never_an_exception(raw, reason):
    got = mc.parse_claim(raw)
    assert isinstance(got, mc.Malformed) and got.reason == reason


@pytest.mark.parametrize("raw, error", [
    ("{not json", "unparseable"),
    ([], "bad_envelope"),
    ({"claims": [], "abstain": None, "extra": 1}, "bad_envelope"),
    ({"abstain": "unsure"}, "bad_envelope"),
    ({"claims": [GOOD] * 4, "abstain": None}, "bad_claims"),
    ({"claims": GOOD, "abstain": None}, "bad_claims"),
    ({"claims": [], "abstain": "maybe"}, "bad_abstain"),
])
def test_a_malformed_envelope_is_an_error_code(raw, error):
    assert mc.parse_output(raw).error == error


def test_an_envelope_parses_from_json_text_and_keeps_bad_items_in_place():
    out = mc.parse_output(json.dumps({"claims": [GOOD, {**GOOD, "x": 1}], "abstain": None}))
    assert out.error == "" and isinstance(out.items[0], mc.CorrectionClaim) and isinstance(out.items[1], mc.Malformed)
    assert mc.parse_output({"claims": [], "abstain": "no_correction"}).abstain == "no_correction"


def test_claim_ids_are_stable_and_distinct():
    a = mc.claim_id("demo_u", "ut-1", 0)
    assert a == mc.claim_id("demo_u", "ut-1", 0) and len(a) == 16
    assert a != mc.claim_id("demo_u", "ut-1", 1) != mc.claim_id("demo_v", "ut-1", 1)


# -- the attribute vocabulary --------------------------------------------------------------------------------------

def test_every_attribute_maps_onto_an_existing_structural_predicate():
    assert set(mc.ATTRIBUTES) == {"event_date", "origin_date", "birthday", "age", "amount", "measure", "count",
                                  "time_of_day", "schedule", "location", "employer", "partner", "diet"}
    for spec in mc.ATTRIBUTES.values():
        assert spec.predicate in sc.PREDICATES or spec.predicate.startswith("kin:")
        assert spec.units and set(spec.units) <= mc.UNITS
    assert {a for a, s in mc.ATTRIBUTES.items() if s.needs_old} == {"employer", "diet"}


# -- the normalisers, table-driven ---------------------------------------------------------------------------------

DATES = [
    # evidence appendix rows 6, 7, 8, 9, 16, 17, 21 and design note G C05-C14, C39
    ("the 19th", {"d": 19}), ("19th", {"d": 19}), ("the 21st", {"d": 21}), ("the 14th", {"d": 14}),
    ("the 5th", {"d": 5}), ("the 3rd", {"d": 3}), ("the 2nd", {"d": 2}), ("1st", {"d": 1}),
    ("the 12th of january", {"m": 1, "d": 12}), ("12th of December", {"m": 12, "d": 12}),
    ("the 27th of november", {"m": 11, "d": 27}), ("the twentieth of November", {"m": 11, "d": 20}),
    ("twenty-first of may", {"m": 5, "d": 21}), ("twenty first of may", {"m": 5, "d": 21}),
    ("the thirtieth of june", {"m": 6, "d": 30}), ("December 19", {"m": 12, "d": 19}), ("19 Dec", {"m": 12, "d": 19}),
    ("the 3rd of may", {"m": 5, "d": 3}), ("sept 4", {"m": 9, "d": 4}),
    ("12th of December 2026", {"y": 2026, "m": 12, "d": 12}), ("12 December 2026", {"y": 2026, "m": 12, "d": 12}),
    ("the 12th of december, 2026", {"y": 2026, "m": 12, "d": 12}), ("2026-12-19", {"y": 2026, "m": 12, "d": 19}),
    ("12/12", {"m": 12, "d": 12}), ("3/1/2027", {"y": 2027, "m": 1, "d": 3}), ("on 13/12", {"m": 12, "d": 13}),
    ("January", {"m": 1}), ("in january", {"m": 1}), ("March 2027", {"y": 2027, "m": 3}),
    ("friday the 19th", {"d": 19}), ("29 February", {"m": 2, "d": 29}),
    # abstain: relative, a weekday alone, a 2-digit year, impossible dates, money, a day and year with no month
    ("next friday", None), ("tomorrow", None), ("friday", None), ("7/8/91", None), ("31 April", None),
    ("29 February 2027", None), ("the 32nd", None), ("$20", None), ("the 12th, 2026", None), ("", None),
    ("the 12th and the 19th", None), ("december january", None),
]


@pytest.mark.parametrize("text, want", DATES)
def test_dates(text, want):
    got = mc.normalise("event_date", text)
    assert (got.value if got else None) == want
    if got:
        assert got.unit == "date"


@pytest.mark.parametrize("text, want", [
    ("the 3rd of may", {"m": 5, "d": 3}), ("the 4th", {"d": 4}), ("4 May 2019", {"y": 2019, "m": 5, "d": 4}),
    ("May", None), ("2019", None),
])
def test_birthdays_name_a_day(text, want):
    got = mc.normalise("birthday", text)
    assert (got.value if got else None) == want


AMOUNTS = [
    # evidence appendix rows 9, 10, 18 and design note G C17-C21, C43
    ("$13,500", "13500", "AUD", 1), ("$12,000", "12000", "AUD", 1), ("13.5k", "13500", "AUD", 1000),
    ("12k", "12000", "AUD", 1000), ("13.5 k", "13500", "AUD", 1000), ("13 thousand", "13000", "AUD", 1000),
    ("twelve thousand dollars", "12000", "AUD", 1000), ("a hundred thousand", "100000", "AUD", 1000),
    ("twelve thousand five hundred", "12500", "AUD", 1000), ("2 grand", "2000", "AUD", 1000),
    ("1.2m", "1200000", "AUD", 1_000_000), ("1.2 million", "1200000", "AUD", 1_000_000), ("27", "27", "AUD", 1),
    ("20 dollar", "20", "AUD", 1), ("20 dollars", "20", "AUD", 1), ("$27.50", "27.5", "AUD", 1),
    ("US$40", "40", "USD", 1), ("40 usd", "40", "USD", 1), ("€15", "15", "EUR", 1), ("fifty bucks", "50", "AUD", 1),
]


@pytest.mark.parametrize("text, v, cur, scale", AMOUNTS)
def test_amounts(text, v, cur, scale):
    got = mc.normalise("amount", text)
    assert got.value == {"v": v, "cur": cur} and got.unit == "money" and got.scale == scale


@pytest.mark.parametrize("text", ["50 cents", "lots", "13,50", "the 12th", "12 km", "-5"])
def test_amounts_that_abstain(text):
    assert mc.normalise("amount", text) is None


@pytest.mark.parametrize("old, new, readings, new_v", [
    ("12", "13 thousand", [{"v": "12", "cur": "AUD"}, {"v": "12000", "cur": "AUD"}], "13000"),     # the qa8 rule
    ("12k", "13.5k", [{"v": "12000", "cur": "AUD"}], "13500"),
    ("$12,000", "13.5k", [{"v": "12000", "cur": "AUD"}], "13500"),           # a full number takes no scale
    ("$12,000", "$13,500", [{"v": "12000", "cur": "AUD"}], "13500"),
    ("20", "27", [{"v": "20", "cur": "AUD"}], "27"),
])
def test_amount_pairs(old, new, readings, new_v):
    ok, o, n = mc.normalise_pair("amount", old, new)
    assert ok and list(o.readings()) == readings and n.value["v"] == new_v


def test_amount_pairs_take_a_stated_currency_and_refuse_a_mismatch():
    ok, o, n = mc.normalise_pair("amount", "40", "US$45")
    assert ok and o.value["cur"] == n.value["cur"] == "USD"
    ok, _, _ = mc.normalise_pair("amount", "€40", "US$45")
    assert not ok


AGES = [("8", 8.0), ("eight", 8.0), ("8 years old", 8.0), ("9 years", 9.0), ("seven years old", 7.0), ("8 yo", 8.0),
        ("three and a half", 3.5), ("3 and a half years old", 3.5), ("3.5", 3.5), ("twenty-one", 21.0), ("0", 0.0),
        ("8 dogs", None), ("8th", None), ("8 months", None), ("3.3", None), ("121", None), ("the 8th of may", None),
        ("$8", None), ("8 km", None)]


@pytest.mark.parametrize("text, want", AGES)
def test_ages_are_never_counts_dates_or_money(text, want):
    got = mc.normalise("age", text)
    assert (got.value["v"] if got else None) == want


@pytest.mark.parametrize("text, want", [("3 dogs", 3), ("8", 8), ("three", 3), ("twenty four", 24), ("8 years", None),
                                        ("8 years old", None), ("2.5", None), ("$3", None), ("5 km", None)])
def test_counts_are_never_ages(text, want):
    got = mc.normalise("count", text)
    assert (got.value["v"] if got else None) == want


@pytest.mark.parametrize("text, want", [("5 km", ("5", "km")), ("5km", ("5", "km")), ("2.5 hours", ("2.5", "hour")),
                                        ("3%", ("3", "percent")), ("ten kilos", ("10", "kg")), ("5", None),
                                        ("5 dogs", None)])
def test_measures_keep_their_unit(text, want):
    got = mc.normalise("measure", text)
    assert ((got.value["v"], got.unit) if got else None) == want


def test_a_bare_old_measure_takes_the_new_unit_and_a_unit_clash_abstains():
    ok, o, n = mc.normalise_pair("measure", "5", "6 km")
    assert ok and o.value == {"v": "5", "u": "km"}
    ok, _, _ = mc.normalise_pair("measure", "5 kg", "6 km")
    assert not ok


TIMES = [("5pm", {"h": 17, "mi": 0}), ("5 pm", {"h": 17, "mi": 0}), ("5.30 p.m.", {"h": 17, "mi": 30}),
         ("4pm", {"h": 16, "mi": 0}), ("9am", {"h": 9, "mi": 0}), ("12am", {"h": 0, "mi": 0}),
         ("12pm", {"h": 12, "mi": 0}), ("17:00", {"h": 17, "mi": 0}), ("09:30", {"h": 9, "mi": 30}),
         ("noon", {"h": 12, "mi": 0}), ("midnight", {"h": 0, "mi": 0}), ("five pm", {"h": 17, "mi": 0}),
         ("at 5 o'clock", {"h12": 5, "mi": 0}), ("5", {"h12": 5, "mi": 0}), ("5:30", {"h12": 5, "mi": 30}),
         ("13pm", None), ("25:00", None), ("5:7", None), ("half past five", None), ("soon", None)]


@pytest.mark.parametrize("text, want", TIMES)
def test_times_of_day(text, want):
    got = mc.normalise("time_of_day", text)
    assert (got.value if got else None) == want


def test_an_hour_alone_inherits_the_stored_half_of_the_day_or_abstains():
    assert mc.resolve_time({"h12": 5, "mi": 0}, {"h": 16, "mi": 0}) == {"h": 17, "mi": 0}
    assert mc.resolve_time({"h12": 5, "mi": 0}, {"h": 9, "mi": 0}) == {"h": 5, "mi": 0}
    assert mc.resolve_time({"h12": 5, "mi": 0}, {}) is None
    assert mc.old_matches("time_of_day", {"h12": 4, "mi": 0}, {"h": 16, "mi": 0})
    assert not mc.old_matches("time_of_day", {"h12": 4, "mi": 0}, {"h": 17, "mi": 0})


@pytest.mark.parametrize("text, want", [
    ("on wednesday", {"freq": "weekly", "byday": ["WE"]}), ("wednesdays", {"freq": "weekly", "byday": ["WE"]}),
    ("mondays and thursdays", {"freq": "weekly", "byday": ["MO", "TH"]}), ("every day", {"freq": "daily"}),
    ("weekly", {"freq": "weekly"}), ("every month", {"freq": "monthly"}),
    ("weekdays at 5pm", {"freq": "weekly", "byday": ["MO", "TU", "WE", "TH", "FR"], "time": {"h": 17, "mi": 0}}),
    ("next week", None), ("daily on monday", None), ("at noonish", None),
])
def test_schedules(text, want):
    got = mc.normalise("schedule", text)
    assert (got.value if got else None) == want


@pytest.mark.parametrize("attribute, text, want, unit", [
    ("location", "Ballarat", {"label": "ballarat"}, "place"), ("location", "Wagga Wagga", {"label": "wagga wagga"}, "place"),
    ("employer", "Corvid Freight", {"label": "corvid freight"}, "org"), ("diet", "Vegan", {"label": "vegan"}, "label"),
    ("partner", "Sam Reyes", {"name": "sam reyes"}, "person"), ("location", "  ", None, None),
])
def test_labels_fold(attribute, text, want, unit):
    got = mc.normalise(attribute, text)
    assert (got.value, got.unit) == (want, unit) if want else got is None


def test_partial_dates_compare_and_merge_without_guessing():
    assert mc.date_matches({"d": 12}, {"m": 12, "d": 12})
    assert mc.date_matches({"m": 12, "d": 12}, {"y": 2026, "m": 12, "d": 12})      # a year on one side: (m, d)
    assert not mc.date_matches({"m": 11, "d": 12}, {"m": 12, "d": 12})
    assert not mc.date_matches({"y": 2026}, {"m": 12, "d": 12})                   # nothing shared
    assert mc.merge_date({"d": 19}, {"y": 2026, "m": 12, "d": 12}) == ({"y": 2026, "m": 12, "d": 19}, True)
    assert mc.merge_date({"m": 1, "d": 12}, {"m": 12, "d": 12}) == ({"m": 1, "d": 12}, False)
    assert mc.merge_date({"m": 1}, {"m": 12, "d": 12}) == ({"m": 1}, False)       # a month-only move drops the day
    assert mc.merge_date({"y": 2027, "m": 1, "d": 3}, {"y": 2026, "m": 12, "d": 12}) == ({"y": 2027, "m": 1, "d": 3}, False)
    assert mc.date_not_before({"y": 2026, "m": 12, "d": 19}, date(2026, 11, 14))
    assert not mc.date_not_before({"y": 2026, "m": 1, "d": 3}, date(2026, 11, 14))
    assert not mc.date_not_before({"m": 1, "d": 3}, date(2026, 11, 14))


def test_age_from_a_birthday_with_a_year():
    assert mc.age_at({"y": 2019, "m": 5, "d": 4}, date(2026, 11, 14)) == 7
    assert mc.age_at({"y": 2019, "m": 12, "d": 4}, date(2026, 11, 14)) == 6
    assert mc.age_at({"m": 5, "d": 4}, date(2026, 11, 14)) is None


def test_row_spans_compute_their_value():
    text = "Book club is on the 12th of December 2026"
    start = text.index("12th")
    span = f"{start}:{len(text)}"
    assert mc.span_value("event_date", text, span).value == {"y": 2026, "m": 12, "d": 12}
    assert mc.span_value("event_date", text, f"{start}:{len(text) + 1}") is None
    assert mc.span_value("event_date", text, "5:2") is None and mc.span_value("event_date", text, "x") is None


# -- names and subjects --------------------------------------------------------------------------------------------

def test_name_parts_keep_hyphens_and_drop_a_possessive():
    assert mc.name_parts("Ana-Lise Moss") == ("ana-lise", "moss")
    assert mc.name_parts("Juno's swimming") == ("juno", "swimming")
    assert mc.name_parts("José  O'Brien") == ("jose", "obrien")
    assert mc.occurs_in("my daughter juno started school", "Juno")
    assert not mc.occurs_in("ana-lise is 7", "ana")
    assert not mc.occurs_in("kais 21 not 22", "Kai")
    assert mc.occurs_in("the boat fund is 12k", "boat fund")


def test_lazy_possessive_base_and_label_keys():
    assert mc.possessive_base("kais") == "kai" and mc.possessive_base("Kai") is None and mc.possessive_base("is") is None
    assert mc.label_key("the boat fund") == "boat fund" and mc.label_key("My Savings") == "savings"


@pytest.mark.parametrize("subject, form, value", [
    ("I", "member", ""), ("me", "member", ""), ("my dad", "relation", "father"), ("our son", "relation", "son"),
    ("she", "pronoun", ""), ("It", "pronoun", ""), ("Juno", "name", "Juno"), ("book club", "name", "book club"),
    ("my grandson", "name", "my grandson"), ("", "unknown", ""),
])
def test_subject_forms(subject, form, value):
    assert mc.subject_form(subject) == mc.SubjectForm(form, value)


@pytest.mark.parametrize("rel, pet", [("pet", True), ("pet dog", True), ("Pet cat", True), ("", False),
                                      ("son", False), ("petsitter", False), (None, False)])
def test_the_pet_marker_is_correction_apply_s(rel, pet):
    assert mc.is_pet_relationship(rel) is pet


# -- spelled numbers: the accumulator (review of PR 2477) ----------------------------------------------------------

def test_one_hundred_is_a_hundred_not_one():
    assert mc.normalise("count", "one hundred").value == {"v": 100}
    assert mc.normalise("count", "a hundred").value == {"v": 100}
    assert mc.normalise("count", "one hundred dogs").value == {"v": 100}


def test_several_scale_words_are_not_scaled_twice():
    assert mc.normalise("amount", "two hundred thousand").value["v"] == "200000"
    assert mc.normalise("amount", "one million five hundred thousand").value["v"] == "1500000"
    assert mc.normalise("amount", "one million five hundred thousand dollars").value["v"] == "1500000"


_ONES = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen " \
        "eighteen nineteen".split()
_TENS_W = "twenty thirty forty fifty sixty seventy eighty ninety".split()


def _words(n: int, *, and_=False) -> str:
    """An independent number-to-words writer (not memory_claims' reader): 0..999,999,999."""
    def below_1000(k: int) -> str:
        parts = []
        if k >= 100:
            parts.append(_ONES[k // 100] + " hundred")
            k %= 100
            if k and and_:
                parts.append("and")
        if k >= 20:
            parts.append(_TENS_W[k // 10 - 2] + ("-" + _ONES[k % 10] if k % 10 else ""))
        elif k:
            parts.append(_ONES[k])
        return " ".join(parts)

    if n == 0:
        return "zero"
    out = []
    for scale, word in ((1_000_000, "million"), (1000, "thousand"), (1, "")):
        if n >= scale:
            out.append((below_1000(n // scale) + (" " + word if word else "")).strip())
            n %= scale
    return " ".join(out)


SPOT = [0, 7, 10, 11, 19, 20, 21, 99, 100, 101, 110, 999, 1000, 1001, 1100, 12000, 13500, 99999, 100000, 200000,
        999999, 1000000, 1500000, 999999999, 1000500, 250075, 40000013]


@pytest.mark.parametrize("n", SPOT)
@pytest.mark.parametrize("and_", [False, True])
def test_spelled_and_digit_numbers_agree(n, and_):
    words = _words(n, and_=and_)
    assert mc.normalise("count", words).value == {"v": n}, words
    assert mc.normalise("count", f"{n:,}").value == {"v": n}
    assert mc.normalise("count", str(n)).value == {"v": n}
    assert mc.normalise("amount", words + " dollars").value["v"] == str(n), words
    assert mc.normalise("amount", f"${n:,}").value["v"] == str(n)


def test_the_test_writer_is_itself_sane():
    assert _words(1500000) == "one million five hundred thousand"
    assert _words(101, and_=True) == "one hundred and one" and _words(21) == "twenty-one"


@pytest.mark.parametrize("text", [
    "a couple of hundred", "five six", "twenty thirty", "one hundred five twenty", "thousand million",
    "one thousand one million", "hundred", "one hundred and", "and five", "zero five", "one zero", "a five",
    "five hundred hundred", "a few thousand", "one hundred thousand thousand",
])
def test_spelled_numbers_it_cannot_read_with_certainty_abstain(text):
    assert mc.normalise("count", text) is None
    assert mc.normalise("amount", text) is None


@pytest.mark.parametrize("text, v, scale", [
    ("12 thousand", "12000", 1000), ("13.5k", "13500", 1000), ("2 grand", "2000", 1000), ("two grand", "2000", 1000),
    ("1.2m", "1200000", 1_000_000), ("1.5 billion", "1500000000", 1_000_000_000),
    ("one hundred and five thousand", "105000", 1000), ("a hundred thousand", "100000", 1000),
])
def test_scale_words_after_numerals_and_inside_spelled_numbers(text, v, scale):
    got = mc.normalise("amount", text)
    assert got.value["v"] == v and got.scale == scale


# -- a leading decimal point, and a sign before the currency (review of PR 2477, round 2) -------------------------

@pytest.mark.parametrize("text", ["$.50", ".50", ".5", "0.50", "$0.50", "$ .50", "$.5"])
def test_a_leading_decimal_point_is_kept(text):
    assert mc.normalise("amount", text).value == {"v": "0.5", "cur": "AUD"}


@pytest.mark.parametrize("attribute, text, want", [
    ("measure", ".5 km", {"v": "0.5", "u": "km"}), ("age", ".5", {"v": 0.5}), ("count", ".5", None),
    ("event_date", "the .5th", None), ("time_of_day", ".30", None), ("time_of_day", ".30pm", None),
])
def test_a_leading_decimal_point_in_the_other_normalisers(attribute, text, want):
    got = mc.normalise(attribute, text)
    assert (got.value if got else None) == want


@pytest.mark.parametrize("text", [
    "-$50", "-US$50", "−$50", "–$50", "—$50", "- $ 50", "-A$50", "-AUD 50", "-.5", "-$.50", "$-50",
    "($50)", "(50)", "(US$50)", "($ 50 )",
])
def test_a_negative_amount_abstains_wherever_the_sign_sits(text):
    assert mc.normalise("amount", text) is None


@pytest.mark.parametrize("attribute, text", [
    ("count", "–2"), ("count", "-2 dogs"), ("count", "(3)"), ("measure", "–5 km"), ("measure", "−.5 km"),
    ("age", "−3"), ("age", "–3"), ("event_date", "-5th"), ("time_of_day", "-5pm"),
])
def test_a_negative_value_abstains_in_the_other_normalisers(attribute, text):
    assert mc.normalise(attribute, text) is None


@pytest.mark.parametrize("attribute, text, want", [
    # a dash after a word character is a range or a hyphenated word, never a sign: these abstain for OTHER reasons
    ("amount", "10-$50", None),               # two numbers: a range is not one amount
    ("amount", "pay-$50", None),              # "pay" is no amount word
    ("amount", "5-10 dollars", None),
    # and hyphens that are no sign at all still read
    ("amount", "twenty-one dollars", {"v": "21", "cur": "AUD"}),
    ("event_date", "2026-12-19", {"y": 2026, "m": 12, "d": 19}),
    ("event_date", "twenty-first of may", {"m": 5, "d": 21}),
    ("age", "twenty-one", {"v": 21.0}),
])
def test_a_range_or_hyphenated_word_is_not_a_negative(attribute, text, want):
    got = mc.normalise(attribute, text)
    assert (got.value if got else None) == want


# -- impossible completed dates; the raw-text guard every numeric normaliser runs (review of PR 2477, round 3) -----

@pytest.mark.parametrize("new, stored, want", [
    ({"d": 31}, {"m": 2, "d": 1}, None),                                        # 31 -> February
    ({"d": 29}, {"y": 2027, "m": 2, "d": 1}, None),                             # 29 -> February of a non-leap year
    ({"d": 29}, {"y": 2028, "m": 2, "d": 1}, {"y": 2028, "m": 2, "d": 29}),     # a leap year
    ({"d": 30}, {"m": 4, "d": 1}, {"m": 4, "d": 30}),
    ({"d": 31}, {"m": 4, "d": 1}, None),                                        # 31 -> April
    ({"d": 29}, {"m": 2, "d": 1}, {"m": 2, "d": 29}),                           # no year anywhere: possible in some year
    ({"m": 2, "d": 29}, {"y": 2027, "m": 1, "d": 3}, None),                     # the inherited year decides
    ({"m": 2}, {"y": 2027, "m": 1, "d": 31}, {"y": 2027, "m": 2}),              # a month-only move drops the day
])
def test_a_completed_date_must_exist(new, stored, want):
    assert mc.merge_date(new, stored)[0] == want


def test_a_schedule_hands_the_raw_clock_text_to_the_time_guard():
    for text in ("every day at -7:00", "mondays at −5pm", "mondays at –7:00", "weekdays at - 5pm"):
        assert mc.normalise("schedule", text) is None, text
    assert mc.normalise("schedule", "mondays at 7:00").value == {"freq": "weekly", "byday": ["MO"],
                                                                 "time": {"h12": 7, "mi": 0}}


@pytest.mark.parametrize("attribute, text", [
    ("amount", "¥500"), ("amount", "₹500"), ("amount", "+5"), ("amount", "±5"), ("measure", "~5 km"),
    ("measure", "5°"), ("count", "+3"), ("age", "~8"), ("event_date", "the 19th +1"), ("time_of_day", "~5pm"),
])
def test_a_symbol_the_tokeniser_would_drop_abstains(attribute, text):
    assert mc.normalise(attribute, text) is None


MINUS_FORMS = ["-", "−", "–", "—"]
VALID = [
    ("event_date", "19 December 2026"), ("birthday", "3 May"), ("age", "8 years old"), ("age", ".5"),
    ("count", "3 dogs"), ("amount", "$50"), ("amount", "US$50"), ("amount", ".50"), ("amount", "13.5k"),
    ("measure", "5 km"), ("measure", ".5 km"), ("time_of_day", "7:00"), ("time_of_day", "5pm"),
    ("schedule", "mondays at 7:00"),
]


def _minus_variants(text: str, minus: str) -> list:
    """The minus before the whole input, after a space, and directly before the first digit / point / currency mark."""
    first = next(i for i, c in enumerate(text) if c.isdigit() or c in ".$")
    if text[:first].upper().endswith("US"):
        first -= 2
    return [minus + text, minus + " " + text, text[:first] + minus + text[first:]]


@pytest.mark.parametrize("attribute, text", VALID)
def test_property_a_minus_never_yields_a_positive_value(attribute, text):
    assert mc.normalise(attribute, text) is not None                 # the input itself is valid
    for minus in MINUS_FORMS:
        for variant in _minus_variants(text, minus):
            assert mc.normalise(attribute, variant) is None, variant


@pytest.mark.parametrize("fn", [mc.norm_date, mc.norm_birthday, mc.norm_age, mc.norm_count, mc.norm_amount,
                                mc.norm_measure, mc.norm_time, mc.norm_schedule])
def test_every_numeric_normaliser_runs_the_raw_guard_itself(fn):
    assert fn("−" + "5") is None and fn("(5)") is None          # direct calls, not only through normalise()


# -- ranges: a dropped separator never leaves the two endpoints (review of PR 2477, round 3) -----------------------

WEEK = ["MO", "TU", "WE", "TH", "FR"]


@pytest.mark.parametrize("text, days", [
    ("monday-friday at 5pm", WEEK), ("monday–friday at 5pm", WEEK), ("monday to friday", WEEK),
    ("monday through friday", WEEK), ("mon-fri", WEEK), ("mondays-fridays", WEEK), ("tue-thu", ["TU", "WE", "TH"]),
    ("monday-friday weekly", WEEK),
])
def test_a_weekday_range_expands_to_every_day_in_it(text, days):
    assert mc.normalise("schedule", text).value["byday"] == days


@pytest.mark.parametrize("text", [
    "friday-monday",                         # across the week boundary: abstain (a choice, not a reading)
    "monday-monday", "monday/friday",        # no range / a slash: one, the other or both?
    "Mon - Fri at 9am", "monday — friday",   # a spaced dash is a loose minus to the raw guard: abstain
    "monday-friday and saturday-sunday", "mondays till fridays", "monday-june",
    "mondays at 9-5", "mondays at 9am-5pm", "mondays at 9am to 5pm",     # a time RANGE is no one time
])
def test_a_range_that_is_not_a_forward_weekday_range_abstains(text):
    assert mc.normalise("schedule", text) is None


@pytest.mark.parametrize("attribute, text", [
    ("time_of_day", "9-5"), ("time_of_day", "9am-5pm"), ("time_of_day", "9am to 5pm"), ("time_of_day", "9/5"),
    ("event_date", "the 12th-19th"), ("event_date", "the 12th to the 19th"), ("event_date", "12/12-19/12"),
    ("event_date", "monday-friday the 19th"), ("amount", "10-50"), ("amount", "$10 to $50"),
    ("measure", "5-10 km"), ("count", "3-4 dogs"), ("age", "7-8"), ("age", "7 to 8"),
])
def test_a_range_in_any_other_normaliser_abstains(attribute, text):
    assert mc.normalise(attribute, text) is None


# -- deferred from the normalisers' review (PR 2477): a clock suffix is no count noun; two named currencies abstain ------

@pytest.mark.parametrize("text", ["5pm", "5 pm", "5am", "5 am", "5 p.m.", "5 A.M.", "five pm", "12 PM"])
def test_a_clock_suffix_makes_a_count_abstain(text, monkeypatch):
    assert mc.normalise("count", text) is None
    assert mc.span_value("count", text, f"0:{len(text)}") is None
    assert mc.normalise("time_of_day", text) is not None             # it IS a time
    # negative control: without the clock marks the suffix is stripped as a noun and the time reads as a count
    monkeypatch.setattr(mc, "_NOT_A_COUNT", mc._NOT_A_COUNT - mc._CLOCK_MARKS)
    assert mc.normalise("count", text) is not None


def test_a_count_with_an_ordinary_noun_still_reads():
    for text, n in (("5 dogs", 5), ("three cats", 3), ("5", 5), ("12 pmo", 12)):
        assert mc.normalise("count", text).value == {"v": n}, text


@pytest.mark.parametrize("text", ["USD 60 EUR", "US$60 euros", "EUR 60 USD", "£60 EUR", "NZ$ 5 pounds", "aud 13.5k usd",
                                  "A$60 USD", "€ 60 gbp"])
def test_an_amount_that_names_two_currencies_abstains(text):
    assert mc.normalise("amount", text) is None


@pytest.mark.parametrize("text,cur", [("USD 60", "USD"), ("60 EUR", "EUR"), ("EUR 60 euros", "EUR"), ("US$60 USD", "USD"),
                                      ("US$60 dollars", "USD"), ("$60 dollars", "AUD"), ("£60 pounds", "GBP"),
                                      ("60 bucks", "AUD")])
def test_control_one_currency_named_once_or_twice_still_reads(text, cur):
    got = mc.normalise("amount", text)
    assert got is not None and got.value == {"v": "60", "cur": cur}, text


def test_a_pair_with_a_two_currency_side_is_not_ok():
    ok, _old, new = mc.normalise_pair("amount", "50 EUR", "USD 60 EUR")
    assert not ok and new is None
    ok, old, new = mc.normalise_pair("amount", "USD 50 EUR", "60 EUR")
    assert not ok and old is None
    assert mc.normalise_pair("amount", "50 EUR", "60 EUR")[0] is True      # control
