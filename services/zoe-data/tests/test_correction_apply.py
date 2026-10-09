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
OTHER_USER = "demo_correction_other"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("ZOE_TIMEZONE", "Australia/Perth")
    monkeypatch.delenv("ZOE_DATE_ORDER", raising=False)
    monkeypatch.setenv(ca.ENV, "1")


class FakeSvc:
    """MemoryService stand-in with the real scoping contract: every row belongs to a user,
    ``list_by_status`` only returns the caller's rows, ``review(edit)`` supersedes and ``ingest``
    adds under the caller. ``texts`` seed ``USER``; ``other`` seeds another member."""

    def __init__(self, texts, other=()):
        self.rows = {}
        self.n = 0
        self.superseded = []
        for t in texts:
            self._add(t, user_id=USER)
        for t in other:
            self._add(t, user_id=OTHER_USER)

    def _add(self, text, **meta):
        self.n += 1
        rid = f"m{self.n}"
        self.rows[rid] = MemoryRef(id=rid, text=text, metadata={"status": "approved", **meta})
        return self.rows[rid]

    async def list_by_status(self, *, user_id, status="pending", limit=100, offset=0):
        rows = [r for r in self.rows.values()
                if r.metadata.get("status") == status and r.metadata.get("user_id") == user_id]
        return rows[offset:offset + limit]

    async def review(self, mem_id, *, decision, actor, edits=None, note=None, metadata=None,
                     source_excerpt=None, **_kw):
        assert decision == "edit"
        old = self.rows[mem_id]
        old.metadata["status"] = "superseded"
        self.superseded.append(mem_id)
        return self._add(edits, supersedes=mem_id, user_id=old.metadata["user_id"])

    async def ingest(self, text, *, user_id, source, **kw):
        return self._add(text, source=source, user_id=user_id, **kw)

    def approved(self, user_id=None):
        return [r.text for r in self.rows.values() if r.metadata.get("status") == "approved"
                and r.metadata.get("user_id") == (user_id or USER)]


