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
        ("my password is on the fridge: zebrafiddle", "zebrafiddle"),   # the first word goes AND the value after the colon
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
    "the alarm code changed yesterday", "the wifi password changed", "pin it to the board",
    "pin 3 photos to the fridge", "the zip code", "reset my password", "the password reset tomorrow",
    # the closed list after "password": the sentence is ABOUT the password, it holds no value
    "I need a password manager", "the password reset link expired", "type it in the password field",
    "our password policy changed", "the file is password protected", "the password prompt froze", "use the password hint",
    "the password box is empty", "the password change worked", "the password rules are strict", "the password for the wifi",
    "I forgot my password.", "change your password now", "my password doesn't work",
    # (a STATE / PLACE word right after "<label> is / was" is NOT here: it is redacted, fail closed - test_state_words_*)
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


def test_a_place_word_after_password_is_is_redacted_fail_closed():
    """FAIL CLOSED (review of #2660, as main does): the word right after "password is" is ALWAYS redacted - a password can be any
    word, "on" included - and the value runs to the clause end (a later word may be the secret: "is saved with value <x>"), so
    "The wifi password is on the fridge" is stored as "... is [REDACTED]" (words lost, accepted: docs/knowledge/open-problems.md)."""
    assert scrub_pii("The wifi password is on the fridge") == ("The wifi password is [REDACTED]", None)
    assert scrub_pii("The wifi password is zebrafiddle")[0] == "The wifi password is [REDACTED]"


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

    assert word.lower() not in memory_service._PASSWORD_NOT_VALUES
    out, reject = scrub_pii(form.format(w=word))
    assert reject is None and word not in out and R in out


#: the words the scrubber USED to excuse after "<label> is / was" (state, time, place): now every one of them is redacted there
STATE_WORDS = sorted({
    "wrong", "incorrect", "invalid", "valid", "expired", "working", "broken", "gone", "lost", "forgotten", "missing", "saved",
    "stored", "kept", "written", "hidden", "weak", "strong", "long", "short", "blank", "empty", "unset", "fine", "ok", "okay",
    "same", "yesterday", "today", "tonight", "recently", "earlier", "lately", "on", "in", "under", "behind", "inside", "with",
    "somewhere", "there", "here"})


@pytest.mark.parametrize("word", STATE_WORDS)
@pytest.mark.parametrize("form", ["my password is {w} now then", "the password was {w}", "the pin is {w}", "password: {w}",
                                  "the token = {w}"])
def test_state_words_right_after_a_label_are_redacted(word, form):
    """FAIL CLOSED (review of #2660): right after a secret label + is / was / : / = the next word is ALWAYS the value, state word
    or not ("my password is hidden" may BE the password)."""
    out, reject = scrub_pii(form.format(w=word))
    assert reject is None and word not in out.lower().split() and R in out, out


def test_no_state_word_list_is_left_to_excuse_a_value():
    import memory_service

    assert not hasattr(memory_service, "_LABELLED_NOT_VALUES") and not hasattr(memory_service, "_LABELLED_KEPT_VALUE_RE")


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


def test_every_modifier_then_a_state_word_is_redacted():
    """Exhaustive: after "is" / "was", any modifier (and two) is itself the value: it and the words after it are redacted."""
    ms = _ms()
    for mod in sorted(ms._VALUE_MODIFIERS):
        for word in STATE_WORDS:
            for text, head in ((f"my password is {mod} {word}", "my password is "), (f"the passcode was {mod} {mod} {word}", "the passcode was ")):
                out, _ = scrub_pii(text)
                assert out.startswith(head + R) and mod not in out[len(head):].split(), (text, out)


@pytest.mark.parametrize("text", [
    # modifiers only after "is" / "was": no value was said; and no connector at all (the bare label's closed list)
    "change your password now", "change your password again",
    "my password no longer works", "the password reset link expired", "the password change worked",
])
def test_modifier_forms_that_carry_no_value_are_kept(text):
    assert scrub_pii(text) == (text, None)


@pytest.mark.parametrize("text,want", [
    ("my password is still working", "my password is [REDACTED]"), ("my password is not working", "my password is [REDACTED]"),
    ("my password is now on the fridge", "my password is [REDACTED]"),
    ("my password is no longer valid", "my password is [REDACTED]"),
    ("the password was reset yesterday", "the password was [REDACTED]"),      # formerly a MUST_NOT row
])
def test_modifier_then_a_state_word_after_is_loses_the_word(text, want):
    """Formerly kept; fail closed now (review of #2660): the word after the modifiers is redacted, whatever it is."""
    assert scrub_pii(text) == (want, None)


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


