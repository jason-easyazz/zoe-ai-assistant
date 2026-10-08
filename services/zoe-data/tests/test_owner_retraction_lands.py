"""The owner's retraction of their own fact LANDS: "Good news: I no longer get the migraines since I switched to
new glasses." retires "I've been getting migraines most afternoons lately", it is not parked as a dispute.

Incident 2026-10-08 (live main 28a2d89d, day-sim 6n "do I still get migraines?" ERROR 5/5, "setup not exercised:
d2-migraine-neg (never landed)"): the log showed ``AUTHORITY_BLOCKED writer=turn_digest kind=other action=ingest`` +
MEMORY_DISPUTE_QUESTION on that turn - the digest wrote the retraction as a ``disputed`` row (never served), so the
recall packet carried neither "glasses" nor "no longer".

Root cause (reproduced with the real ``run_turn_digest`` over a fake collection, no model, no DB):
* the first migraine row is PROMOTED (the owner's one verbatim sentence entails it) and so carries user_stated power;
* the retraction must earn the same promotion (``entailing_span``) to retire it, and could not: ``_plainly_first_person``
  read the lead-in "Good news" as a CLAIM word because ``_stem("news") == _stem("new")`` (the fact says "new glasses"),
  putting a claim word before the "I"; and ``_stem("getting") == "gett"`` != ``_stem("get")`` so "has stopped getting
  migraines" never matched "I no longer get". Without the promotion the retraction is rank 3 against a rank-4 row:
  AUTHORITY_BLOCKED, disputed, not served. (Which wording the 4B writes varies run to run - "no longer gets" landed on
  2026-10-07 15:37, "has stopped getting ... since switching to new glasses" does not.)

Break-the-fix control: with ``entailing_span`` forced to None (no promotion) the same turn is parked as a dispute.
Synthetic user, real MemoryService over a fake collection.
"""
from __future__ import annotations

import asyncio

import pytest

import memory_authority as ma
import memory_digest
from test_memory_implicit_supersede import UID, _flag, _patch_llm, _svc  # noqa: F401

pytestmark = pytest.mark.ci_safe

T1 = "I've been getting migraines most afternoons lately and it's starting to worry me."
T2 = "Good news: I no longer get the migraines since I switched to new glasses."
OLD = "User has been getting migraines most afternoons lately"


def _digest_retraction(monkeypatch, fact: str):
    _flag(monkeypatch, True)
    svc, col = _svc(monkeypatch)

    async def go():
        _patch_llm(monkeypatch, [{"type": "profile", "fact": OLD}])
        await memory_digest.run_turn_digest(UID, T1, session_id="s-1")
        _patch_llm(monkeypatch, [{"type": "profile", "fact": fact}])
        return await memory_digest.run_turn_digest(UID, T2, session_id="s-1")

    res = asyncio.run(go())
    rows = {d: m for d, m in col.rows.values()}
    return res, rows


@pytest.mark.parametrize("fact", [
    "User has stopped getting migraines since switching to new glasses",
    "User is no longer getting migraines since switching to new glasses",
    "User has stopped getting migraines",
    "User no longer gets migraines since switching to new glasses",
    "User no longer gets migraines",
])
def test_the_owners_retraction_is_stored_approved_and_the_owners_word(monkeypatch, fact):
    res, rows = _digest_retraction(monkeypatch, fact)
    assert rows[fact]["status"] == "approved"                # not parked as a dispute
    assert rows[fact]["authority_class"] == ma.USER_STATED_DERIVED
    assert res.get("new", 0) >= 1


def test_negative_control_without_the_promotion_the_retraction_is_parked(monkeypatch):
    real = ma.entailing_span
    # the pre-fix behaviour: the retraction turn cannot earn the promotion the first migraine turn earned
    monkeypatch.setattr(ma, "entailing_span", lambda fact, text: None if text == T2 else real(fact, text))
    fact = "User has stopped getting migraines since switching to new glasses"
    _res, rows = _digest_retraction(monkeypatch, fact)
    assert rows[fact]["status"] == "disputed"                # the incident: AUTHORITY_BLOCKED kind=other


@pytest.mark.parametrize("fact,said,want", [
    # the lead-in label is not a claim word: "Good news" must not read as the "new" of "new glasses"
    ("User no longer gets migraines since switching to new glasses", T2, True),
    ("User is doing the Lakeside 12k in August", "Change of plan: I'm doing the Lakeside 12k in August.", True),
    # a claim word BEFORE the "I" still fails the plain-first-person test (not a lead-in colon)
    ("User lives in Perth", "Perth is where I live.", False),
])
def test_a_lead_in_label_is_not_part_of_the_claim(fact, said, want):
    assert (ma.entailing_span(fact, said) is not None) is want


@pytest.mark.parametrize("a,b", [
    ("getting", "get"), ("dropped", "drop"), ("stopped", "stop"), ("planned", "plan"), ("running", "run"),
    ("switching", "switched"),
])
def test_an_inflection_that_doubled_the_consonant_stems_to_its_base(a, b):
    assert ma._stem(a) == ma._stem(b)


def test_news_is_not_the_word_new_and_real_words_are_unchanged():
    assert ma._stem("news") != ma._stem("new")
    assert ma._stem("glasses") == ma._stem("glass")
    assert ma._stem("added") == ma._stem("adding")           # no false undoubling of 'dd'
    assert ma._stem("called") == ma._stem("call")
