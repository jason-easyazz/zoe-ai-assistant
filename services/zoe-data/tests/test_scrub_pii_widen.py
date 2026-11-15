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
        # "<label> is / was": default redact, an introducer takes the words after it (invented words)
        ("my password is zebrafiddle", "zebrafiddle"),                   # already caught before 2026-11-15
        ("my password is the word zebrafiddle", "zebrafiddle"),
        ("my password is my dog's name", "dog's"),
        ("the passcode is 4821", "4821"),                                # already caught before 2026-11-15
        ("the password was zebrafiddle", "zebrafiddle"),
        ("password is: zebrafiddle", "zebrafiddle"),
        ("my password is on the fridge: zebrafiddle", "zebrafiddle"),   # a kept state word, then the value after a colon
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
    # "<label> is / was" + a STATE / PLACE / negation word (``_LABELLED_NOT_VALUES``): about the password, not its value
    "my password is not working", "The wifi password is on the fridge", "the password is wrong",
    "the password is written on the fridge",
    # email / address concepts
    "send an email to the school", "I'll email you", "down the road", "we walked 5 km down the road",
    "she lives at the end of the street", "I live at home", "the road was closed", "my address is changing",
    "the IP address is 10.0.0.1", "account was hacked", "my bank account has 1200 in it", "my bank account is overdrawn",
    "it was a 5 minute walk", "look at the way she smiled",
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
    ("", ("", None)),
])
def test_existing_behaviour_is_unchanged(text, want):
    assert scrub_pii(text) == want


def test_a_place_word_after_password_is_is_now_kept_a_deliberate_change():
    """A DELIBERATE CHANGE (2026-11-16), not existing behaviour: the old scrubber redacted ANY word after "password is", so
    "The wifi password is on the fridge" was stored as "... is [REDACTED] the fridge" - the meaning changed. Now a state or place
    word after "<label> is / was" (``_LABELLED_NOT_VALUES``) is not a value and is kept; everything else is still redacted."""
    assert scrub_pii("The wifi password is on the fridge") == ("The wifi password is on the fridge", None)
    assert scrub_pii("The wifi password is zebrafiddle")[0] == "The wifi password is [REDACTED]"     # control: a value still goes


# ── the callers' contract: the night mind's verbatim gate rejects any changed text ─────────────────────────────────

@pytest.mark.parametrize("text", ["call me on 0400 555 101", "the door code is 5791", "my pin 4821",
                                  "we live at 12 Example Street", "write to ottilie.grey@example.com"])
def test_the_night_quote_gate_refuses_the_new_classes(text):
    import night_mind as nm

    assert nm._unstorable(text) is True


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
    # contact run-ons (2026-11-16): the bounded postcode follow-on groups and the phone separator
    "1 Aa St, 1234 " * 2000, "we live at 1 a st and 1234" + " 1" * 6000, "1 Aa St 1234" + "-1" * 8000,
    "0400 555 101. " * 3000, "+61 4. " * 4000, "+61 4" + " 5" * 8000, "call me on 0400" + " 5" * 8000,
    "call me on " + "1." * 8000, "call me on 1" + " - 2" * 4000, "my number is 1" + "(2)" * 5000,
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
@pytest.mark.parametrize("form", ["my password {w}", "the password was {w}", "passphrase {w} please", "password: {w}",
                                  "my password is {w}", "the passcode is {w} ok", "the token was {w}"])
def test_any_unlisted_word_after_password_is_redacted(word, form):
    """FAIL-CLOSED: the token after "password" is a value unless it is on the closed non-secret list (``_PASSWORD_NOT_VALUES``),
    so an invented word nobody listed is always redacted."""
    import memory_service

    assert word.lower() not in memory_service._PASSWORD_NOT_VALUES and word.lower() not in memory_service._LABELLED_NOT_VALUES
    out, reject = scrub_pii(form.format(w=word))
    assert reject is None and word not in out and R in out


