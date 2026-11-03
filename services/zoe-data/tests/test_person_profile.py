"""person_profile + the "what do you know about <contact>" answer (person-centred memory phase 1, ZOE_PERSON_PROFILE_ANSWERS).

One group per owner rule, each with a control that goes red when the rule is broken: D1 (pending never served, unanchored
inference counted not said, approved inference labelled), D6 (own people only, private classes counted not read, guest and
unverified speaker hear nothing), D7 (a child's profile: no inference, no health/mood, no model gift idea), forgotten means
forever, and the flag (off = the brain answers, as before). Real in-memory SQLite, a fake memory store, synthetic names only.
"""
from __future__ import annotations

import asyncio
import contextlib
from datetime import date

import aiosqlite
import pytest

import db_pool
import memory_tombstones
import person_profile as pp
import provenance_answers as pa
from memory_service import MemoryRef

pytestmark = pytest.mark.ci_safe

U, OTHER = "demo_pp_owner", "demo_pp_other"
TODAY = date(2026, 3, 5)


def run(coro):
    return asyncio.run(coro)


class FakeSvc:
    """``list_by_entity`` WITHOUT the status filter, so the composer's own approved-only guard is what is tested."""

    def __init__(self, rows):
        self.rows = rows
        self.asked = []

    async def list_by_entity(self, user_id, entity_ids, *, status="approved"):
        self.asked.append(tuple(entity_ids))
        return [r for r in self.rows if r.metadata.get("entity_id") in entity_ids and r.metadata.get("user_id") == user_id]


def row(i, text, pid="p-ana", user=U, cls="user_stated", status="approved", basis="", added="2026-03-0{}T00:00:00Z", **meta):
    md = {"entity_id": pid, "user_id": user, "status": status, "authority_class": cls, "added_at": added.format(min(i, 9)),
          "source": "conversation", **meta}
    if basis:
        md["authority_basis"] = basis
    return MemoryRef(id=f"m{i}", text=text, metadata=md)


async def _db():
    db = await aiosqlite.connect(":memory:")
    db.row_factory = aiosqlite.Row
    await db.execute("""CREATE TABLE people (id TEXT PRIMARY KEY, user_id TEXT, name TEXT, relationship TEXT, circle TEXT,
        birthday TEXT, how_we_met TEXT, deleted INTEGER DEFAULT 0, is_partial INTEGER DEFAULT 0, created_at TEXT, updated_at TEXT,
        last_contacted_at TEXT)""")
    await db.execute("""CREATE TABLE person_important_dates (id TEXT PRIMARY KEY, person_id TEXT, user_id TEXT, label TEXT,
        date_type TEXT, month INTEGER, day INTEGER, year INTEGER, reminder_days_before INTEGER NOT NULL DEFAULT 7)""")
    await db.execute("""CREATE TABLE person_gift_ideas (id TEXT PRIMARY KEY, person_id TEXT, user_id TEXT, description TEXT,
        status TEXT DEFAULT 'idea', source TEXT, created_at TEXT)""")
    await db.execute("""CREATE TABLE person_relationships (id TEXT PRIMARY KEY, user_id TEXT, person_a_id TEXT, person_b_id TEXT,
        rel_a_to_b TEXT, rel_b_to_a TEXT, valid_to TEXT)""")
    people = [("p-ana", U, "Ana Lindqvist", "friend", "friends", "", "at university", 0),
              ("p-bo", U, "Bo Kestrel", "colleague", "work", "1990-03-12", "", 0),
              ("p-kit", U, "Kit Arden", "son", "family", "", "", 0),
              ("p-gone", U, "Gone Person", "friend", "friends", "", "", 1),
              ("p-sam1", U, "Sam Orr", "friend", "friends", "", "", 0),
              ("p-sam2", U, "Sam Pell", "cousin", "family", "", "", 0),
              ("p-theirs", OTHER, "Rue Vance", "friend", "friends", "", "", 0)]
    await db.executemany("INSERT INTO people (id, user_id, name, relationship, circle, birthday, how_we_met, deleted) "
                         "VALUES (?,?,?,?,?,?,?,?)", people)
    await db.execute("INSERT INTO person_important_dates VALUES ('d1','p-ana',?,'Birthday','birthday',3,19,NULL,7)", (U,))
    await db.execute("INSERT INTO person_gift_ideas VALUES ('g1','p-ana',?,'a pottery class','idea','manual','2026-01-01')", (U,))
    await db.execute("INSERT INTO person_gift_ideas VALUES ('g2','p-kit',?,'a drone','idea','llm','2026-01-01')", (U,))
    await db.execute("INSERT INTO person_gift_ideas VALUES ('g3','p-kit',?,'a chess set','idea','manual','2026-01-01')", (U,))
    await db.execute("INSERT INTO person_relationships VALUES ('e1',?,'p-ana','p-bo','friend','friend',NULL)", (U,))
    await db.commit()
    return db


