"""The shared scrubber's widened classes (``memory_service.scrub_pii``), generated from DATA so the lists can grow.

Each class has a MUST-CATCH table (text, the secret that must not survive) and the file has one MUST-NOT table (text that
comes back byte-identical). Every value here is invented: example.com addresses, 0400 / 555 / 5550 style numbers,
"Example" streets. A new case is one line in a table, never a new test.

Negative control: every MUST-CATCH row survives the 2026-11-14 ``scrub_pii`` (origin/main e90abc6db) unchanged, except
where a row says it was already caught.
"""
from __future__ import annotations

import time

import pytest

from memory_service import scrub_pii

pytestmark = pytest.mark.ci_safe

R = "[REDACTED]"

#: class -> [(text, secret that must be gone)]. "secret" classes apply on every call; "contact" classes need contact=True.
SECRET_CATCH: dict[str, list[tuple[str, str]]] = {
    "labelled_code": [
        ("the door code is 5791", "5791"),
        ("the door code's 5791 by the way", "5791"),
        ("alarm code 4821", "4821"),
        ("gate pin: 0042", "0042"),                # already caught before 2026-11-15 ("pin:"): the one row the control passes
        ("the garage code is 1-2-3-4", "1-2-3-4"),
        ("our safe combination is 12-34-56", "12-34-56"),
        ("the key safe code is 3390.", "3390"),
        ("the padlock combo 4417", "4417"),
        ("wifi password plum-ladder-9", "plum-ladder-9"),
        ("the wi-fi key is Quokka77", "Quokka77"),
        ("the code for the gate is 8812", "8812"),
        ("code to the front door: 2468", "2468"),
        ("the gate code is 12 34", "34"),
        ("the security code is 913", "913"),
    ],
    "bare_pin_password": [
        ("my pin 4821", "4821"),
        ("pin 4821", "4821"),
        ("my pin number is 4821", "4821"),
        ("the passcode 77310 opens it", "77310"),
        ("password plum-ladder-9", "plum-ladder-9"),
        ("my old password was hunter22", "hunter22"),
        # a PIN in each SECRET form (2026-11-16 context rule): connector, label words, possessive, what it opens, a verb
        ("pin: 4821", "4821"),
        ("pin = 4821", "4821"),
        ("pin is 4821", "4821"),
        ("the pin 4821 opens the side gate", "4821"),
        ("our pin 4821", "4821"),
        ("their pin 4821 works", "4821"),
        ("PIN code 4821", "4821"),
        ("pin number 4821", "4821"),
        ("the card pin 4821", "4821"),
        ("my phone pin 4821", "4821"),
        ("the safe pin 4821", "4821"),
        ("the alarm pin 4821", "4821"),
        ("enter pin 4821 at the door", "4821"),
        ("use pin 4821", "4821"),
        ("type pin 4821 then hash", "4821"),
        ("my pin 4821 thanks", "4821"),
        ("pin 4821 thanks", "4821"),               # "thanks" is an s-word, not a plural count: a bare PIN stays a secret
        ("my pin 400 notes", "400"),               # secret context wins over a following count noun
        # DEFAULT IS REDACT: a verb, or any word not on the closed count-noun list, after the digits (cross-review #2657)
        ("pin 4821 works", "4821"),
        ("PIN 4821 expires tomorrow", "4821"),
        ("pin 4821 opens the gate", "4821"),
        ("pin 4821 unlocks the door", "4821"),
        ("PIN 4821 fails", "4821"),
        # secret context ANYWHERE in the window, not only adjacent (cross-review #2657, second pass)
        ("my secret pin 4821 photos", "4821"),
        ("my important pin 4821 photos", "4821"),
        ("the actual pin 4821 notes", "4821"),
        ("Sam's actual pin 4821 photos", "4821"),
        ("my keys, pin 300 photos", "300"),        # a comma does not end the window: redaction preferred
        # letters-only, every spoken form (invented words): redacted by DEFAULT
        ("my password zebrafiddle", "zebrafiddle"),
        ("the password zebrafiddle.", "zebrafiddle"),
        ("password: zebrafiddle", "zebrafiddle"),                # already caught before 2026-11-15 ("password:")
        ("password - zebrafiddle", "zebrafiddle"),
        ("my old password was zebrafiddle", "zebrafiddle"),
        ("my password's zebrafiddle", "zebrafiddle"),
        ("passphrase zebrafiddle now", "zebrafiddle"),
        ("Password Zebrafiddle, ok", "Zebrafiddle"),
        ("the password for the wifi is zebrafiddle", "zebrafiddle"),
        ("the password to my laptop was zebrafiddle", "zebrafiddle"),
    ],
    "labelled_account": [
        ("my account number is 12345678", "12345678"),
        ("account no. 5550 1234", "5550 1234"),
        ("BSB 062-000", "062-000"),
        ("the bsb is 062 000 and the account number is 1234 5678", "1234 5678"),
        ("sort code 12-34-56", "12-34-56"),
        ("routing number 012345678", "012345678"),
        ("my member number is 99887766", "99887766"),
        ("customer number: 55501234", "55501234"),
        ("my licence number is 1234567", "1234567"),
        ("passport number PA1234567", "PA1234567"),
        ("my tax file number is 123 456 789", "123 456 789"),
        ("medicare number 2123 45670 1", "2123 45670"),
        ("the card number is 1234 5678 9012", "1234 5678 9012"),
    ],
}
CONTACT_CATCH: dict[str, list[tuple[str, str]]] = {
    "phone": [
        ("call me on 0400 555 101", "0400 555 101"),
        ("my number is 0400555101", "0400555101"),
        ("ring the shop on (08) 5550 1202", "5550 1202"),
        ("her mobile is 04 0055 5101", "0055 5101"),
        ("call +61 400 555 101 after six", "400 555 101"),
        ("the office is +61-8-5550-1303", "5550-1303"),
        ("reach him at +44 20 7946 0958", "7946 0958"),
        ("the US number is +1 (555) 010-0199", "010-0199"),
        ("try 555-010-0199 or 555.010.0199", "010-0199"),
        ("the plumber's number is 5550 1234", "5550 1234"),
        ("text me at 5550 1234 tomorrow", "5550 1234"),
        ("call mum on 9555 0123", "9555 0123"),
        ("020 7946 0958 is the London line", "7946 0958"),
        ("07700 900123 is my cell", "900123"),
    ],
    "email": [
        ("send it to ottilie.grey@example.com please", "ottilie.grey@example.com"),
        ("my email is o+zoe@mail.example.org", "mail.example.org"),
        ("E-mail: quinn_harrow99@example.co.uk", "quinn_harrow99"),
        ("my email is ottilie at example dot com", "ottilie at example"),
    ],
    "address": [
        ("we live at 12 Example Street", "12 Example Street"),
        ("drop it at 4/27 Example Road, Springfield WA 6000", "27 Example Road"),
        ("the party is at 7 Sample Ave.", "7 Sample Ave"),
        ("Unit 3, 18 Placeholder Crescent", "18 Placeholder Crescent"),
        ("it's 221B Example Lane", "221B Example Lane"),
        ("my address is 12 example street, springfield", "12 example street"),
        ("our new address: 9 the mews, sampletown 6000", "9 the mews"),
        ("I live at 33 sample road", "33 sample road"),
        ("she lives at 5 placeholder close, springfield", "5 placeholder close"),
        ("send it to PO Box 1234", "1234"),
        ("meet at 12 St Example Terrace", "12 St Example Terrace"),
        # DEFAULT IS REDACT (2026-11-16, cross-review #2657); these carry address context as well
        ("the plumber works from 1 Example St.", "1 Example St"),
        ("I moved to 9 Sample Road last year", "9 Sample Road"),
        ("deliver it to 5 Example Way please", "5 Example Way"),
        ("come to 14 Sample Parade at six", "14 Sample Parade"),
        ("meet me at 3 Placeholder Close", "3 Placeholder Close"),
        ("we're staying at 40 Example Esplanade", "40 Example Esplanade"),
        ("they live on 8 Sample Grove", "8 Sample Grove"),
        ("my 12 Example Street place", "12 Example Street"),
        ("send it to our 12 Example Street box", "12 Example Street"),
        ("the address: 12 Example Street", "12 Example Street"),
        ("Sam's 22 Sample Drive", "22 Sample Drive"),
        ("we met at 21 Jump Street", "21 Jump Street"),         # "at" + a title reads exactly as an address: redacted
        ("the film is 21 Jump Street", "21 Jump Street"),       # a copula is a lead-in too: ambiguous, redacted
        ("12 Example Street Sampleton", "12 Example Street"),
        ("12 Example Street, Sampleton", "12 Example Street"),
        ("12 Example Street Sampleton 6000", "12 Example Street"),
        ("I saw 12 Example Street, unit 4", "12 Example Street"),
        ("Flat 2, 9 Sample Lane", "9 Sample Lane"),
        ("Lot 12 Example Road", "12 Example Road"),
        ("4/27 Example Road is the one", "27 Example Road"),
        ("12 Example Street", "12 Example Street"),             # the whole text: the answer to "what's the address?"
        ("12 Example Street is my address", "12 Example Street"),
        ("12 Example Street is where we live", "12 Example Street"),
        ("deliver to 5 Example Way and 7 Example Way", "7 Example Way"),   # the second address of a list
        ("at 5 Example Way, 7 Example Way", "7 Example Way"),
        # no lead word and no title word after it: redacted by default
        ("meet 12 Example Street", "12 Example Street"),
        ("I work 12 Example Street", "12 Example Street"),
        # MOVED from MUST_NOT (2026-11-16 first cut): no listed title word follows, so the default (redact) applies
        ("we watched 21 Jump Street last night", "21 Jump Street"),
        ("she starred in 21 Jump Street", "21 Jump Street"),
        ("I watched 21 Jump Street. Then we slept", "21 Jump Street"),
        ("we read 12 Main Street articles and then 21 Jump Street", "then 21 Jump Street"),
        ("the 3 Little Pigs Lane mural", "3 Little Pigs Lane"),
        ("play 21 Jump Street", "21 Jump Street"),
        ("21 Jump Street was funny", "21 Jump Street"),
        # a lead-in OVERRIDES the title exemption
        ("I live at 12 Example Street articles", "12 Example Street"),
        ("send it to 12 Example Street the movie set", "12 Example Street"),
        ('my "12 Example Street"', "12 Example Street"),
        # address context ANYWHERE in the window, not only adjacent (cross-review #2657, second pass)
        ("the delivery address is actually 12 Example Street articles", "12 Example Street"),
        ("my actual 12 Example Street articles", "12 Example Street"),
        ("our current 12 Example Street movie", "12 Example Street"),
        ("we went to see the 21 Jump Street film", "21 Jump Street"),     # "to" is a lead word, in the window
    ],
}