async def _people_db():
    db = await aiosqlite.connect(":memory:")
    await db.execute(
        """CREATE TABLE people (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, name TEXT NOT NULL,
           relationship TEXT, email TEXT, phone TEXT, birthday TEXT, is_partial INTEGER DEFAULT 0,
           deleted INTEGER NOT NULL DEFAULT 0, updated_at TEXT)""")
    await db.execute(
        """CREATE TABLE person_relationships (id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
           person_a_id TEXT NOT NULL, person_b_id TEXT NOT NULL, rel_type TEXT NOT NULL,
           rel_a_to_b TEXT NOT NULL, rel_b_to_a TEXT NOT NULL, rel_group TEXT NOT NULL,
           updated_at TEXT, valid_to TEXT, created_at TEXT, valid_from TEXT, superseded_by TEXT, authority TEXT,
           origin TEXT, close_reason TEXT, turn_id TEXT, quote_span TEXT, speaker_rank INTEGER)""")
    await db.execute(
        "CREATE UNIQUE INDEX person_relationships_pair_active ON person_relationships(user_id, person_a_id, person_b_id) "
        "WHERE valid_to IS NULL")
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
        await db.execute(
            "INSERT INTO people (id, user_id, name, relationship, deleted) VALUES (?,?,?,?,0)",
            (pid, USER, name, rel))
    await db.execute("INSERT INTO person_relationships (id, user_id, person_a_id, person_b_id, rel_type, rel_a_to_b, rel_b_to_a, rel_group) VALUES ('e1',?,?,?,?,?,?,?)",
                     (USER, "pet", "o", "parent", "Parent", "Child", "family"))
    await db.execute("INSERT INTO person_relationships (id, user_id, person_a_id, person_b_id, rel_type, rel_a_to_b, rel_b_to_a, rel_group) VALUES ('e2',?,?,?,?,?,?,?)",
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
    # the parent edge is CLOSED (never rewritten in place) and the pet edge is the new current one (pet = person_a);
    # the real child's edge is untouched
    cur = await db.execute("SELECT person_a_id, person_b_id, rel_type, close_reason, valid_to, superseded_by "
                           "FROM person_relationships WHERE id='e1'")
    a, b, rel_type, reason, valid_to, sup = tuple(await cur.fetchone())
    assert (a, b, rel_type, reason) == ("pet", "o", "parent", "corrected_pet") and valid_to and sup
    cur = await db.execute("SELECT id, person_a_id, person_b_id, rel_type, rel_group, authority, origin, turn_id, "
                           "quote_span, speaker_rank FROM person_relationships WHERE valid_to IS NULL AND rel_type='pet'")
    pet_edge = tuple(await cur.fetchone())
    assert pet_edge[0] == sup and pet_edge[1:5] == ("pet", "o", "pet", "pet")
    assert pet_edge[5:7] == ("user_stated", ca.SOURCE)
    assert pet_edge[7].startswith("ut-") and pet_edge[8].startswith("0:") and pet_edge[9] >= 4   # the evidence pointer
    cur = await db.execute("SELECT rel_type FROM person_relationships WHERE id='e2'")
    assert (await cur.fetchone())[0] == "parent"
    # stored facts: the enumeration loses only the pet; the child-of row becomes the pet fact
    live = svc.approved()
    assert not any(re.search(r"\bBiscuit\b", t) and re.search(r"\bchild", t)
                   and "not a child" not in t for t in live), live
    assert "Jordan Smith has three children: Casey, Riley, Pat." in live, live
    assert "Biscuit Smith is a pet dog, not a child." in live
    assert "User likes tea" in live


async def test_pet_correction_closes_a_wrong_edge_that_would_duplicate_the_pet_edge():
    db = await _family_db()
    await db.execute("UPDATE person_relationships SET valid_to='2026-01-01T00:00:00Z' WHERE id='e1'")  # no wrong pair edge left...
    await db.execute("INSERT INTO person_relationships (id, user_id, person_a_id, person_b_id, rel_type, rel_a_to_b, rel_b_to_a, "
                     "rel_group) VALUES ('ep', ?, 'pet', 'o', 'pet', 'Pet owner', 'Pet', 'pet')", (USER,))
    await db.execute("INSERT INTO person_relationships (id, user_id, person_a_id, person_b_id, rel_type, rel_a_to_b, rel_b_to_a, "
                     "rel_group) VALUES ('wrong', ?, 'o', 'pet', 'parent', 'Parent', 'Child', 'family')", (USER,))  # ...the reverse one
    await db.commit()
    assert await ca.apply_pet_correction("Biscuit is their dog", USER, svc=FakeSvc(["Biscuit Smith is a child."]), db=db) is not None
    cur = await db.execute("SELECT valid_to IS NOT NULL, close_reason, superseded_by FROM person_relationships WHERE id='wrong'")
    assert tuple(await cur.fetchone()) == (1, "corrected_pet_duplicate", "ep")               # closed, not deleted, not duplicated
    cur = await db.execute("SELECT COUNT(*) FROM person_relationships WHERE rel_type='pet' AND valid_to IS NULL")
    assert (await cur.fetchone())[0] == 1


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


def test_relationship_vocabulary_has_a_pet_type():
    from routers.people import RELATIONSHIP_TYPES, _rel_lookup

    assert _rel_lookup("pet") == ("pet", "Pet owner", "Pet")
    assert "pet" in RELATIONSHIP_TYPES


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


# ═══ review round 1 (#1860): one test per finding ═══════════════════════════════

# 3. date correction touches only rows that carry the SAME digits / the same dated rendering
async def test_date_correction_never_rewrites_a_date_stated_in_words():
    svc = FakeSvc([
        "Jordan: 7/8/1991",                           # same digits -> fixed
        "Pat's birthday is July 8",                   # another person, words, no year
        "Jordan's cousin's birthday is July 8",       # shares a name with the source, words, no year
        "Jordan's birthday is July 8, 1985",          # same person, different year -> a different fact
    ])
    res = await ca.apply_date_correction(WRONG_TURN, USER, [SAID], svc=svc)
    assert res is not None
    live = svc.approved()
    assert "Jordan: 7 August 1991" in live
    for untouched in ("Pat's birthday is July 8", "Jordan's cousin's birthday is July 8",
                      "Jordan's birthday is July 8, 1985"):
        assert untouched in live, live


async def test_date_correction_never_invents_a_year():
    # a year-less message: only rows holding the digits are rewritten, and no year appears
    svc = FakeSvc(["Jordan's birthday is 7/8", "Jordan's birthday is July 8"])
    said = "Pat Brown has a colleague called Jordan, born 7/8"
    res = await ca.apply_date_correction(WRONG_TURN, USER, [said], svc=svc)
    assert res is not None
    live = svc.approved()
    assert "Jordan's birthday is 7 August" in live and "Jordan's birthday is July 8" in live
    assert not any("19" in t or "20" in t for t in live)


# (c) a US-style preference sets the order for THIS correction instead of undoing it
async def test_us_style_cue_reads_the_date_month_first():
    assert ca.correction_order("I use US style dates") == "mdy"
    assert ca.correction_order("that date is wrong") == "dmy"
    svc = FakeSvc(["Jordan: 7/8/1991", "Jordan's birthday is 7 August 1991"])
    res = await ca.apply_date_correction("I use US style dates, that birthday is wrong", USER,
                                         [SAID], svc=svc)
    assert res is not None
    assert "Jordan: 8 July 1991" in svc.approved()
    assert "Jordan's birthday is 8 July 1991" in svc.approved()  # the day-first rendering flips too
    assert "dates are month first" in res.reply


# 4. namesakes
async def _namesake_db(*rows):
    db = await _people_db()
    for pid, name, rel, email in rows:
        await db.execute(
            "INSERT INTO people (id, user_id, name, relationship, email, deleted) VALUES (?,?,?,?,?,0)",
            (pid, USER, name, rel, email))
    await db.commit()
    return db


async def test_two_namesakes_refuse_and_ask_nothing_is_written():
    db = await _namesake_db(("h", "Biscuit Brown", "friend", "b@example.test"),
                            ("k", "Biscuit Smith", "friend's child", None))
    svc = FakeSvc(["Biscuit Smith is a child of Jordan Smith."])
    res = await ca.apply_pet_correction("Biscuit is my dog", USER, svc=svc, db=db)
    assert res is not None and res.kind == "pet_ask" and "more than one Biscuit" in res.reply
    cur = await db.execute("SELECT relationship FROM people ORDER BY id")
    assert [r[0] for r in await cur.fetchall()] == ["friend", "friend's child"]
    assert svc.superseded == [] and len(svc.rows) == 1


async def test_a_human_with_contact_data_is_never_made_a_pet():
    db = await _namesake_db(("h", "Biscuit Brown", "friend", "b@example.test"))
    svc = FakeSvc(["Biscuit Brown works at the library."])
    assert await ca.apply_pet_correction("Biscuit is my dog", USER, svc=svc, db=db) is None
    cur = await db.execute("SELECT relationship FROM people")
    assert (await cur.fetchone())[0] == "friend"
    assert svc.superseded == [] and len(svc.rows) == 1


async def test_a_single_childlike_match_is_still_converted():
    db = await _namesake_db(("k", "Biscuit Smith", "friend's child", None))
    res = await ca.apply_pet_correction("Biscuit is my dog", USER,
                                        svc=FakeSvc(["Biscuit Smith is a child of Jordan Smith."]), db=db)
    assert res is not None and res.kind == "pet"


# 5. clause-level edits, no wholesale replacement, no duplicate pet fact
async def test_a_row_about_kids_and_a_separately_named_dog_is_left_alone():
    db = await _namesake_db(("k", "Sam Smith", "friend's child", None))
    svc = FakeSvc(["Jordan has two kids and a dog named Sam"])
    res = await ca.apply_pet_correction("Sam is their dog", USER, svc=svc, db=db)
    assert res is not None  # the person row IS converted ...
    live = svc.approved()
    assert "Jordan has two kids and a dog named Sam" in live  # ... the kids fact is not lost
    assert svc.superseded == []


async def test_only_the_clause_about_the_pet_is_edited():
    svc = FakeSvc(["Jordan has two kids, Mika and Biscuit. He coaches football on Saturdays."])
    res = await ca.apply_pet_correction("Biscuit is their dog", USER, svc=svc, db=None)
    assert res is not None
    live = svc.approved()
    assert "Jordan has one kid, Mika. He coaches football on Saturdays." in live, live


async def test_the_pet_fact_is_stored_exactly_once():
    db = await _family_db()
    svc = FakeSvc(["Biscuit Smith is a child of Jordan Smith."])
    await ca.apply_pet_correction("Biscuit is their dog", USER, svc=svc, db=db)
    assert sum("not a child" in t for t in svc.approved()) == 1
    # and with no child row to edit, it is still ingested once
    svc2 = FakeSvc([])
    db2 = await _family_db()
    await ca.apply_pet_correction("Biscuit is their dog", USER, svc=svc2, db=db2)
    assert sum("not a child" in t for t in svc2.approved()) == 1


@pytest.mark.parametrize("text", [
    "Their baby girl is called Ruby and Biscuit sleeps all day",   # generic words, Biscuit elsewhere
    "My son plays football; Biscuit is the team mascot",
    "Biscuit likes the boys",
])
def test_generic_child_words_without_the_name_in_the_clause_are_not_a_child_claim(text):
    assert ca._claims_child(text, "Biscuit") is False


@pytest.mark.parametrize("text", [
    "Jordan has four children: Casey, Riley, Pat and Biscuit.",
    "Jordan has two kids, Mika and Biscuit.",
    "Biscuit Smith is a child of Jordan Smith.",
])
def test_child_claims_are_recognised(text):
    assert ca._claims_child(text, "Biscuit") is True


# scoping: a correction never touches another member's rows
async def test_corrections_never_touch_another_members_rows():
    svc = FakeSvc(["Jordan: 7/8/1991"],
                  other=["Jordan: 7/8/1991", "Biscuit Smith is a child of Jordan Smith."])
    res = await ca.apply_date_correction(WRONG_TURN, USER, [SAID], svc=svc)
    assert res is not None
    assert svc.approved(OTHER_USER) == ["Jordan: 7/8/1991", "Biscuit Smith is a child of Jordan Smith."]
    await ca.apply_pet_correction("Biscuit is their dog", USER, svc=svc, db=None)
    assert "Biscuit Smith is a child of Jordan Smith." in svc.approved(OTHER_USER)


# (e) a pet is never minted as a person by the regex relationship extractor
async def test_x_is_ys_dog_mints_no_person_row():
    import person_extractor as pe

    db = await _people_db()
    await db.execute("CREATE TABLE person_activities (id TEXT)")
    await pe.process_text("Biscuit is Jordan's dog", user_id=USER, db=db)
    cur = await db.execute("SELECT COUNT(*) FROM people")
    assert (await cur.fetchone())[0] == 0
    assert pe._REL_RE.search("Biscuit is Jordan's dog") is None