def _labelled_not_values():
    import memory_service

    # collection must not depend on the list existing (the old-code control collects this file): a missing list fails the test
    return sorted(getattr(memory_service, "_LABELLED_NOT_VALUES", None) or {"not", "on", "wrong"})


@pytest.mark.parametrize("word", _labelled_not_values())
@pytest.mark.parametrize("form", ["my password is {w} today", "the password was {w}", "the pin is {w}"])
def test_every_listed_state_word_after_is_or_was_is_left_alone(word, form):
    """The closed list after "<label> is / was" is real: each word on it is kept (the sentence is about the secret). After ":" or
    "=" no word is excused (a colon always introduces a value)."""
    text = form.format(w=word)
    assert scrub_pii(text) == (text, None)
    assert word not in scrub_pii(f"password: {word}")[0].split(": ", 1)[1]


def test_no_introducer_is_on_the_kept_list():
    """The is / was list must not excuse a word that INTRODUCES a value ("is the word ...", "is my dog's name")."""
    import memory_service

    assert not memory_service._LABELLED_NOT_VALUES & memory_service._LABELLED_INTRODUCERS
    assert not memory_service._LABELLED_NOT_VALUES & {"the", "a", "an", "my", "our", "your", "his", "her", "their"}


# ── one match never swallows the next label (cross-review, PR #2574): every secret of a multi-secret sentence goes ──────────────

_LABELS = ["password", "passcode", "pin", "token", "secret", "api key"]
_CONNECTORS = [" is", " was", ":", " ="]


def _labelled_rows() -> list:
    """(sentence, secret) for every label x connector x (plain value | an introducer "the word <value>"); invented words."""
    out, n = [], 0
    for label in _LABELS:
        for conn in _CONNECTORS:
            for intro in ("", "the word "):
                n += 1
                value = f"zq{n:02d}vex"
                out.append((f"my {label}{conn} {intro}{value}", value))
    return out


LABELLED = _labelled_rows()
_JOINS = [" and ", ", ", ". "]


@pytest.mark.parametrize("a,b", [
    ("token is alpha", "secret is bravo"), ("my password is the word alpha", "pin is 4455"),
    ("password: alpha", "api key = bravo"), ("the passcode was alpha", "the token is the word bravo"),
    ("secret = alpha", "my password was bravo"), ("my api key is alpha", "the passcode: bravo"),
])
@pytest.mark.parametrize("join", _JOINS)
def test_two_labelled_secrets_in_one_sentence_both_go(a, b, join):
    out, _ = scrub_pii(a + join + b)
    assert "alpha" not in out and "bravo" not in out, out
    out, _ = scrub_pii(b + join + a)                                    # and in the other order
    assert "alpha" not in out and "bravo" not in out, out


def test_three_labelled_secrets_in_one_sentence_all_go():
    for text in ("token is alpha, secret was the word bravo and my password is charlie",
                 "pin = 4455 and the passcode is the word bravo. api key: charlie"):
        out, _ = scrub_pii(text)
        assert not {"alpha", "bravo", "charlie", "4455"} & set(out.replace(".", " ").replace(",", " ").split()), out


@pytest.mark.parametrize("join", _JOINS)
def test_any_two_and_three_labelled_rows_joined_lose_every_secret(join):
    """PROPERTY over the generated labelled table: every pair, and a fixed spread of triples, joined - no secret survives."""
    n = len(LABELLED)
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            (ta, sa), (tb, sb) = LABELLED[i], LABELLED[j]
            out, _ = scrub_pii(ta + join + tb)
            assert sa not in out and sb not in out, (ta, tb, out)
        rows = [LABELLED[i], LABELLED[(i * 7 + 3) % n], LABELLED[(i * 13 + 5) % n]]
        out, _ = scrub_pii(join.join(t for t, _s in rows))
        assert not any(sec in out for _t, sec in rows), (rows, out)