#: text that must come back byte-identical, with and without the contact classes
MUST_NOT: list[str] = [
    # numbers, quantities, ages, dates, times, years, prices, measurements, readings
    "buy 12 eggs and 2 litres of milk", "she turned 40 on Friday", "at 5:30 tomorrow", "on the 26th",
    "the appointment is 2026-11-15 at 09:30", "back in 2019", "from 1999-2001 we lived in the hills",
    "it cost $1,250.00", "it was 1 250 000 dollars", "the shelf is 1.8 m by 0.45 m", "it weighs 0.75 kg",
    "it's 38.5 degrees", "my blood sugar is 5.4", "my blood pressure was 120 over 80",
    "the score was 21 to 14", "the score was 3-2", "room 12", "channel 7", "page 204", "flight QF 1234 lands at 6",
    "we need 10 20 30 40 chairs", "number 3 is my favourite", "my lucky number is 7", "the number of people is 12",
    "call me at 5:30", "ring me in 10 minutes", "text me at 6", "call at 10 30", "add 0.5 cups of sugar",
    "05-11-2026 was a good day", "the times were 00 15 30 45",
    # code / pin words that are not secrets
    "pineapple on pizza", "the dress code is casual", "the area code", "what's the postcode", "the postcode is unknown",
    "the alarm code changed yesterday", "the gate code is green", "the wifi password changed", "pin it to the board",
    "pin 3 photos to the fridge", "the zip code", "reset my password", "the password reset tomorrow",
    # the closed list after "password": the sentence is ABOUT the password, it holds no value
    "I need a password manager", "the password reset link expired", "type it in the password field",
    "our password policy changed", "the file is password protected", "the password prompt froze", "use the password hint",
    "the password box is empty", "the password change worked", "the password rules are strict", "the password for the wifi",
    "I forgot my password.", "the password was reset yesterday", "change your password now", "my password doesn't work",
    # email / address concepts
    "send an email to the school", "I'll email you", "down the road", "we walked 5 km down the road",
    "she lives at the end of the street", "I live at home", "the road was closed", "my address is changing",
    "the IP address is 10.0.0.1", "account was hacked", "my bank account has 1200 in it", "my bank account is overdrawn",
    "it was a 5 minute walk", "look at the way she smiled",
    # 2026-11-16: a number + street phrase that is PLAINLY a title (a listed title word after it, or quoted) is kept
    "I read 12 Main Street articles", "I watched 21 Jump Street the movie", "the 21 Jump Street film",
    "21 Jump Street Season 2 is out", "we binged 21 Jump Street episodes", "the 21 Jump Street reboot was fun",
    'we watched "21 Jump Street" last night', "we watched “21 Jump Street” again",
    "the real 21 Jump Street film",                                   # "the" is not address context
    "That is my plan. We watched 21 Jump Street the movie",           # the window stops at the full stop
    "Use the app. Then pin 300 photos to the board", "can you quickly pin 120 more photos",
    # 2026-11-16: "pin" the verb + a listed count noun is not a secret
    "pin 123 photos to the board", "pin 400 notes to the board", "pin 300 of them", "please pin 250 of those",
    "can you pin 120 more photos", "pin 100 recipes for me",
]


