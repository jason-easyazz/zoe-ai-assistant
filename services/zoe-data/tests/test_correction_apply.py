"""Class 2 — a correction reaches the STORED record, and the reply says what changed.

Synthetic people only (Jordan, Casey, Riley, Pat, Biscuit, Priya...). The memory store is an
in-memory fake with the real ``list_by_status`` / ``review(edit)`` / ``ingest`` contract; the
people graph is an in-memory SQLite with the production column names.

Negative controls (each proves the test can go red): flag OFF -> nothing is touched and
``fast_tiers`` never reaches the module; no stored evidence -> None (never claims a fix);
a month-first row about an UNRELATED person is not rewritten; a date that is not ambiguous is
not "corrected".
"""
import re

import aiosqlite
import pytest

pytestmark = pytest.mark.ci_safe

import correction_apply as ca
from memory_service import MemoryRef

USER = "demo_correction_user"  # a DEMO user — never a real person


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("ZOE_TIMEZONE", "Australia/Perth")
    monkeypatch.delenv("ZOE_DATE_ORDER", raising=False)
    monkeypatch.setenv(ca.ENV, "1")


class FakeSvc:
    """MemoryService stand-in: approved rows; review(edit) supersedes, ingest adds."""

    def __init__(self, texts):
        self.rows = {}
        self.n = 0
        self.superseded = []
        for t in texts:
            self._add(t)

    def _add(self, text, **meta):
        self.n += 1
        rid = f"m{self.n}"
        self.rows[rid] = MemoryRef(id=rid, text=text, metadata={"status": "approved", **meta})
        return self.rows[rid]

    async def list_by_status(self, *, user_id, status="pending", limit=100, offset=0):
        return [r for r in self.rows.values() if r.metadata.get("status") == status][offset:offset + limit]

    async def review(self, mem_id, *, decision, actor, edits=None, note=None, metadata=None,
                     source_excerpt=None):
        assert decision == "edit"
        old = self.rows[mem_id]
        old.metadata["status"] = "superseded"
        self.superseded.append(mem_id)
        return self._add(edits, supersedes=mem_id)

    async def ingest(self, text, *, user_id, source, **kw):
        return self._add(text, source=source, **kw)

    def approved(self):
        return [r.text for r in self.rows.values() if r.metadata.get("status") == "approved"]


async def _people_db():
    db = await aiosqlite.connect(":memory:")
    await db.execute(
        """CREATE TABLE people (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, name TEXT NOT NULL,
           relationship TEXT, deleted INTEGER NOT NULL DEFAULT 0, updated_at TEXT)""")
    await db.execute(
        """CREATE TABLE person_relationships (id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
           person_a_id TEXT NOT NULL, person_b_id TEXT NOT NULL, rel_type TEXT NOT NULL,
           rel_a_to_b TEXT NOT NULL, rel_b_to_a TEXT NOT NULL, rel_group TEXT NOT NULL,
           updated_at TEXT, valid_to TEXT)""")
    await db.execute(
        """CREATE TABLE person_important_dates (id TEXT PRIMARY KEY, person_id TEXT, user_id TEXT,
           label TEXT, date_type TEXT, month INTEGER, day INTEGER, year INTEGER, mem_id TEXT)""")
    await db.commit()
    return db


# ── cues ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "That date is wrong, we put the day before the month here",
    "that birthday is wrong",
    "no the date is the wrong way round",
    "you've got the day and month mixed up",
])
def test_date_correction_cue(text):
    assert ca.is_date_correction(text)


@pytest.mark.parametrize("text", [
    "What's the date today", "I was born in Australia", "set a reminder for the 26th",
])
def test_non_correction_turns(text):
    assert not ca.is_date_correction(text)


@pytest.mark.parametrize("text,expect", [
    ("Biscuit is their dog", ("Biscuit", "dog")),
    ("No, Biscuit is the dog", ("Biscuit", "dog")),
    ("biscuit is our family cat", ("Biscuit", "cat")),
    ("Biscuit is Jordan's dog.", ("Biscuit", "dog")),
])
def test_pet_statement(text, expect):
    assert ca.pet_statement(text) == expect


@pytest.mark.parametrize("text", [
    "Is Biscuit their dog?", "She is their dog walker", "That is my dog", "He is their friend",
    "Biscuit is their daughter",
])
def test_not_a_pet_statement(text):
    assert ca.pet_statement(text) is None


# ── date correction ──────────────────────────────────────────────────────────

SAID = "Pat Brown has a colleague called Jordan, his birthday is 7/8/1991"
WRONG_TURN = "That date is wrong, we put the day before the month here"


