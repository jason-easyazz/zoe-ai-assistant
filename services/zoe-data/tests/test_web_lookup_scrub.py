"""The web-lookup query scrubber: nothing private leaves the house.

Every test plants SENTINELS (invented names, places, memory texts - synthetic
data only) and checks the scrubbed query with an oracle that shares no code
with the scrubber: letters and digits only, lowercased, then a substring
search. The oracle is proved able to say "leak" by a negative control.
ci_safe: pure functions, no sockets, no database.
"""
from __future__ import annotations

import random
import re
import unicodedata

import pytest

import web_lookup_scrub as ws
from web_lookup_scrub import PrivateTerms, scrub_query

pytestmark = pytest.mark.ci_safe

NAMES = ["Mirabel Quillfeather", "Tobiah", "Chloë Ångström", "Biscotto"]
PLACES = ["Larkspur Hollow", "Wrenfield"]
MEMORY = [
    "Mirabel is allergic to shellfish and sees Dr Okonjo at Wrenfield Clinic",
    "The gate code is 4417 and the spare key lives under the blue planter",
    "Tobiah starts at Harrowgate Primary in February",
]
TERMS = PrivateTerms.of(names=NAMES, places=PLACES, memory=MEMORY)
# What must never be findable in an outbound query (compare-form, see _squeeze).
SENTINELS = ["mirabel", "quillfeather", "tobiah", "chloe", "angstrom", "biscotto", "larkspurhollow", "wrenfield",
             "okonjo", "4417", "harrowgate", "blueplanter", "allergictoshellfish"]


def _squeeze(text: str) -> str:
    s = unicodedata.normalize("NFKD", unicodedata.normalize("NFKC", text)).lower()
    return "".join(c for c in s if c.isascii() and c.isalnum())


def leaks(query: str) -> list[str]:
    """Independent oracle: sentinels findable in ``query`` once spacing/punctuation/case/accents are ignored."""
    sq = _squeeze(query)
    return [s for s in SENTINELS if s in sq]


def test_oracle_negative_control_catches_an_unscrubbed_query():
    assert leaks("is Mirabel's shellfish allergy serious") == ["mirabel"]
    assert leaks("M.i.r.a.b.e.l lives at larkspur  hollow") == ["mirabel", "larkspurhollow"]
    assert leaks("who won the 2026 world cup final") == []


PUBLIC = [
    ("who won the 2026 world cup final", "who won 2026 world cup final"),
    ("What is the price of an iPhone 17 today?", "what price iphone 17 today"),
    ("hey zoe can you look up how to fix a leaking tap please", "how fix leaking tap"),
    ("bitcoin", "bitcoin"),
    ("5 best ways to learn chess", "5 best ways learn chess"),
    ("gift ideas for my wife", "gift ideas wife"),
    ("internationalization best practices", "internationalization best practices"),
    ("covid19 symptoms 4k tv iphone17", "covid19 symptoms 4k tv iphone17"),  # a dictionary word + number, a number + unit
]


@pytest.mark.parametrize("raw,expected", PUBLIC)
def test_a_public_question_becomes_minimal_keywords(raw, expected):
    r = scrub_query(raw, TERMS)
    assert (r.send, r.query, r.reason, r.stripped) == (True, expected, "ok", 0)


STRIPPED = [
    ("Is Mirabel's shellfish allergy dangerous", "shellfish allergy dangerous", {"private"}),
    ("is MIRABEL QUILLFEATHER's shellfish allergy dangerous", "shellfish allergy dangerous", {"private"}),
    ("cheap flights from Wrenfield to Japan in June", "cheap flights japan june", {"private"}),
    ("jazz festivals 2026 for my friend Dana", "jazz festivals 2026 friend", {"relation_name"}),
    ("dog food recall for our dog called biscuit brand list", "dog food recall dog called list", {"relation_name"}),  # the word after a named one is dropped as a possible surname: privacy over query quality
    ("best pizza delivery call 0491 570 006 tonight", "best pizza delivery call tonight", {"number"}),
    ("is tobiah@example.com a known scam sender address", "known scam sender address", {"email"}),
    ("pollen count today at -31.9523, 115.8613 forecast", "pollen count today forecast", {"coordinates"}),
    ("council bin day for 12 Larkspur Lane this week schedule", "council bin day week schedule", {"address"}),
    ("gate code 4417 keypad reset instructions manual", "keypad reset instructions manual", {"memory", "private"}),
    ("ｍｉｒａｂｅｌ shellfish allergy dangerous", "shellfish allergy dangerous", {"private"}),
    ("Mira​bel shellfish allergy dangerous", "shellfish allergy dangerous", {"private"}),
    ("m.i.r.a.b.e.l shellfish allergy dangerous", "shellfish allergy dangerous", {"private"}),
    ("Mirabel-Quillfeather shellfish allergy dangerous", "shellfish allergy dangerous", {"private"}),
    ("tobiahquillfeather92 violin lessons price", "violin lessons price", {"private"}),
    ("Zoë Ångström violin lessons price", "violin lessons price", {"private"}),
    ("mirabels shellfish allergy dangerous", "shellfish allergy dangerous", {"private"}),
    ("m1rabel shellfish allergy dangerous", "shellfish allergy dangerous", {"private"}),
    ("T0b1ah violin lessons price", "violin lessons price", {"private"}),
]