def _all_must_rows() -> list:
    return [(t, s) for table in (SECRET_CATCH, CONTACT_CATCH) for rows in table.values() for t, s in rows] + LABELLED


#: run-ons between two CONTACT patterns that still leak. EMPTY since 2026-11-16 (the postcode takes the digit groups that follow
#: it, ``memory_service._POSTCODE``; a phone never crosses ". " or a line break, ``_PHONE_SEP``). Kept as a ratchet: a new
#: leaking pair fails the test; it must stay empty.
_KNOWN_CONTACT_RUN_ONS: set = set()
_FIXED_RUN_ONS_2026_11_16 = {
    (" and ", "we live at 12 Example Street", "07700 900123 is my cell"),
    (" and ", "I live at 33 sample road", "07700 900123 is my cell"),
    (" and ", "she lives at 5 placeholder close, springfield", "07700 900123 is my cell"),
    (", ", "we live at 12 Example Street", "07700 900123 is my cell"),
    (", ", "the party is at 7 Sample Ave.", "07700 900123 is my cell"),
    (", ", "Unit 3, 18 Placeholder Crescent", "07700 900123 is my cell"),
    (", ", "it's 221B Example Lane", "07700 900123 is my cell"),
    (", ", "I live at 33 sample road", "07700 900123 is my cell"),
    (", ", "she lives at 5 placeholder close, springfield", "07700 900123 is my cell"),
    (", ", "meet at 12 St Example Terrace", "07700 900123 is my cell"),
    (". ", "the office is +61-8-5550-1303", "020 7946 0958 is the London line"),
    (". ", "the office is +61-8-5550-1303", "07700 900123 is my cell"),
    (". ", "the plumber's number is 5550 1234", "020 7946 0958 is the London line"),
    (". ", "the plumber's number is 5550 1234", "07700 900123 is my cell"),
    (". ", "call mum on 9555 0123", "020 7946 0958 is the London line"),
    (". ", "call mum on 9555 0123", "07700 900123 is my cell"),
    (". ", "we live at 12 Example Street", "07700 900123 is my cell"),
    (". ", "Unit 3, 18 Placeholder Crescent", "07700 900123 is my cell"),
    (". ", "it's 221B Example Lane", "07700 900123 is my cell"),
    (". ", "I live at 33 sample road", "07700 900123 is my cell"),
    (". ", "meet at 12 St Example Terrace", "07700 900123 is my cell"),
}


@pytest.mark.parametrize("join", _JOINS)
def test_the_whole_must_table_joined_pairwise_loses_every_secret(join):
    """After ALL passes (``scrub_pii`` order), any two MUST rows joined: neither secret survives (a pair is skipped only when one
    row's secret is literally part of the other row's text, or the joined text is rejected outright). Every pair that involves a
    SECRET or LABELLED row must pass; the only leaking pairs allowed are the pinned pre-existing contact run-ons."""
    rows = _all_must_rows()
    leaks = set()
    for ta, sa in rows:
        for tb, sb in rows:
            if ta == tb or sa in tb or sb in ta:
                continue
            out, reject = scrub_pii(ta + join + tb)
            if not reject and (sa in out or sb in out):
                leaks.add((join, ta, tb))
    assert leaks == {k for k in _KNOWN_CONTACT_RUN_ONS if k[0] == join}, sorted(leaks ^ _KNOWN_CONTACT_RUN_ONS)


def test_the_run_on_ratchet_is_empty():
    assert not _KNOWN_CONTACT_RUN_ONS