def _cases(table: dict[str, list[tuple[str, str]]]):
    return [pytest.param(text, secret, id=f"{cls}:{text[:40]}") for cls, rows in table.items() for text, secret in rows]


@pytest.mark.parametrize("text,secret", _cases(SECRET_CATCH))
def test_secret_classes_redact_on_every_call(text, secret):
    for contact in (True, False):
        out, reject = scrub_pii(text, contact=contact)
        assert reject is None and R in out and secret not in out, (contact, out)


@pytest.mark.parametrize("text,secret", _cases(CONTACT_CATCH))
def test_contact_classes_redact_by_default(text, secret):
    out, reject = scrub_pii(text)
    assert reject is None and R in out and secret not in out, out


@pytest.mark.parametrize("text,secret", _cases(CONTACT_CATCH))
def test_the_fact_text_keeps_contact_details(text, secret):
    """contact=False (ingest's fact text): "the plumber's number is ..." is a household fact, kept as said."""
    assert scrub_pii(text, contact=False) == (text, None)


@pytest.mark.parametrize("text", MUST_NOT)
def test_ordinary_text_is_untouched(text):
    assert scrub_pii(text) == (text, None)
    assert scrub_pii(text, contact=False) == (text, None)


@pytest.mark.parametrize("text,label", [
    ("the door code is 5791", "the door code is "), ("call me on 0400 555 101", "call me on "),
    ("my account number is 12345678", "my account number is "), ("my address is 12 example street", "my address is "),
])
def test_the_label_is_kept_and_only_the_value_goes(text, label):
    assert scrub_pii(text)[0] == label + R