@pytest.mark.parametrize("raw,expected,kinds", STRIPPED)
def test_private_parts_are_stripped_and_the_rest_is_sent(raw, expected, kinds):
    r = scrub_query(raw, TERMS)
    assert r.send and r.query == expected, r
    assert set(r.kinds) == kinds and r.stripped >= len(kinds)
    assert leaks(r.query) == []


REFUSED = [
    ("", "empty"), ("   ", "empty"), ("are you sure?", "empty"), (None, "empty"), (42, "empty"),
    ("x" * 301, "too_long"),
    ("how old is Tobiah", "private_only"),
    ("Mirabel Quillfeather", "private_only"),
    ("weather in Wrenfield", "private_only"),
    ("restaurants near 12 Larkspur Lane Wrenfield", "private_only"),
    ("what is at https://example.com/?q=mirabel", "link"),
    ("www.example.com reviews", "link"),
    ("lookup TWlyYWJlbCBRdWlsbGZlYXRoZXI shellfish", "encoded"),
    ("lookup 6d69726162656c2071756c6c shellfish", "encoded"),
    ("lookup a1b2c3d4e5f6 shellfish", "encoded"),
    ("M i r a b e l shellfish allergy", "spelled_out"),
    ("\u041cirabel shellfish allergy dangerous", "mixed_script"),  # Cyrillic capital Em, looks like M
    ("allergic to shellfish symptoms treatment", "memory_heavy"),
    ("spare key lives under the blue planter", "memory_heavy"),
]


@pytest.mark.parametrize("raw,reason", REFUSED)
def test_when_in_doubt_nothing_is_sent(raw, reason):
    r = scrub_query(raw, TERMS)
    assert (r.send, r.query, r.reason) == (False, "", reason)


def test_the_result_never_carries_what_was_removed():
    r = scrub_query("is Mirabel Quillfeather's shellfish allergy dangerous near Wrenfield", TERMS)
    assert r.send and leaks(repr(r)) == []
    refused = scrub_query("how old is Tobiah", TERMS)
    assert leaks(repr(refused)) == []


def test_without_private_terms_structural_rules_still_hold():
    r = scrub_query("is sam@example.com at 4 Example Street a scam sender address", None)
    assert r.send and "@" not in r.query and "example street" not in r.query and set(r.kinds) == {"address", "email"}


def test_a_short_name_matches_whole_words_only():
    terms = PrivateTerms.of(names=["Al", "Mira"])
    assert scrub_query("aluminium foil recycling rules", terms).query == "aluminium foil recycling rules"
    assert scrub_query("admiral navy biography miracle", terms).query == "admiral navy biography miracle"
    assert scrub_query("al birthday cake recipes easy", terms).query == "birthday cake recipes easy"


def test_a_long_private_word_is_stripped_inside_any_token_and_the_short_one_limit_is_pinned():
    assert scrub_query("mirabelfamily holiday rentals coast", TERMS).query == "holiday rentals coast"
    assert scrub_query("xxharrowgatexx uniform shop hours", TERMS).query == "uniform shop hours"
    # KNOWN LIMIT (documented in docs/knowledge/web-lookup.md): under six letters + three or more glued letters.
    # The short-name limit this pinned is CLOSED by the allow-list: an unknown glued word never leaves.
    assert leaks(scrub_query("chloefamily holiday rentals coast", TERMS).query) == []