ROWS = [
    row(1, "Ana loves wild swimming"),
    row(2, "Ana started a new job at the observatory", cls="user_confirmed"),
    row(3, "Ana is probably into jazz", cls="model_from_turn"),
    row(4, "Ana prefers tea over coffee", cls="model_from_transcript", basis="unanchored"),
    row(5, "Ana is waiting on a diagnosis from the hospital"),
    row(6, "Ana is moving to the coast", status="pending"),
    row(7, "Ana: plays the cello", pid="slug:ana_lindqvist"),
    row(8, "Ana loves wild swimming", added="2026-02-0{}T00:00:00Z"),        # exact repeat: shown once
    row(20, "Kit loves dinosaurs"),
]
for r in ROWS[-1:]:
    r.metadata["entity_id"] = "p-kit"
ROWS += [row(21, "Kit seems anxious about school", pid="p-kit"),
         row(22, "Kit probably wants a drone", pid="p-kit", cls="model_from_turn"),
         row(23, "Rue loves sailing", pid="p-theirs", user=OTHER)]


@pytest.fixture(autouse=True)
def _clean():
    memory_tombstones.clear_all()
    yield
    memory_tombstones.clear_all()


def compose(pid, user=U, rows=ROWS):
    async def go():
        db = await _db()
        try:
            return await pp.compose(db, user, pid, svc=FakeSvc(rows), today=TODAY)
        finally:
            await db.close()
    return run(go())


# ── the model: D1 authority ladder ───────────────────────────────────────────

def test_profile_lines_carry_authority_and_pending_is_never_served():
    prof = compose("p-ana")
    texts = {ln.text: ln.authority for ln in prof.lines}
    assert texts["Ana loves wild swimming"] == pp.USER_STATED
    assert texts["Ana started a new job at the observatory"] == pp.USER_CONFIRMED
    assert texts["Ana is probably into jazz"] == pp.INFERRED
    assert "Ana: plays the cello" in texts                     # the name-only stub's rows are the same person's
    assert "Ana is moving to the coast" not in texts           # pending (approve-before-write): never served
    assert "Ana prefers tea over coffee" not in texts and prof.to_check == 1   # unanchored inference: counted, not said
    assert sum(1 for ln in prof.lines if ln.text == "Ana loves wild swimming") == 1


def test_negative_control_pending_guard_bites():
    """Break the guard (a pending row stamped approved by a buggy reader) -> it IS served: the guard, not luck, kept it out."""
    rows = [row(6, "Ana is moving to the coast", status="approved")]
    assert [ln.text for ln in compose("p-ana", rows=rows).lines] == ["Ana is moving to the coast"]


def test_private_class_counted_never_read():
    prof = compose("p-ana")
    assert prof.private == 1
    out = pp.render(prof, voice=False)
    assert "diagnosis" not in out and "1 private note" in out


def test_facets_and_chips_in_chat_card():
    out = pp.render(compose("p-ana"), voice=False)
    assert out.startswith("Ana Lindqvist is your friend. You met at university.")
    assert "Life now:" in out and "Likes:" in out
    assert "(you said)" in out and "(you confirmed)" in out and "(I think)" in out
    assert "Coming up: birthday 19 March (in 14 days)" in out
    assert "Gift ideas: a pottery class" in out and "People: Bo Kestrel (friend)" in out


def test_voice_answer_is_ordered_and_short():
    out = pp.render(compose("p-ana"), voice=True)
    who, life, when = out.index("is your friend"), out.index("new job"), out.index("birthday is on 19 March")
    assert who < life < when and out.rstrip().endswith("only share those if you ask.") and "Want more?" in out
    assert "diagnosis" not in out


# ── D7 children ──────────────────────────────────────────────────────────────

def test_child_profile_owner_said_only():
    prof = compose("p-kit")
    assert prof.child
    assert [ln.text for ln in prof.lines] == ["Kit loves dinosaurs"]
    assert prof.private == 0                                   # a child's mood row is not even counted
    assert prof.gifts == ["a chess set"]                       # the model-proposed gift is dropped