#: two or three contact details in a row: (text, every value that must be gone)
RUN_ON_CATCH: list[tuple[str, list[str]]] = [
    # an address followed by a phone number, an account number, a labelled secret
    ("we live at 12 Example Street 0400 555 101", ["12 Example Street", "555 101"]),
    ("it's 221B Example Lane, 07700 900123 is my cell", ["221B Example Lane", "07700", "900123"]),
    ("drop it at 4 Example Road, Springfield 6000 0400 555 101", ["4 Example Road", "555 101"]),
    ("we live at 12 Example Street and 9555 0123 is the landline", ["12 Example Street", "9555", "0123"]),
    ("drop it at 4 Example Road, Springfield 6000 and my account number is 12345678", ["4 Example Road", "12345678"]),
    ("we live at 12 Example Street, door code 5791", ["12 Example Street", "5791"]),
    ("I live at 33 sample road 6000, the alarm code is 4821", ["33 sample road", "4821"]),
    # two phone numbers across sentence punctuation or a line break
    ("call me on 0400 555 101. 0400 555 202 is the backup", ["555 101", "555 202"]),
    ("the office is +61 400 555 101. 020 7946 0958 is the London line", ["400 555 101", "7946 0958"]),
    ("my number is 5550 1234; 07700 900123 is my cell", ["5550 1234", "900123"]),
    ("call me on 0400 555 101! 0400 555 202 also works", ["555 101", "555 202"]),
    ("is it 0400 555 101? 0400 555 202 maybe", ["555 101", "555 202"]),
    ("call me on 0400 555 101\n0400 555 202 is the backup", ["555 101", "555 202"]),
    ("+61 400 555 101\n+61 400 555 202", ["400 555 101", "400 555 202"]),
    # two numbers back to back with only a space: the run is over one number's cap, so it is two numbers, both redacted
    ("call me on 0400 555 101 0400 555 202", ["555 101", "555 202"]),
    ("07700 900123 07700 900456 are ours", ["900123", "900456"]),
    # a number spelled with ". " inside it is still read as ONE number by the wide fallback (never freed)
    ("call me on 0400. 555 101", ["0400", "555 101"]),
    ("+61 400. 555 101", ["400", "555 101"]),
    # three in a row
    ("we live at 12 Example Street, 07700 900123 is my cell. call mum on 9555 0123",
     ["12 Example Street", "900123", "9555 0123"]),
    ("the office is +61-8-5550-1303. call mum on 9555 0123. 020 7946 0958 is the London line",
     ["5550-1303", "9555 0123", "7946 0958"]),
    ("Unit 3, 18 Placeholder Crescent, 07700 900123 is my cell and my account number is 12345678",
     ["18 Placeholder Crescent", "900123", "12345678"]),
]


@pytest.mark.parametrize("text,gone", RUN_ON_CATCH, ids=[t[:40] for t, _g in RUN_ON_CATCH])
def test_contact_details_in_a_row_all_go(text, gone):
    out, reject = scrub_pii(text)
    assert reject is None and not [g for g in gone if g in out], out


@pytest.mark.parametrize("join", _JOINS)
def test_contact_rows_joined_in_triples_lose_every_value(join):
    """PROPERTY over the CONTACT table: every ordered pair (i, j) plus a third row spread from them, joined three in a row."""
    rows = [(t, s) for table in (CONTACT_CATCH,) for rs in table.values() for t, s in rs]
    n, leaks = len(rows), []
    for i in range(n):
        for j in range(n):
            k = (i * 7 + j * 3 + 1) % n
            trio = [rows[i], rows[j], rows[k]]
            if len({t for t, _s in trio}) < 3 or any(sa in tb for ta, sa in trio for tb, _sb in trio if ta != tb):
                continue
            out, reject = scrub_pii(join.join(t for t, _s in trio))
            if not reject and any(sec in out for _t, sec in trio):
                leaks.append((join, [t for t, _s in trio], out))
    assert not leaks, leaks[:3]


@pytest.mark.parametrize("join,a,b", sorted(_FIXED_RUN_ONS_2026_11_16))
def test_the_21_former_run_on_pairs_lose_both_values(join, a, b):
    secret = dict(_all_must_rows())
    out, reject = scrub_pii(a + join + b)
    assert reject is None and secret[a] not in out and secret[b] not in out, out