def test_each_half_of_a_hyphenated_or_apostrophised_name_is_private():
    terms = PrivateTerms.of(names=["Mary-Jane O'Brannock"])
    for raw in ("mary birthday cake recipes easy", "Jane birthday cake recipes easy", "brannock birthday cake recipes easy",
                "maryjane obrannock birthday cake recipes easy", "Mary-Jane O'Brannock's birthday cake recipes easy"):
        assert scrub_query(raw, terms).query == "birthday cake recipes easy", raw


def test_output_is_bounded():
    words = ("apple bread chair dance eagle fabric garden hammer island jacket kettle ladder magnet needle orange "
             "pencil quilt river saddle table umbrella violin window yellow zebra anchor").split()
    r = scrub_query(" ".join(words), TERMS)
    assert r.send and len(r.query.split()) == ws.MAX_WORDS and len(r.query) <= ws.MAX_CHARS


_TEMPLATES = [
    "what is {s} doing this weekend near the coast", "{s} news today headlines", "is {s} mentioned in recent recalls list",
    "best schools ranking {s} 2026 results", "how tall is the tower compared with {s}", "weather radar {s} storm warning tonight",
    "recipe ideas {s} dinner tonight quick", "{s}", "tell me about {s}", "cheap flights {s} tokyo june",
]
_SECRETS = ["Mirabel", "Quillfeather", "Mirabel Quillfeather", "Tobiah", "Chloë Ångström", "Biscotto", "Larkspur Hollow",
            "Wrenfield", "Dr Okonjo", "4417", "Harrowgate Primary", "tobiah@example.com", "the blue planter",
            "allergic to shellfish"]


def _obfuscations(s: str, rng: random.Random) -> list[str]:
    return [
        s, s.upper(), s.lower(), s + "'s", s.replace(" ", "-"), s.replace(" ", "."), s.replace(" ", ""),
        "​".join(s), ".".join(s.replace(" ", "")), " ".join(s.replace(" ", "")),
        unicodedata.normalize("NFD", s), "".join(chr(ord(c) + 0xFEE0) if "!" <= c <= "~" else c for c in s),
        s + str(rng.randint(10, 99)), s[: len(s) // 2] + " " + s[len(s) // 2:], s.replace(" ", "") + "x1",
    ]


def test_planted_sentinels_never_leave_under_obfuscation():
    """Seeded sweep: 14 secrets x 15 spellings x 10 phrasings = 2100 queries; none may leak, sent or not."""
    rng = random.Random(20261114)
    sent = refused = 0
    for secret in _SECRETS:
        for variant in _obfuscations(secret, rng):
            for template in _TEMPLATES:
                r = scrub_query(template.format(s=variant), TERMS)
                assert leaks(r.query) == [], (template, variant, r)
                assert r.send == bool(r.query)
                sent += r.send
                refused += not r.send
    assert sent + refused == 2100 and sent > 300 and refused > 300  # both arms are exercised


def test_the_scrubber_is_pure_and_total():
    rng = random.Random(7)
    alphabet = "abc XYZ 019 .-'@/:​‮éＡ\n\t\x00"
    for _ in range(500):
        raw = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 60)))
        a, b = scrub_query(raw, TERMS), scrub_query(raw, TERMS)
        assert a == b and a.send == bool(a.query) and "\n" not in a.query and "\x00" not in a.query


def test_the_module_does_no_io_and_no_logging():
    src = open(ws.__file__, encoding="utf-8").read()
    for banned in ("import logging", "import os", "import socket", "urllib", "httpx", "requests", "print("):
        assert banned not in src


@pytest.mark.parametrize("raw", ["mi ra bel shellfish allergy symptoms", "m ira bel shellfish allergy symptoms",
                                 "mir a b el shellfish allergy symptoms"])
def test_a_known_name_split_over_three_or_more_words_never_leaves(raw):
    r = scrub_query(raw, PrivateTerms.of(names=["Mirabel"]))
    assert (not r.send) or ("mirabel" not in r.query.replace(" ", "")), r


@pytest.mark.parametrize("raw", ["jazz festivals for my friend dana smith", "jazz festivals for my friend dana",
                                 "concerts my partner sam would like"])
def test_a_lowercase_name_after_my_relation_is_stripped(raw):
    r = scrub_query(raw)
    assert r.send, r
    assert not {"dana", "smith", "sam"}.intersection(r.query.split()), r