def test_negative_control_adult_keeps_what_child_drops():
    rows = [row(21, "Bo seems anxious about work", pid="p-bo"), row(22, "Bo probably wants a drone", pid="p-bo",
                                                                      cls="model_from_turn")]
    prof = compose("p-bo", rows=rows)
    assert not prof.child and prof.private == 1 and [ln.text for ln in prof.lines] == ["Bo probably wants a drone"]


# ── walls: ownership, deletion, forgetting ───────────────────────────────────

def test_other_members_person_and_deleted_person_are_none():
    assert compose("p-theirs") is None
    assert compose("p-gone") is None


def test_forgotten_name_answers_nothing():
    assert compose("p-ana") is not None                        # control: the same person answers before the forget
    memory_tombstones.add(U, "Ana Lindqvist")
    assert compose("p-ana") is None


# ── dates ────────────────────────────────────────────────────────────────────

def test_legacy_birthday_column_read_through():
    prof = compose("p-bo", rows=[])
    assert prof.dates == [("birthday", 3, 12, 7)]


@pytest.mark.parametrize("raw,want", [("1990-03-12", (3, 12)), ("--03-12", (3, 12)), ("12 March", (3, 12)),
                                      ("March 12th", (3, 12)), ("12th of Mar", (3, 12)), ("31 February", None),
                                      ("someday", None), ("", None)])
def test_parse_birthday(raw, want):
    assert pp.parse_birthday(raw) == want


def test_next_occurrence_today_and_leap_day():
    assert pp.next_occurrence(3, 5, TODAY) == TODAY
    assert pp.next_occurrence(2, 29, date(2027, 1, 1)) == date(2027, 2, 28)


def test_sparse_profile_asks_one_question():
    prof = compose("p-sam1", rows=[])
    assert pp.render(prof, voice=True) == ("Sam Orr is your friend. I don't know much about Sam yet. "
                                           "Do you know when their birthday is?")


# ── the tier (provenance_answers.handle) ─────────────────────────────────────

@pytest.fixture
def wired(monkeypatch):
    @contextlib.asynccontextmanager
    async def ctx():
        db = await _db()          # one per call, closed: an open aiosqlite thread would keep the interpreter alive
        try:
            yield db
        finally:
            await db.close()

    async def no_names():
        return frozenset({"rue"})

    async def own(_uid):
        return frozenset({"owner"})

    monkeypatch.setattr(db_pool, "get_db_ctx", ctx)
    monkeypatch.setattr(pa, "account_names", no_names)
    monkeypatch.setattr(pa, "_own_names", own)
    monkeypatch.setenv(pp.ENV, "1")
    monkeypatch.delenv("ZOE_MEMORY_PROVENANCE_ANSWERS", raising=False)
    return FakeSvc(ROWS)


def ask(text, svc, user=U, **kw):
    return run(pa.handle(text, user, "s1", svc=svc, **kw))


def test_flag_off_leaves_it_to_the_brain(wired, monkeypatch):
    monkeypatch.setenv(pp.ENV, "0")
    assert ask("what do you know about Ana Lindqvist?", wired) is None
    assert ask("tell me about Ana", wired) is None


def test_flag_on_answers_from_rows(wired):
    out = ask("what do you know about Ana?", wired, channel="voice")
    assert out.startswith("Ana Lindqvist is your friend.") and "diagnosis" not in out
    assert ask("Tell me about ana lindqvist", wired).startswith("Ana Lindqvist is your friend.")


def test_unknown_name_and_non_person_go_to_the_brain(wired):
    assert ask("what do you know about Zebedee?", wired) is None
    assert ask("tell me about the weather", wired) is None
    assert ask("tell me about An", wired) is None              # never a prefix guess


def test_ambiguous_name_asks_which(wired):
    out = ask("what do you know about Sam?", wired)
    assert "Sam Orr" in out and "Sam Pell" in out and out.endswith("Which one do you mean?")


def test_walls_guest_unverified_other_member(wired):
    assert ask("what do you know about Ana?", wired, user="guest") == pa.GUEST_ABOUT_OTHER_REPLY
    assert ask("tell me about Ana", wired, user="guest") is None
    assert ask("what do you know about Ana?", wired, channel="voice", speaker_verified=False) == pa.UNVERIFIED_SPEAKER_REPLY
    assert ask("what do you know about Rue?", wired) == pa.OTHER_MEMBER_REPLY
    # another member's contact is not the asker's: nothing is read from it
    assert ask("what do you know about Rue Vance?", wired) == pa.OTHER_MEMBER_REPLY