# ── MODIFIERS are skipped and the NEXT word judged (cross-review, PR #2574): "is still <value>" never keeps the value ─────────────

def _ms():
    import memory_service

    return memory_service


_INVENTED = ["zebrafiddle", "quokkamarble", "Sunflowerlamp", "tangerine-cloud", "velvetkite", "marmalade7", "orbitpancake",
             "ziggurat"]


@pytest.mark.parametrize("mod", sorted(getattr(_ms(), "_VALUE_MODIFIERS", None) or {"still", "now", "also", "too"}))
@pytest.mark.parametrize("conn", ["is", "was"])
def test_every_modifier_then_a_value_is_redacted_labelled(mod, conn):
    out, _ = scrub_pii(f"my password {conn} {mod} zebrafiddle")
    assert "zebrafiddle" not in out and R in out, out


@pytest.mark.parametrize("mod", sorted(getattr(_ms(), "_BARE_MODIFIERS", None) or ("now", "too", "please")))
def test_every_modifier_then_a_value_is_redacted_bare(mod):
    out, _ = scrub_pii(f"my password {mod} zebrafiddle")
    assert "zebrafiddle" not in out and R in out, out


def test_fail_closed_every_modifier_and_every_chain_of_two_before_an_invented_word_is_redacted():
    """PROPERTY: 1 and 2 modifiers (every pair, both orders) before each invented word, after "is" and "was" (labelled) and after a
    bare label: the word never survives."""
    ms = _ms()
    mods, bare = sorted(ms._VALUE_MODIFIERS), sorted(ms._BARE_MODIFIERS)
    for chains, forms in (([[m] for m in mods] + [[a, b] for a in mods for b in mods], ("my password is {c} {w}",
                                                                                         "the pin was {c} {w}")),
                          ([[m] for m in bare] + [[a, b] for a in bare for b in bare], ("my password {c} {w}",))):
        for chain in chains:
            for w in _INVENTED:
                for form in forms:
                    text = form.format(c=" ".join(chain), w=w)
                    out, _ = scrub_pii(text)
                    assert w not in out, (text, out)


def test_every_modifier_then_a_slot_ending_word_is_kept():
    """MUST_NOT, exhaustive: after "is" / "was", any modifier (and two) followed by a state or place word is kept as said."""
    ms = _ms()
    for mod in sorted(ms._VALUE_MODIFIERS):
        for slot in sorted(ms._LABELLED_NOT_VALUES):
            for text in (f"my password is {mod} {slot}", f"the passcode was {mod} {mod} {slot}"):
                assert scrub_pii(text) == (text, None), text


@pytest.mark.parametrize("text", [
    "my password is still working", "my password is not working", "my password is now on the fridge",
    "my password is no longer valid", "the password was reset", "change your password now", "change your password again",
    "my password no longer works", "the password reset link expired", "the password change worked",
])
def test_modifier_forms_that_carry_no_value_are_kept(text):
    assert scrub_pii(text) == (text, None)


@pytest.mark.parametrize("text,secret", [
    ("my password is still zebrafiddle", "zebrafiddle"), ("my password is now actually zebrafiddle", "zebrafiddle"),
    ("my password is not zebrafiddle any more", "zebrafiddle"), ("my password is still the word zebrafiddle", "zebrafiddle"),
    ("my password is still on the fridge: zebrafiddle", "zebrafiddle"), ("the password was changed to zebrafiddle", "zebrafiddle"),
    ("my password now zebrafiddle", "zebrafiddle"), ("my password too zebrafiddle", "zebrafiddle"),
    ("password please zebrafiddle", "zebrafiddle"), ("password: now zebrafiddle", "zebrafiddle"),
    ("the password for the wifi is still zebrafiddle", "zebrafiddle"), ("my password is new", "new"),
])
def test_modifier_forms_with_a_value_lose_it(text, secret):
    out, _ = scrub_pii(text)
    assert secret not in out and R in out, out