def test_a_relation_word_used_as_a_plain_noun_keeps_the_query():
    assert scrub_query("dog food recall brands australia").query == "dog food recall brands australia"


@pytest.mark.parametrize("raw,gone", [
    ("weather l4rkspur hollow forecast today", {"larkspur", "l4rkspur", "hollow"}),
    ("jazz festivals my friend called dana smith tonight", {"dana", "smith"}),
    ("jazz festivals for friend Dana Smith", {"dana", "smith"}),
])
def test_digit_spelled_places_and_relation_surnames_never_leave(raw, gone):
    r = scrub_query(raw, PrivateTerms.of(names=["Mirabel"], places=["Larkspur Hollow"]))
    assert r.send, r
    assert not gone.intersection(r.query.split()), r


def test_a_split_name_with_lookalike_digits_never_leaves():
    r = scrub_query("m1 ra bel shellfish allergy symptoms", PrivateTerms.of(names=["Mirabel"]))
    assert (not r.send) or ("mirabel" not in r.query.replace(" ", "").translate(str.maketrans("1", "i"))), r


@pytest.mark.parametrize("raw,name", [("ki m violin lessons price", "Kim"), ("b o swimming lessons", "Bo")])
def test_a_short_known_name_split_into_pieces_never_leaves(raw, name):
    r = scrub_query(raw, PrivateTerms.of(names=[name]))
    assert not r.send or name.casefold() not in r.query.replace(" ", ""), r


@pytest.mark.parametrize("raw", ["pizza lasagna recipe", "real estate prices"])
def test_a_short_name_is_not_found_across_ordinary_words(raw):
    assert scrub_query(raw, PrivateTerms.of(names=["Al"])).query == raw


@pytest.mark.parametrize("raw", ["example.com?token=syntheticsecret reviews", "shop.example.org#ref=abc review",
                                 "example.net:8080 status", "local.example.io/path?x=1 hours"])
def test_a_schemeless_link_that_can_carry_data_is_refused(raw):
    r = scrub_query(raw)
    assert not r.send and r.reason == "link", r


def test_a_bare_domain_alone_is_still_a_word():
    assert scrub_query("best sites like reddit.com").send


@pytest.mark.parametrize("raw", ["pizza delivery call 0491/570/006 tonight", "pizza delivery call 0491–570–006 tonight"])
def test_a_phone_number_with_any_separator_is_stripped(raw):
    r = scrub_query(raw)
    assert r.send and not any(ch.isdigit() for ch in r.query), r


def test_a_fraction_is_not_a_phone_number():
    assert scrub_query("3/4 cup flour conversion").send



def test_a_split_name_is_dropped_or_refused_never_sent():
    r = scrub_query("mi rabel shellfish allergy symptoms", TERMS)
    assert not r.send or not leaks(r.query), r


def test_words_the_allow_list_cannot_check_never_leave():
    """The allow-list is English: words in another script cannot be checked, so they are dropped (fail closed)."""
    r = scrub_query("\u6771\u4eac\u306e\u5929\u6c17 caf\u00e9 \u043f\u043e\u0433\u043e\u0434\u0430")
    assert not r.send or set(r.query.split()) <= {"cafe"}, r


@pytest.mark.parametrize("raw,gone", [("xqzvbn uniform shop hours", "xqzvbn"), ("x7k2 door code reset", "x7k2"),
                                      ("syntheticsecret reviews", "syntheticsecret"),
                                      ("b2b software reviews", "b2b")])
def test_a_word_that_is_not_in_the_dictionary_never_leaves(raw, gone):
    r = scrub_query(raw)
    assert gone not in r.query, r


def test_a_missing_allow_list_fails_closed(monkeypatch):
    monkeypatch.setattr(ws, "allow_words", lambda: frozenset())
    assert not scrub_query("weather forecast tomorrow").send


def test_truncation_never_cuts_a_word():
    r = scrub_query(" ".join(["internationalization internationalisation"] * 3))
    assert r.send and all(w in ("internationalization", "internationalisation") for w in r.query.split()), r


def test_letters_spelled_out_with_dots_never_leave():
    r = scrub_query("p.r.i.c.e reviews today")
    assert "p.r.i.c.e" not in r.query, r


@pytest.mark.parametrize("raw,gone", [("12zz reviews today", "12zz"), ("7qx tracking parcel", "7qx")])
def test_only_listed_units_follow_a_number(raw, gone):
    assert gone not in scrub_query(raw).query