async def test_date_correction_supersedes_every_stored_wrong_birthday():
    svc = FakeSvc([
        "Jordan: 7/8/1991",                        # person extractor, raw digits
        "Jordan's birthday is 7/8/1991",           # turn digest, raw digits
        "Jordan's birthday is July 8th, 1991.",    # consolidation: a month-first model's rewrite
        "User likes tea",
    ])
    db = await _people_db()
    await db.execute("INSERT INTO person_important_dates VALUES ('d1','p1',?,?,'birthday',7,8,1991,'m1')",
                     (USER, "Jordan's birthday"))
    await db.commit()
    res = await ca.apply_date_correction(WRONG_TURN, USER, [SAID], svc=svc, db=db)
    assert res is not None and res.kind == "date"
    # every wrong row is superseded; the live rows say 7 August 1991
    live = svc.approved()
    assert "User likes tea" in live
    assert not any("7/8/1991" in t or "July 8" in t for t in live), live
    assert sum("7 August 1991" in t for t in live) == 3
    # the reply states WHAT changed, not "I'll get it right next time"
    assert res.reply.startswith("Fixed: Jordan's birthday is 7 August 1991")
    assert "next time" not in res.reply
    # the structured important-date row follows (month/day/year + the new memory id)
    cur = await db.execute("SELECT month, day, year, mem_id FROM person_important_dates")
    month, day, year, mem_id = await cur.fetchone()
    assert (month, day, year) == (8, 7, 1991) and mem_id != "m1"


async def test_date_correction_negative_unrelated_month_first_row_untouched():
    # a month-first row about someone the corrected message never mentions is left alone
    svc = FakeSvc(["Quinn's birthday is July 8th, 1991.", "Jordan: 7/8/1991"])
    res = await ca.apply_date_correction(WRONG_TURN, USER, [SAID], svc=svc, db=None)
    assert res is not None
    live = svc.approved()
    assert "Quinn's birthday is July 8th, 1991." in live
    assert "Jordan: 7 August 1991" in live


async def test_date_correction_needs_evidence():
    svc = FakeSvc(["Jordan: 7 August 1991"])
    # no ambiguous date in what the user said -> nothing to correct, the brain answers
    assert await ca.apply_date_correction(WRONG_TURN, USER, ["hello there"], svc=svc) is None
    # an UNambiguous date (26/10) was never mis-read, so it is not "corrected"
    assert await ca.apply_date_correction(WRONG_TURN, USER, ["Riley's birthday is 26/10/1985"],
                                          svc=FakeSvc(["Riley: 26/10/1985"])) is None
    # ambiguous date said, but nothing stored carries it -> no claim
    assert await ca.apply_date_correction(WRONG_TURN, USER, [SAID], svc=FakeSvc(["User likes tea"])) is None
    assert svc.superseded == []


async def test_date_correction_only_the_latest_dated_message_is_touched():
    svc = FakeSvc(["Jordan: 7/8/1991", "Riley: 3/4/1990"])
    res = await ca.apply_date_correction(
        WRONG_TURN, USER, ["Jordan's birthday is 7/8/1991", "Riley's birthday is 3/4/1990"], svc=svc)
    # newest first: the correction applies to the message just above it only
    assert res is not None
    assert "Jordan: 7/8/1991" in svc.approved() or "Jordan: 7 August 1991" in svc.approved()
    assert sum("3/4/1990" in t for t in svc.approved()) + sum("3 April 1990" in t for t in svc.approved()) == 1


async def test_flag_off_touches_nothing(monkeypatch):
    monkeypatch.delenv(ca.ENV)
    svc = FakeSvc(["Jordan: 7/8/1991"])
    assert await ca.maybe_apply(WRONG_TURN, USER, "s1", recent_messages=[SAID], svc=svc, db=None) is None
    assert svc.superseded == []


async def test_us_household_would_not_flip_the_row(monkeypatch):
    # control: the corrected rendering comes from date_locale, not from a constant
    monkeypatch.setenv("ZOE_DATE_ORDER", "mdy")
    svc = FakeSvc(["Jordan: 7/8/1991"])
    res = await ca.apply_date_correction(WRONG_TURN, USER, [SAID], svc=svc)
    assert res is not None and "Jordan: 8 July 1991" in svc.approved()


# ── pet correction ───────────────────────────────────────────────────────────

async def _family_db():
    db = await _people_db()
    for pid, name, rel in (("o", "Jordan Smith", "friend"), ("c1", "Casey Smith", "friend's child"),
                           ("pet", "Biscuit Smith", "friend's child")):
        await db.execute("INSERT INTO people VALUES (?,?,?,?,0,NULL)", (pid, USER, name, rel))
    await db.execute("INSERT INTO person_relationships VALUES ('e1',?,?,?,?,?,?,?,NULL,NULL)",
                     (USER, "pet", "o", "parent", "Parent", "Child", "family"))
    await db.execute("INSERT INTO person_relationships VALUES ('e2',?,?,?,?,?,?,?,NULL,NULL)",
                     (USER, "c1", "o", "parent", "Parent", "Child", "family"))
    await db.commit()
    return db


