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


# ── performance: compiled regexes, no backtracking trap ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("blob", [
    "0" * 20000, "1 " * 10000, "+1 " * 7000, "a@" * 10000, "a" * 20000 + "@", "12 " * 7000 + "Street",
    "A " * 10000, "Aa " * 7000 + "1", "password " * 3000, "door code " * 2000 + "1", "my address is 1" + " x" * 10000,
    "call me on " * 2000 + "1", "account number " * 1500 + "1", "x.dot " * 4000 + "@", "1-" * 10000, "(0" * 10000,
    "I live at 1 " + "word " * 5000,
])
def test_a_long_adversarial_string_is_fast(blob):
    t0 = time.perf_counter()
    scrub_pii(blob)
    assert time.perf_counter() - t0 < 1.0