def test_listed_units_and_decimals_still_leave():
    assert scrub_query("4k tv 5g phone 3rd party").query == "4k tv 5g phone 3rd party"
    assert scrub_query("what is 3.5 times 2").query == "what 3.5 times 2"


@pytest.mark.parametrize("raw", ["2026", "4k 5g", "weather forecast tomorrow"])
def test_a_missing_allow_list_refuses_even_numbers(monkeypatch, raw):
    monkeypatch.setattr(ws, "allow_words", lambda: frozenset())
    r = scrub_query(raw)
    assert not r.send and r.reason == ws.REASON_NO_ALLOW_LIST, r


@pytest.mark.parametrize("raw", ["rose2 mary violin lessons price", "blue4 planter replacement prices"])
def test_a_digit_padding_a_known_name_or_memory_never_hides_it(raw):
    t = PrivateTerms.of(names=["Rosemary"], memory=["the spare key is under the blue planter"])
    r = scrub_query(raw, t)
    squeezed = "".join(ch for ch in r.query if not ch.isdigit()).replace(" ", "")
    assert not r.send or ("rosemary" not in squeezed and "blueplanter" not in squeezed), r


@pytest.mark.parametrize("raw", ["rose-mary garden ideas", "rose.mary garden ideas", "mary-rose garden ideas"])
def test_a_private_name_inside_a_hyphenated_dictionary_compound_is_stripped(raw):
    # "rose" and "mary" are both dictionary words, so the allow-list alone passes "rose-mary"; the name is a part.
    r = scrub_query(raw, PrivateTerms.of(names=["Rose"]))
    assert "rose" not in r.query.replace("-", " ").replace(".", " ").split()
    assert r.send and r.query.startswith("garden")


def test_a_proper_name_only_entry_is_not_an_allowed_word():
    # SCOWL lists "Jennifer" capitalised only; folding it to an ordinary word would let a person's name out.
    allow = ws.allow_words()
    assert {"jennifer", "michael", "sarah", "nelson"}.isdisjoint(allow)
    assert {"rose", "violin", "tv", "monday", "christmas", "japan"}.issubset(allow)   # words, abbreviations, supplement
    r = scrub_query("jennifer violin lessons price", PrivateTerms())
    assert r.send and r.query == "violin lessons price"


def test_a_malformed_lexicon_refuses_instead_of_raising():
    r = scrub_query("weather forecast", lexicon={"enabled": True})
    assert not r.send and r.reason == ws.REASON_ERROR


def test_a_dictionary_word_that_starts_with_a_member_name_still_leaves():
    # OWNER DECISION 2026-11-16: every word that leaves must already be an ordinary dictionary word, so a name can only
    # get out glued into something if the result is itself a real word ("rosemary", "billboard"), which says nothing
    # about the person. Stripping those would weaken real searches for every household with a Rose, Bill or Mark.
    terms = PrivateTerms.of(names=["Rose", "Bill"])
    assert scrub_query("rosemary garden ideas", terms).query == "rosemary garden ideas"
    assert scrub_query("billboard charts week", terms).query == "billboard charts week"
    # ...while the name itself, split or glued into a non-word, still never leaves
    assert "rose" not in scrub_query("rose garden ideas", terms).query.split()
    assert scrub_query("roseparty garden ideas", terms).query == "garden ideas"



@pytest.mark.parametrize("raw", ["red box2 replacement", "red2 box replacement", "red b0x replacement",
                                 "red-box replacement prices", "red.box replacement prices", "red_box replacement", "red/box2 replacement",
                                 "red 2 box replacement prices", "red 2 3 box replacement", "red 2-box replacement",
                                 "red x box replacement prices", "red boxes replacement prices", "reds box replacement"])
def test_a_short_memory_phrase_with_a_digit_added_is_still_stripped(raw):
    # Codex P1 on #2527: a memory phrase shorter than the glue length ("red box") escaped once a digit was stuck to one word
    r = scrub_query(raw, PrivateTerms.of(memory=["The spare key is in the red box"]))
    flat = set(re.split(r"[\s.\-_/]+", r.query))
    assert not ({"red", "red2", "reds"} & flat and {"box", "box2", "b0x", "boxes"} & flat), r