def test_surrounding_words_survive():
    out, _ = scrub_pii("the door code is 5791 and the plumber comes at 5:30 on the 26th")
    assert out == "the door code is [REDACTED] and the plumber comes at 5:30 on the 26th"
    out, _ = scrub_pii("send it to ottilie.grey@example.com, thanks")
    assert out == "send it to [REDACTED], thanks"


# ── what the scrubber already did is unchanged ──────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,want", [
    ("my pin is 4826 and I always forget it", ("my pin is [REDACTED] and I always forget it", None)),
    ("changing the router, the password is hunter2 btw", ("changing the router, the password is [REDACTED] btw", None)),
    ("no, march 25 - pin: 4455", ("no, march 25 - pin: [REDACTED]", None)),
    ("my card number is 4111 1111 1111 1111", ("my card number is 4111 1111 1111 1111", "luhn_cc")),
    ("my ssn is 123-45-6789", ("my ssn is 123-45-6789", "ssn")),
    ("the pin 482155 again", ("the pin 482155 again", "totp")),
    ("use key AKIAIOSFODNN7EXAMPLE now", ("use key AKIAIOSFODNN7EXAMPLE now", "aws_key")),
    ("The wifi password is on the fridge", ("The wifi password is [REDACTED] the fridge", None)),
    ("", ("", None)),
])
def test_existing_behaviour_is_unchanged(text, want):
    assert scrub_pii(text) == want


