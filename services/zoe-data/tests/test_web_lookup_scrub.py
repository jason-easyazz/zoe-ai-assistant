"""The web-lookup query scrubber: nothing private leaves the house.

Every test plants SENTINELS (invented names, places, memory texts - synthetic
data only) and checks the scrubbed query with an oracle that shares no code
with the scrubber: letters and digits only, lowercased, then a substring
search. The oracle is proved able to say "leak" by a negative control.
ci_safe: pure functions, no sockets, no database.
"""
from __future__ import annotations

import random
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
    ("covid19 symptoms b2b 4k tv iphone17", "covid19 symptoms b2b 4k tv iphone17"),
    ("\u6771\u4eac\u306e\u5929\u6c17 caf\u00e9 \u043f\u043e\u0433\u043e\u0434\u0430", "\u6771\u4eac\u306e\u5929\u6c17 caf\u00e9 \u043f\u043e\u0433\u043e\u0434\u0430"),
]


@pytest.mark.parametrize("raw,expected", PUBLIC)
def test_a_public_question_becomes_minimal_keywords(raw, expected):
    r = scrub_query(raw, TERMS)
    assert (r.send, r.query, r.reason, r.stripped) == (True, expected, "ok", 0)


STRIPPED = [
    ("Is Mirabel's shellfish allergy dangerous", "shellfish allergy dangerous", {"private"}),
    ("is MIRABEL QUILLFEATHER's shellfish allergy dangerous", "shellfish allergy dangerous", {"private"}),
    ("cheap flights from Wrenfield to Tokyo in June", "cheap flights tokyo june", {"private"}),
    ("jazz festivals 2026 for my friend Dana", "jazz festivals 2026 friend", {"relation_name"}),
    ("dog food recall for our dog called biscuit brand list", "dog food recall dog called brand list", {"relation_name"}),
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
    ("mi rabel shellfish allergy symptoms", "tripwire"),
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
    assert scrub_query("admiral nelson biography miracle", terms).query == "admiral nelson biography miracle"
    assert scrub_query("al birthday cake recipes easy", terms).query == "birthday cake recipes easy"


def test_a_long_private_word_is_stripped_inside_any_token_and_the_short_one_limit_is_pinned():
    assert scrub_query("mirabelfamily holiday rentals coast", TERMS).query == "holiday rentals coast"
    assert scrub_query("xxharrowgatexx uniform shop hours", TERMS).query == "uniform shop hours"
    # KNOWN LIMIT (documented in docs/knowledge/web-lookup.md): under six letters + three or more glued letters.
    assert leaks(scrub_query("chloefamily holiday rentals coast", TERMS).query) == ["chloe"]


def test_each_half_of_a_hyphenated_or_apostrophised_name_is_private():
    terms = PrivateTerms.of(names=["Mary-Jane O'Brannock"])
    for raw in ("mary birthday cake recipes easy", "Jane birthday cake recipes easy", "brannock birthday cake recipes easy",
                "maryjane obrannock birthday cake recipes easy", "Mary-Jane O'Brannock's birthday cake recipes easy"):
        assert scrub_query(raw, terms).query == "birthday cake recipes easy", raw


def test_output_is_bounded():
    r = scrub_query(" ".join(f"word{chr(97 + i)}" for i in range(26)), TERMS)
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