def test_a_hyphenated_public_compound_is_kept():
    # Codex P2 on #2527: the allow-list saw the folded "glutenfree" (not a word) instead of "gluten" + "free"
    r = scrub_query("gluten-free bread recipes", PrivateTerms())
    assert r.send and r.query.startswith("gluten-free"), r


def test_a_separator_sweep_finds_no_way_to_split_a_short_memory_phrase():
    """Every separator between the two words of a short memory phrase (punctuation, unicode dashes, invisible spaces, emoji,
    digits, a filler letter) and every spelling (case, look-alike digits, a plural) still strips it."""
    import itertools

    terms = PrivateTerms.of(memory=["The spare key is in the red box"])
    seps = [" ", "-", ".", "_", "/", "'", "\u2019", " 2 ", " 2 3 ", "2", " - ", "\u2013", "\u2014", "\u200b", "\u00a0", "+", ",", ";",
            ":", "&", "|", "~", "*", " \U0001F642 ", "(", ")", "\t", "  ", " x ", " #2 "]
    leaks = []
    for left, sep, right in itertools.product(["red", "RED", "r3d", "red2", "reds"], seps, ["box", "Box", "b0x", "box2", "boxes"]):
        r = scrub_query(f"{left}{sep}{right} replacement prices", terms)
        flat = set(re.split(r"[\W_]+", r.query.lower())) if r.send else set()
        if {"red", "r3d", "red2", "reds"} & flat and {"box", "b0x", "box2", "boxes"} & flat:
            leaks.append((left + sep + right, r.query))
    assert leaks == [], leaks[:5]


@pytest.mark.parametrize("raw", ["red xqzv box replacement prices", "red qq zzkx box replacement prices",
                                 "red xqzv box-prices replacement", "price-red xqzv box replacement", "red xqzv box.prices replacement"])
def test_dropping_an_unknown_word_cannot_join_a_memory_phrase(raw):
    r = scrub_query(raw, PrivateTerms.of(memory=["The spare key is in the red box"]))
    flat = set(re.split(r"[\W_]+", r.query)) if r.send else set()
    assert not ({"red"} & flat and {"box"} & flat), r


@pytest.mark.parametrize("raw", ["pizza delivery call 0491\u2011570\u2011006 tonight", "pizza delivery call 0491\u2212570\u2212006 tonight",
                                 "pizza delivery call 0491\u2010570\u2010006 tonight"])
def test_a_phone_number_with_unicode_hyphens_is_stripped(raw):
    r = scrub_query(raw)
    assert "0491" not in r.query and "570" not in r.query, r


def test_a_comma_after_the_house_number_is_still_an_address():
    r = scrub_query("pizza delivery 12, Maple Street tonight")
    assert "maple" not in r.query and "12" not in r.query.split(), r


_SEPARATORS = [" ", "-", ".", ",", ", ", "_", "/", "\u2011", "\u2013", "\u2212", " - ", "\u00a0", "(", ")", "·", "|", "~", "*", ":", ";"]


@pytest.mark.parametrize("sep", _SEPARATORS)
def test_a_phone_number_is_stripped_whatever_separates_its_groups(sep):
    r = scrub_query(f"pizza delivery call 0491{sep}570{sep}006 tonight")
    assert not ({"0491", "570", "006"} & set(re.split(r"[\W_]+", r.query))), (sep, r)


@pytest.mark.parametrize("sep", [" ", ".", "-", "_", ", ", ",", "\u2013", " . "])
def test_an_address_is_stripped_whatever_separates_its_parts(sep):
    r = scrub_query(f"pizza delivery 12{sep}Maple{sep}Street tonight")
    assert "maple" not in r.query, (sep, r)


@pytest.mark.parametrize("memory", ["The spare key is in the red-box", "The spare key is in the red.box", "The spare key is in the red_box"])
@pytest.mark.parametrize("raw", ["red box replacement prices", "red-box replacement prices", "red boxes replacement prices"])
def test_a_compound_in_the_stored_memory_protects_every_spelling(memory, raw):
    r = scrub_query(raw, PrivateTerms.of(memory=[memory]))
    flat = set(re.split(r"[\W_]+", r.query)) if r.send else set()
    assert not ({"red"} & flat and {"box", "boxes"} & flat), r


def test_a_two_letter_part_of_a_hyphenated_name_is_private_as_a_whole_word():
    r = scrub_query("bo violin lessons price", PrivateTerms.of(names=["Bo-Peep"]))
    assert "bo" not in r.query.split(), r