# ── the callers' contract: the night mind's verbatim gate rejects any changed text ─────────────────────────────────

@pytest.mark.parametrize("text", ["call me on 0400 555 101", "the door code is 5791", "my pin 4821",
                                  "we live at 12 Example Street", "write to ottilie.grey@example.com"])
def test_the_night_quote_gate_refuses_the_new_classes(text):
    import night_mind as nm

    assert nm._unstorable(text) is True


# ── fail-closed: the exemptions are CLOSED lists, so every word NOT on them leaves the value redacted ───────────────

#: ordinary verbs, adverbs, function words and invented words; none is on an exemption list (asserted below)
_NOT_EXEMPT = ["works", "worked", "expires", "opens", "unlocks", "fails", "changes", "matters", "lasts", "goes", "sticks",
               "is", "was", "and", "or", "but", "please", "now", "today", "tomorrow", "again", "thanks", "yesterday",
               "tonight", "last", "here", "there", "zorbles", "blicks", "flurbs", "always", "maybe", "every", "for",
               "with", "at", "on", "by", "people", "words", "times", "places", "", ".", ",", "!", "?"]


def test_the_probe_words_are_not_on_any_exemption_list():
    import memory_service as ms

    for w in _NOT_EXEMPT:
        assert not ms._PIN_COUNT_RE.match(" " + w), w
        assert not ms._ADDRESS_TITLE_RE.match(" " + w), w


@pytest.mark.parametrize("word", _NOT_EXEMPT)
def test_a_pin_followed_by_any_unlisted_word_stays_redacted(word):
    for lead in ("", "please ", "can you ", "I said "):
        text = f"{lead}pin 4821 {word}".rstrip()
        out, reject = scrub_pii(text)
        assert reject is None and "4821" not in out, (text, out)


@pytest.mark.parametrize("word", _NOT_EXEMPT)
def test_a_street_phrase_followed_by_any_unlisted_word_stays_redacted(word):
    for lead in ("", "meet ", "I work ", "we watched ", "play ", "I love ", "the "):
        text = f"{lead}12 Example Street {word}".rstrip()
        out, reject = scrub_pii(text)
        assert reject is None and "12 Example Street" not in out, (text, out)


# ── the context window: a lead word still counts with filler words between it and the candidate ────────────────────

_FILLERS = ["actual", "important", "current", "new", "old", "secret", "real", "usual", "main", "other", "florbish"]
#: every run of 1, 2 and 3 consecutive fillers (wrapping): 33 runs
_FILLER_RUNS = [" ".join((_FILLERS * 2)[i:i + k]) for k in (1, 2, 3) for i in range(len(_FILLERS))]


def _leads(words) -> list[str]:
    return sorted(set(words) | {"my", "our", "your", "his", "her", "their", "Sam's"})


def test_a_pin_lead_with_fillers_before_a_count_noun_stays_redacted():
    import memory_service as ms

    combos = [f"{lead} {run} pin 4821 photos" for lead in _leads(ms._PIN_LEADS) for run in _FILLER_RUNS]
    assert len(combos) > 2000
    leaked = [t for t in combos if "4821" in scrub_pii(t)[0]]
    assert not leaked, leaked[:5]


def test_an_address_lead_with_fillers_before_a_title_word_stays_redacted():
    import memory_service as ms

    combos = [f"{lead} {run} 12 Example Street {title}" for lead in _leads(ms._ADDRESS_LEADS) + ["address", "secret"]
              for run in _FILLER_RUNS for title in ("articles", "movie", "the film")]
    assert len(combos) > 5000
    leaked = [t for t in combos if "12 Example Street" in scrub_pii(t)[0]]
    assert not leaked, leaked[:5]