# ── LINEAR on every label / connector form (bot review, PR #2574): a long run after a label never goes quadratic ──────────────

_TIMING_FORMS = [f"{label}{conn}" for label in ("password", "passcode", "passphrase", "pin", "api key", "token", "secret",
                                                "auth token")
                 for conn in (" is", " was", ":", " =", " is:", " was =")] + [
    "my password", "the password for the wifi is", "the password is still", "the password is still on the fridge",
    "the door code is", "door code", "my pin", "account number is", "bsb", "call me on", "my number is", "my email is",
    "email:", "my address is", "I live at", "we live at 12", "send it to", "+61", "0400"]
_TIMING_PADS = {"spaces": " ", "tabs": "\t", "mixed": " \t", "newlines": "\n", "is": "is ", "mods": "still ", "now": "now ",
                "fillers": "actually really just ", "quote": "\" ",
                "digits": "1-2 "}
_TIMING_BOUND_S = 0.25


def _timed(text: str) -> float:
    best = 9.0
    for _ in range(2):
        t0 = time.perf_counter()
        scrub_pii(text)
        best = min(best, time.perf_counter() - t0)
    return best


@pytest.mark.parametrize("pad", sorted(_TIMING_PADS))
def test_every_label_form_stays_linear_on_a_long_padding_run(pad):
    """For EVERY label / connector form: the form + 16000 chars of the padding completes under a fixed bound, and doubling the
    length (8000 -> 16000) does not more than ~2.5x the time (a ratio read only above 80 ms and confirmed twice, so a
    quadratic shape is caught without flaking on a loaded box). ``password is`` + 16000 spaces took 4.6 s before the connector was made
    unambiguous."""
    unit = _TIMING_PADS[pad]
    slow = []
    for form in _TIMING_FORMS:
        short, long = (form + unit * (n // len(unit)) for n in (8000, 16000))
        t_long = _timed(long)
        if t_long > _TIMING_BOUND_S:
            slow.append((form, "bound", round(t_long, 3)))
            continue
        # a ratio on a few milliseconds is noise on a loaded box (it flaked at 34 ms): the ratio is read only where a
        # quadratic shape would show (the old connector was 1.1 s -> 4.5 s here), and must hold on a second measurement
        if t_long > 0.08 and all(_timed(long) / max(_timed(short), 1e-4) > 2.5 for _ in range(2)):
            slow.append((form, "ratio", round(t_long, 3)))
    assert not slow, slow


# --- a secret with punctuation INSIDE it is redacted whole (review of #2660) ------------------------------------------------------
# The value patterns used to stop at , ; ! ? and then needed whitespace after trailing punctuation, so "abc!def" matched nothing
# (the labelled form came back unchanged) or lost only its first half (the bare and code forms left "!def" behind).
_PUNCT_FORMS = (
    "my password is {v}", "my password was {v}", "the wifi password is {v}", "password: {v}", "password = {v}", "password {v}",
    "my passcode is {v}", "the api key is {v}", "token: {v}", "my secret is {v}", "the passphrase is {v}",
    "the door code is {v}", "the alarm code is {v}", "the gate code: {v}", "the safe combination is {v}",
    "the password for the wifi is {v}", "my pin is {v}",
)
_PUNCT_VALUES = ("qzx7!wkv9", "qzx7,wkv9", "qzx7;wkv9", "qzx7?wkv9", "qzx7!wkv9,jbd4", "7qzx,9wkv", "qzx7!!wkv9", "qzx7,wkv9.")
_PUNCT_TAILS = ("", ".", "!", " and the dog is fed", ", see you later")


@pytest.mark.parametrize("form", _PUNCT_FORMS)
def test_a_secret_with_punctuation_inside_is_redacted_whole(form):
    left = []
    for value in _PUNCT_VALUES:
        for tail in _PUNCT_TAILS:
            out, reject = scrub_pii(form.format(v=value) + tail)
            if reject is None and any(part in out for part in ("qzx", "wkv", "jbd")):
                left.append((value, tail, out))
    assert not left, left


def test_punctuation_after_a_secret_still_ends_it_and_the_sentence_survives():
    """Trailing punctuation followed by a space is the END of the value: the rest of the sentence is not swallowed."""
    out, _ = scrub_pii("my password is qzx7wkv9, and the bins go out on friday")
    assert "qzx7wkv9" not in out and "bins go out on friday" in out
    out, _ = scrub_pii("the door code is 4521. the plumber comes at noon")
    assert "4521" not in out and "plumber comes at noon" in out


# --- a QUOTED word after a secret label is always the value (review of #2660: a regression against main) -------------------------
@pytest.mark.parametrize("said", [
    'my password is "on"', 'my password is "hidden"', "my password is 'wrong'", 'my password was "gone"',
    'my password is “valid”', 'my passcode is "here"', 'the api key is "missing"', 'my password is still "valid"',
    'the password for the wifi is "under"', 'password "on"', 'my password "hidden"',
])
def test_a_quoted_state_word_after_a_secret_label_is_redacted(said):
    out, reject = scrub_pii(said)
    assert reject is None and "[REDACTED]" in out, out
    for word in ("on", "hidden", "wrong", "gone", "valid", "here", "missing", "under"):
        assert f'"{word}"' not in out and f"'{word}'" not in out and f"“{word}”" not in out, out


def test_a_quoted_value_of_several_words_is_redacted_through_its_closing_quote():
    out, _ = scrub_pii('my password is "qzx plum wkv" and the dog is fed')
    assert not any(w in out for w in ("qzx", "plum", "wkv")) and "the dog is fed" in out
    out, _ = scrub_pii('my password is "qzx plum wkv')            # never closed: the whole slot goes
    assert not any(w in out for w in ("qzx", "plum", "wkv"))


@pytest.mark.parametrize("said,gone", [
    ("my password is on the fridge", "on"), ("my password is hidden", "hidden"), ("my password was wrong", "wrong"),
    ("my password is still valid", "valid"),
])
def test_the_same_state_words_unquoted_are_redacted_too(said, gone):
    out, _ = scrub_pii(said)
    assert gone not in out.split() and R in out, out


# --- filler words between the connector and a code (review of #2660) ---------------------------------------------------------
@pytest.mark.parametrize("fill", ["actually", "now", "just", "still", "currently", "now still", "actually just"])
@pytest.mark.parametrize("form", ["the door code is {f} 5791", "the alarm code was {f} 5791", "the gate code: {f} 5791",
                                  "the code for the safe is {f} 5791"])
def test_a_code_said_after_a_filler_word_is_redacted(form, fill):
    out, _ = scrub_pii(form.format(f=fill))
    assert "5791" not in out, out


def test_a_word_after_a_code_label_and_is_goes_whatever_it_is():
    """The class rule (review, PR #2660): no shape is required of a labelled secret, so "broken" / "green" go as a value would."""
    assert scrub_pii("the door code is actually broken")[0] == "the door code is [REDACTED]"
    assert scrub_pii("the gate code is green")[0] == "the gate code is [REDACTED]"


# ── a quoted value runs to its closing quote (or the end of the clause), with NO token cap (review of #2660) ──────────────────────

_WORDS20 = ("amber birch cobalt dune ember fjord garnet heron iris juniper kelp lumen moss nectar opal pine quartz reed "
            "sable thistle").split()
_QUOTE_PAIRS = [('"', '"'), ("'", "'"), ("“", "”"), ("‘", "’")]
_QUOTED_FORMS = ["my password is {v}", "the passcode was {v}", "my secret: {v}", "my password {v}", "the passphrase = {v}",
                 "the password for the wifi is {v}", "the door code is {v}", "the alarm code: {v}"]


@pytest.mark.parametrize("n", [9, 20])
@pytest.mark.parametrize("qo,qc", _QUOTE_PAIRS)
@pytest.mark.parametrize("form", _QUOTED_FORMS)
def test_a_long_quoted_value_loses_every_word_closed_or_not(form, qo, qc, n):
    words = _WORDS20[:n]
    for value, tail in ((qo + " ".join(words) + qc, " and then we left"), (qo + " ".join(words), ". Then we left")):
        out, _ = scrub_pii(form.format(v=value) + tail)
        assert not any(w in out for w in words), (form, value, out)
        assert "left" in out and R in out                              # the rest of the sentence after the quote is kept


def test_an_apostrophe_inside_a_quoted_value_does_not_close_it():
    out, _ = scrub_pii("my password is 'my dog's name is pickle' ok")
    assert "pickle" not in out and "name" not in out and out.endswith(" ok"), out


# ── any number of filler words before a code (review of #2660): a closed list, repeatable, linear ──────────────────────────────

_FILLERS = ["actually", "really", "just", "now", "still", "currently", "definitely", "only"]
_CODE_FORMS = ["the door code is {f}5791", "the alarm code was {f}5791", "the gate code: {f}5791", "the code for the safe is {f}5791",
               "the wifi password is {f}plum-ladder-9", "the garage pin = {f}5791", "my padlock combination is {f}5791"]


@pytest.mark.parametrize("k", [0, 1, 2, 3, 4])
@pytest.mark.parametrize("form", _CODE_FORMS)
def test_zero_to_four_fillers_before_a_code_on_every_code_form(form, k):
    fill = "".join(f + " " for f in _FILLERS[:k])
    out, _ = scrub_pii(form.format(f=fill))
    assert "5791" not in out and "plum-ladder-9" not in out, out


# ── review round (2026-11-16, PR #2574's copy of this scrubber; the same classes here) ───────────────────────────────────────

@pytest.mark.parametrize("text", [
    "call me on 0400 555 101. 9555 0123",
    "call me on 0400 555 101, 9555 0123",
    "call me on 0400 555 101 or 9555 0123",
    "call me on 0400 555 101 and 9555 0123",
    "call me on 0400 555 101\n9555 0123",
    "call me on 0400 555 101; 9555 0123 after five",
    "call me on 0400 555 101. 9555 0123. 9555 0456",
    "call me on 0400 555 101, 9555 0123 or 9555 0456",
    "my email is sam@example.com. 9555 0123",
    "email me at sam@example.com or 9555 0123",
])
def test_every_contact_number_after_the_first_is_redacted(text):
    """A second (and Nth) number said straight after a redacted contact detail is redacted too: the split phone pass ends the
    first at the sentence end and the label the wide fallback needs is gone by then. Fails on f5a00ecbf."""
    out, reject = scrub_pii(text)
    assert reject is None
    for secret in ("9555 0123", "9555 0456", "0400 555 101", "sam@example.com"):
        assert secret not in out, (text, out)


@pytest.mark.parametrize("text", [
    "call me on 0400 555 101. I have 2 kids", "call me on 0400 555 101, 3 times a day", "call me on 0400 555 101 in 2026",
])
def test_a_short_number_after_a_contact_detail_is_kept(text):
    """The chain takes only phone-sized runs (8+ digits): a count or a year after a redacted number stays."""
    out, _ = scrub_pii(text)
    assert out.startswith("call me on " + R) and out.endswith(text.split("101", 1)[1]), out


@pytest.mark.parametrize("text", [
    "my password is same as {w}", "my password is the same as {w}", "my password same as {w}", "my password the word {w}",
    "my password is saved as {w}", "my password is written like {w}", "my password will be {w}", "my password should be {w}",
    "the password has been {w}", "the pin and password are {w}", "my password isn't {w} any more", "the password I use is {w}",
    "the password that you gave me was {w}", "my password this {w}",
])
@pytest.mark.parametrize("value", ["zebrafiddle", "quokkamarble"])
def test_value_introducing_wordings_are_redacted(text, value):
    """Wordings that INTRODUCE the value after a secret label (a following "as" / "like", "will be", "has been", a relative
    clause): the value goes, not only the first word. Fails on f5a00ecbf for most rows."""
    out, _ = scrub_pii(text.format(w=value))
    assert value not in out and R in out, out


def test_the_contact_chain_stays_linear_on_long_padding():
    base_text = "call me on 0400 555 101" + ". 9555 0123" * 50
    t0 = time.perf_counter()
    scrub_pii(base_text)
    base = time.perf_counter() - t0
    long_text = "call me on 0400 555 101" + ". 9555 0123" * 2000 + " ." * 5000
    t0 = time.perf_counter()
    out, _ = scrub_pii(long_text)
    took = time.perf_counter() - t0
    assert "9555" not in out
    assert took < 50 * base + 0.5 and took < 10.0, (took, base)


@pytest.mark.parametrize("text", [
    "call me on 0400 555 101, or {n}", "call me on 0400 555 101.    {n}", "call me on 0400 555 101; and {n}",
])
def test_a_chained_number_after_punctuation_and_a_conjunction_is_redacted(text):
    out, _ = scrub_pii(text.format(n="9555 0123"))
    assert "9555 0123" not in out and "0400 555 101" not in out, out


@pytest.mark.parametrize("text", [
    "my password is saved under the name {w}", "my password is saved, as {w}", "the password I use will be {w}",
    "my password is same as my very favourite old childhood nickname {w}", "my password is not working but was {w}",
    "my password the word my very favourite old childhood nickname {w}",
])
def test_a_value_later_in_the_clause_is_redacted(text):
    """Cross-review round: the value introduced later in the clause, or after more than six words, goes too."""
    out, _ = scrub_pii(text.format(w="zebrafiddle"))
    assert "zebrafiddle" not in out and R in out, out


@pytest.mark.parametrize("text,kept", [
    ("my password is the word quokkamarble and I am allergic to peanuts", "and I am allergic to peanuts"),
    ("password the word quokkamarble and we left early", "and we left early"),
])
def test_an_introduced_value_ends_where_a_new_clause_starts(text, kept):
    out, _ = scrub_pii(text)
    assert "quokkamarble" not in out and out.endswith(kept), out


@pytest.mark.parametrize("text", [
    "call me on 0400555101. 12", "call me on 0400555101. 1234", "call me on 0400 555 101. 9555 0123",
    "call me on 0400. 555 101", "ring the shop on 0400 555 101. 0400 555 202", "call me on +61 400 555 101. 9555 0123",
])
def test_the_wide_phone_shape_is_unioned_with_the_split_one(text):
    """Review (PR #2574): the wide shape runs on the SAME text as the split shapes and the spans are unioned - a fallback on text
    a split pass already replaced lost its label and freed the rest ("call me on 0400555101. 12" kept "12")."""
    out, _ = scrub_pii(text)
    assert not any(c.isdigit() for c in out.replace("+", "")), out


@pytest.mark.parametrize("text", [
    "my password is written as {w}", "my password is spelled {w}", "my password is spelt {w}", "the password reads {w}",
    "my password is written {w}", "my password is kept as {w}", "my password is stored as {w}", "my password is hidden as {w}",
    "my password written as {w}", "the pin is saved as {w}",
])
def test_a_state_word_followed_by_its_value_is_redacted(text):
    """Review (PR #2574): "written", "saved", "stored", "kept", "hidden" ... can be followed by the value ("is written as X",
    "is spelled X"): the value after them is redacted."""
    out, _ = scrub_pii(text.format(w="zebrafiddle"))
    assert "zebrafiddle" not in out and R in out, out


@pytest.mark.parametrize("w", ["still", "now", "reset", "not", "to"])
def test_a_modifier_that_is_the_whole_value_is_redacted(w):
    """Review (PR #2660): a word on the modifier list can itself be the password; with nothing after it, it is the value."""
    out, _ = scrub_pii(f"my password is {w}")
    assert out == f"my password is {R}", out


@pytest.mark.parametrize("w", ["still", "now", "reset", "not", "to"])
@pytest.mark.parametrize("tail", ["", ", please remember it", " and my email is fine"])
def test_no_word_after_a_secret_label_is_excused_as_a_modifier(w, tail):
    """Review (PR #2660): the modifier exemption is gone - the first word after the connector is the value, whatever it is."""
    out, _ = scrub_pii(f"my password is {w}{tail}")
    assert out.startswith(f"my password is {R}") and f"is {w}" not in out, out


@pytest.mark.parametrize("value", ["AB12CD3456", "AB1234CD5678", "AB-12-CD-34"])
def test_a_labelled_id_number_goes_as_a_whole_token(value):
    """Review (PR #2660): letters and digits in any order, dash-joined: the WHOLE token after the label, never a part of it."""
    assert scrub_pii(f"passport number {value}")[0] == f"passport number {R}"
    assert scrub_pii(f"my passport number is {value} ok")[0] == f"my passport number is {R} ok"


_LABELLED_ANY = ["the wifi key is {v}", "my pin is {v}", "the door code is {v}", "the alarm code: {v}", "my passcode is {v}",
                 "the safe code was {v}", "the security code for the gate is {v}", "the network key = {v}"]


@pytest.mark.parametrize("form", _LABELLED_ANY)
@pytest.mark.parametrize("value", ["zebrafiddle", "12 34", "1 2 3 4", "twelve thirty-four", "Tr0ub4dor&3"])
@pytest.mark.parametrize("tail", ["", ". The dog is fed", " and I am tired"])
def test_a_labelled_secret_needs_no_shape(form, value, tail):
    """Review (PR #2660), the class rule: after a secret label and its connector the value is redacted whatever its characters."""
    out, _ = scrub_pii(form.format(v=value) + tail)
    assert R in out and not any(part in out.split() for part in value.split()), out
    if tail.startswith("."):
        assert tail.strip(" .") in out, out                  # the next sentence is never taken


@pytest.mark.parametrize("text,want", [
    ("my pin 12 34", "my pin [REDACTED]"), ("pin 1 2 3 4", "pin [REDACTED]"), ("wifi key zebrafiddle", "wifi key [REDACTED]"),
])
def test_a_secret_said_without_a_connector_goes_too(text, want):
    assert scrub_pii(text) == (want, None)


@pytest.mark.parametrize("text", ["the alarm code changed", "the dress code is casual", "pin 3 photos to the board",
                                  "the wifi password works", "I lost the door key"])
def test_talk_about_a_code_without_a_value_is_kept(text):
    assert scrub_pii(text) == (text, None)


@pytest.mark.parametrize("text", [
    "passwords are ZorbLime", "api keys are ZorbLime", "PINS are 1 2 3 4", "door codes are ZorbLime", "api key ZorbLime",
    "token's ZorbLime", "secret's ZorbLime", "passcode seven two nine four", "token - ZorbLime", "passcode - seven two nine four",
    "door code - 12 34", "network key - ZorbLime", "door code is         ZorbLime", "combination is 12 34",
    'combination is "violet, otters drift"', 'password will be "violet, otters drift"', 'password has been "violet and the otters drift"',
    'password is "s amber, river"', "password's !@#$%^&*", "wifi key !@#$%^&*", "pass-word is ZorbLime", "pass\nword is ZorbLime",
])
def test_the_net_takes_every_labelled_secret_the_grammar_missed(text):
    """An adversarial pass over the label path (PR #2660): plural labels, possessive and dash connectors, long gaps, a bare
    "combination", quotes after the newer connectors, symbol-leading values, split labels - nothing of the value is left."""
    out, _ = scrub_pii(text)
    assert out.endswith(R) and not any(w in out for w in ("Zorb", "violet", "otters", "amber", "river", "seven", "nine", "12", "1 2", "!@#")), out


def test_the_net_is_linear_on_long_input():
    import time
    for big in ("password is " + " " * 20000 + "x", "pin " * 5000, "password " + "1 " * 10000, "door code is " + "a," * 10000,
                "passwords are " + "\"" + "a " * 10000):
        t0 = time.monotonic()
        scrub_pii(big)
        assert time.monotonic() - t0 < 2.0


def test_many_labelled_secrets_in_one_text_stay_linear():
    """Review (PR #2660): the net never rescans from the top; 1,600 labelled secrets in one text are scrubbed in one pass."""
    import time
    big = "password is abc. " * 1600
    scrub_pii("password is abc. " * 50)                      # warm the patterns
    t0 = time.monotonic()
    out, _ = scrub_pii(big)
    took = time.monotonic() - t0
    assert "abc" not in out and took < 0.5, took


@pytest.mark.parametrize("text,gone", [
    ("my BSB is 062\t000", ("062", "000")), ("my account number is 1234\t5678", ("1234", "5678")),
    ("my account number is 1234\n5678", ("1234", "5678")), ("my pin 12\t34", ("12", "34")), ("the door code is 57\t91", ("57", "91")),
])
def test_digit_groups_joined_by_any_whitespace_go_together(text, gone):
    out, _ = scrub_pii(text)
    assert R in out and not any(g in out for g in gone), out


@pytest.mark.parametrize("text", [
    "my password changed to zebrafiddle", "my password updated to zebrafiddle", "my password reset to zebrafiddle",
    "the wifi key changed to zebrafiddle", "my pin changed to 12 34", "the door code was set to zebrafiddle",
    "my password has been changed back to zebrafiddle",
])
def test_changed_to_introduces_the_value(text):
    """Review (PR #2574 / #2660): "<label> changed / updated / reset / set to <value>" - "to" is a connector here."""
    out, _ = scrub_pii(text)
    assert out.endswith(R) and "zebrafiddle" not in out and "12 34" not in out, out


@pytest.mark.parametrize("text", ["my password changed", "the password reset link expired"])
def test_changed_without_a_value_is_kept_as_said(text):
    assert scrub_pii(text) == (text, None)
    assert scrub_pii("my password changed to zebrafiddle")[0] == "my password changed to [REDACTED]"   # control: with a value


@pytest.mark.parametrize("text,gone", [
    ("my BSB is 062 - 000", ("062", "000")), ("my account number is 1234   5678", ("1234", "5678")),
    ("my account number is 1234 -  5678", ("1234", "5678")), ("the door code is" + " " * 41 + "5791", ("5791",)),
    ("the door code is" + " " * 500 + "5791", ("5791",)), ("the wifi key is" + " " * 500 + "zebrafiddle", ("zebrafiddle",)),
    ("my pin" + " " * 60 + "12 - 34", ("12", "34")), ("password" + "\t" * 50 + "is" + "\t" * 50 + "zebrafiddle", ("zebrafiddle",)),
])
def test_no_count_of_spaces_between_label_connector_value_or_groups_lets_a_secret_through(text, gone):
    """Review (PR #2660), the class: whitespace runs are folded before judging, so there is no limit to count past."""
    out, reject = scrub_pii(text)
    assert reject is None and R in out and not any(g in out for g in gone), out


def test_a_text_with_nothing_to_redact_keeps_its_spacing_exactly():
    said = "we  walked\tto the   shops\nand  back"
    assert scrub_pii(said) == (said, None)


def test_ten_thousand_spaces_stay_linear():
    import time
    for big in ("the door code is" + " " * 10000 + "5791", "my account number is 1" + " 2" * 10000, "password " + " - " * 5000 + "x"):
        t0 = time.monotonic()
        scrub_pii(big)
        assert time.monotonic() - t0 < 0.5


@pytest.mark.parametrize("text,gone,kept", [
    ("my pin is 12\n34", ("12", "34"), ""), ("the door code is 57\n91", ("57", "91"), ""),
    ("my pin is 12\n34\n56", ("12", "34", "56"), ""), ("the safe code: 1 2\n3 4\nthen call the plumber", ("1 2", "3 4"), "then call the plumber"),
])
def test_a_secret_carried_onto_the_next_line_goes_too(text, gone, kept):
    out, _ = scrub_pii(text)
    assert R in out and not any(g in out for g in gone) and kept in out, out


def test_a_number_on_the_line_after_ordinary_text_is_kept():
    said = "the bins go out friday\n42 is the bus"
    assert scrub_pii(said) == (said, None)


def test_many_unclosed_quotes_stay_linear():
    """Review (PR #2660): the closing quotes are indexed once; 8,000 unclosed quoted values are not 8,000 scans of the text."""
    import time
    big = 'password is "abc.\n' * 8000
    t0 = time.monotonic()
    out, _ = scrub_pii(big)
    assert time.monotonic() - t0 < 0.5 and "abc" not in out


def test_a_card_split_over_lines_is_still_a_card():
    assert scrub_pii("card 4111\n1111\n1111\n1111", contact=False)[1] == "luhn_cc"


def test_single_digits_one_per_line_after_a_pin_all_go():
    out, _ = scrub_pii("my pin is 1\n2\n3\n4")
    assert out == "my pin is [REDACTED]", out


@pytest.mark.parametrize("value", ["zebra!fiddle", "zebra;fiddle", "zebra?fiddle", "zebra,fiddle", "zebra.fiddle"])
@pytest.mark.parametrize("form", ["my password is {v}", "the wifi key is {v}", "my password {v}", "password: {v}"])
def test_punctuation_inside_a_labelled_value_goes_with_it(form, value):
    """Review (PR #2574): a labelled value is everything to the boundary, punctuation included."""
    out, _ = scrub_pii(form.format(v=value))
    assert out.endswith(R) and "zebra" not in out and "fiddle" not in out, out


@pytest.mark.parametrize("text,gone", [
    ("my account number is actually 12345678", ("12345678",)), ("my passport number is actually AB1234567", ("AB1234567",)),
    ("my BSB is now 062 000", ("062", "000")), ("my account number - 12345678", ("12345678",)),
    ("my pin is 12\n34\r\n56", ("12", "34", "56")), ("my pin is 1\n2\n3\n4\n5\n6\n7\n8\n9\n0", ("\n0", "\n9")),
])
def test_fillers_dash_connectors_crlf_and_long_line_runs(text, gone):
    """Review (PR #2660; main leaks these too): fillers and a dash before a labelled ID; CRLF and any number of digit lines."""
    out, _ = scrub_pii(text)
    assert R in out and not any(g in out for g in gone), out


def test_many_digit_lines_after_a_secret_stay_linear():
    import time
    t0 = time.monotonic()
    out, _ = scrub_pii("my pin is 1" + "\n2" * 20000)
    assert time.monotonic() - t0 < 0.5 and out == "my pin is [REDACTED]"


@pytest.mark.parametrize("text,gone", [
    ("passwords is [REDACTED]zebrafiddle", ("zebrafiddle",)), ("passwords is ”plum. zebrafiddle”", ("plum", "zebrafiddle")),
    ("combinations is ’plum. zebrafiddle’", ("plum", "zebrafiddle")), ("password is 12\n" + "3" * 42, ("333",)),
    ("passwords is 12\n\n34", ("12", "34")),
])
def test_survivors_of_the_second_adversarial_pass(text, gone):
    """PR #2660, second adversarial pass: a literal marker glued to a value, right-hand quotes as openers after the net's labels,
    a long digit line, a blank line between digit lines."""
    out, _ = scrub_pii(text)
    assert R in out and not any(g in out for g in gone), out


def test_a_large_text_is_scrubbed_off_the_event_loop_and_a_short_one_inline(monkeypatch):
    """Review (PR #2660): async callers go through ``scrub_pii_async`` - a 144k text does not hold the loop (a ticker keeps
    ticking while it is scrubbed), and a short text makes no thread hop at all."""
    import asyncio
    import memory_service as ms

    hops = []
    real = asyncio.to_thread

    async def spy(fn, *a, **k):
        hops.append(len(a[0]))
        return await real(fn, *a, **k)

    monkeypatch.setattr(ms.asyncio, "to_thread", spy)

    async def run():
        ticks, done = [], asyncio.Event()

        async def ticker():
            while not done.is_set():
                ticks.append(1)
                await asyncio.sleep(0.005)

        t = asyncio.create_task(ticker())
        await asyncio.sleep(0)
        big = 'password is "abc.\n' * 8000
        before = len(ticks)
        out, _ = await ms.scrub_pii_async(big, contact=False)
        during = len(ticks) - before
        short, _ = await ms.scrub_pii_async("my password is zebrafiddle", contact=False)
        done.set()
        await t
        return out, during, short

    out, during, short = asyncio.run(run())
    assert "abc" not in out and short == "my password is [REDACTED]"
    assert hops == [len('password is "abc.\n' * 8000)], hops          # the big text hopped once; the short one never
    assert during >= 3, during                                        # the loop kept running while the big text was scrubbed


def test_ingest_and_review_scrub_through_the_async_form():
    """Structural: no async method of MemoryService calls the blocking ``scrub_pii`` directly."""
    import ast
    import memory_service as ms

    tree = ast.parse(open(ms.__file__, encoding="utf-8").read())
    bad = [(fn.name, c.lineno) for cls in tree.body if isinstance(cls, ast.ClassDef) and cls.name == "MemoryService"
           for fn in ast.walk(cls) if isinstance(fn, ast.AsyncFunctionDef)
           for c in ast.walk(fn) if isinstance(c, ast.Call) and getattr(c.func, "id", "") == "scrub_pii"]
    assert not bad, bad


def test_the_proposals_endpoint_refuses_an_oversized_body(monkeypatch):
    import asyncio
    from fastapi import HTTPException
    from models import MemoryProposalCreate
    from routers import memories

    async def allowed(*a, **k):
        return None

    monkeypatch.setattr(memories, "require_feature_access", allowed)
    monkeypatch.setattr(memories, "_svc", lambda: (_ for _ in ()).throw(AssertionError("ingest must not be reached")))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(memories.create_memory_proposal(MemoryProposalCreate(content="x" * (memories.PROPOSAL_MAX_CHARS + 1)),
                                                    user={"user_id": "member-px"}, db=None))
    assert exc.value.status_code == 413