async def test_pet_statement_sets_relationship_edge_and_facts():
    db = await _family_db()
    svc = FakeSvc([
        "Jordan Smith has four children: Casey, Riley, Pat and Biscuit.",
        "Biscuit Smith is a child of Jordan Smith.",
        "User likes tea",
    ])
    res = await ca.apply_pet_correction("Biscuit is their dog", USER, svc=svc, db=db)
    assert res is not None and res.kind == "pet"
    assert res.reply.startswith("Fixed: Biscuit Smith is a pet dog, not one of the children")
    # the person row
    cur = await db.execute("SELECT relationship FROM people WHERE id='pet'")
    assert (await cur.fetchone())[0] == "pet dog"
    cur = await db.execute("SELECT relationship FROM people WHERE id='c1'")
    assert (await cur.fetchone())[0] == "friend's child"  # others untouched
    # the parent edge became the pet edge (pet = person_a); the real child's edge is untouched
    cur = await db.execute("SELECT person_a_id, person_b_id, rel_type, rel_group FROM person_relationships WHERE id='e1'")
    assert tuple(await cur.fetchone()) == ("pet", "o", "pet", "pet")
    cur = await db.execute("SELECT rel_type FROM person_relationships WHERE id='e2'")
    assert (await cur.fetchone())[0] == "parent"
    # stored facts: the enumeration loses only the pet; the child-of row becomes the pet fact
    live = svc.approved()
    assert not any(re.search(r"\bBiscuit\b", t) and re.search(r"\bchild", t)
                   and "not a child" not in t for t in live), live
    assert any("Casey" in t and "Riley" in t and "Pat" in t and "Biscuit" not in t for t in live), live
    assert "Biscuit Smith is a pet dog, not a child." in live
    assert "User likes tea" in live


async def test_pet_statement_without_stored_evidence_claims_nothing():
    db = await _people_db()  # nobody stored
    svc = FakeSvc(["User likes tea"])
    assert await ca.apply_pet_correction("Biscuit is their dog", USER, svc=svc, db=db) is None
    assert svc.approved() == ["User likes tea"]


async def test_pet_correction_is_idempotent():
    db = await _family_db()
    svc = FakeSvc(["Biscuit Smith is a child of Jordan Smith."])
    assert await ca.apply_pet_correction("Biscuit is their dog", USER, svc=svc, db=db) is not None
    n = len(svc.rows)
    # said again: the record already says pet -> nothing left to change, nothing new claimed
    assert await ca.apply_pet_correction("Biscuit is their dog", USER, svc=svc, db=db) is None
    assert len(svc.rows) == n


async def test_a_real_child_is_never_turned_into_a_pet():
    db = await _family_db()
    svc = FakeSvc(["Casey Smith is a child of Jordan Smith."])
    assert await ca.apply_pet_correction("Biscuit is their dog", USER, svc=svc, db=db) is not None
    assert "Casey Smith is a child of Jordan Smith." in svc.approved()
    cur = await db.execute("SELECT rel_type FROM person_relationships WHERE id='e2'")
    assert (await cur.fetchone())[0] == "parent"


def test_relationship_vocabulary_has_a_pet_type():
    from routers.people import RELATIONSHIP_TYPES, _rel_lookup

    assert _rel_lookup("pet") == ("pet", "Pet owner", "Pet")
    assert "pet" in RELATIONSHIP_TYPES


def test_extractor_reads_x_is_ys_dog_as_a_pet_edge():
    import person_extractor as pe

    m = pe._REL_RE.search("Biscuit is Jordan's dog")
    assert m.group("role1") == "dog" and pe._ROLE_TO_TYPE["dog"] == ("pet", "pet")


# ── the live path: fast_tiers ────────────────────────────────────────────────

async def test_fast_tier_returns_the_correction_reply(monkeypatch):
    import fast_tiers

    async def fake(text, user_id, session_id, **kw):
        return ca.CorrectionResult("date", "Fixed: Jordan's birthday is 7 August 1991.", [])

    monkeypatch.setattr(ca, "maybe_apply", fake)
    res = await fast_tiers.resolve(WRONG_TURN, USER, "s1", channel="chat")
    assert res is not None and res.tier == "correction"
    assert res.reply.startswith("Fixed: Jordan's birthday is 7 August 1991")


async def test_fast_tier_flag_off_never_calls_the_module(monkeypatch):
    import fast_tiers

    monkeypatch.delenv(ca.ENV)
    called = []

    async def fake(*a, **k):
        called.append(1)

    monkeypatch.setattr(ca, "maybe_apply", fake)
    await fast_tiers.resolve(WRONG_TURN, USER, "s1", channel="chat", run_tier0=False)
    assert called == []