# ── performance: compiled regexes, no backtracking trap ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("blob", [
    "0" * 20000, "1 " * 10000, "+1 " * 7000, "a@" * 10000, "a" * 20000 + "@", "12 " * 7000 + "Street",
    "A " * 10000, "Aa " * 7000 + "1", "password " * 3000, "door code " * 2000 + "1", "my address is 1" + " x" * 10000,
    "call me on " * 2000 + "1", "account number " * 1500 + "1", "x.dot " * 4000 + "@", "1-" * 10000, "(0" * 10000,
    "I live at 1 " + "word " * 5000,
    # review thread PR #2574. Each carries a DIGIT, or the scrubber's no-digit fast path skips the pattern and the case proves
    # nothing. Measured on 4500293f8 (quadratic, 4x per doubling): "my "*4000+"1" 2.5 s, "account number"+12000 spaces 8.5 s,
    # "my number"+spaces 3.9 s, "email"+spaces 3.6 s; bounded now, each under 0.2 s at these sizes
    "my " * 6000 + "1", "our new " * 3000 + "1", "my " * 6000 + "address is 1", "the home " * 3000 + "address: 1",
    "account number" + " " * 12000 + "x1", "my number" + " " * 12000 + "x1", "email" + " " * 12000 + "x dot com 1",
    "door code" + " " * 12000 + "x1", "pin" + " " * 12000 + "x1", "password" + " " * 12000 + ", 1",
    "I live at" + " " * 12000 + "1", "I live at 1" + " Aa" * 6000, "12" + " Aa" * 6000, "call" + " me" * 6000 + " on 1",
    # 2026-11-16 context checks run once per match: many bare matches, and many pin-verb matches
    "1 Aa St " * 2500, "watched 1 Aa St " * 1500, "pin 123 " * 2500, "pin 123 photos " * 1500,
    # cross-review #2657: a long blank PREFIX before many candidates (a per-candidate rescan from the start is quadratic)
    " " * 20000 + "1 Aa St " * 2500, "\n" * 20000 + "1 Aa St articles " * 1500, " " * 20000 + "pin 123 photos " * 1500,
    " " * 20000 + "pin 123 " * 2500, '"' * 10000 + '1 Aa St" ' * 2000,
    # second pass: every candidate preceded by a long run of filler words (the context window is one bounded slice)
    ("actual " * 30 + "pin 123 photos ") * 300, ("actual " * 30 + "1 Aa St articles ") * 300,
    ("x" * 500 + " pin 123 photos ") * 200, ("my, " * 40 + "1 Aa St movie ") * 300,
])
def test_a_long_adversarial_string_is_fast(blob):
    """Linear, not catastrophic: measured relative to a benign sentence of the SAME length on the same box (so a loaded CI
    runner slows both), plus a generous absolute ceiling. Measured 2026-11-15: worst blob ~64 ms; a backtracking trap at
    this size runs for minutes."""
    benign = ("the plumber is coming on Friday about the leak in the laundry " * (len(blob) // 60 + 1))[:len(blob)]
    t0 = time.perf_counter()
    scrub_pii(benign)
    base = time.perf_counter() - t0
    t0 = time.perf_counter()
    scrub_pii(blob)
    took = time.perf_counter() - t0
    assert took < 50 * base + 0.5 and took < 10.0, (took, base)


@pytest.mark.parametrize("word", ["zebrafiddle", "quokkamarble", "Sunflowerlamp", "tangerine-cloud", "velvetkite",
                                  "marmalade7", "orbitpancake", "ziggurat"])
@pytest.mark.parametrize("form", ["my password {w}", "the password was {w}", "passphrase {w} please", "password: {w}"])
def test_any_unlisted_word_after_password_is_redacted(word, form):
    """FAIL-CLOSED: the token after "password" is a value unless it is on the closed non-secret list (``_PASSWORD_NOT_VALUES``),
    so an invented word nobody listed is always redacted."""
    import memory_service

    assert word.lower() not in memory_service._PASSWORD_NOT_VALUES
    out, reject = scrub_pii(form.format(w=word))
    assert reject is None and word not in out and R in out
