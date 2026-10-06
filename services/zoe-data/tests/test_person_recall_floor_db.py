"""Named-person recall floor (S21) - the SQL half: user isolation, the focus reads, the endpoint.

test_person_recall_floor.py proves the matcher, the flag modes and the seam with the reads faked.
Here the reads are REAL SQL against an in-memory database that holds TWO users' people - the
owner (who told Zoe about Dana Whitfield and Mika) and a stranger - reached through the same
``db_pool.get_db_ctx`` seam production uses. Synthetic names throughout.
"""
from __future__ import annotations

import contextlib
import logging

import pytest

aiosqlite = pytest.importorskip("aiosqlite")
pytest.importorskip("fastapi")

import db_pool  # noqa: E402
import memory_gate as mg  # noqa: E402
import memory_service  # noqa: E402
import person_recall_floor as prf  # noqa: E402
import zoe_flue_client as zc  # noqa: E402
import zoe_memory_compose as zmc  # noqa: E402
from memory_service import MemoryRef  # noqa: E402
from routers import memories  # noqa: E402

OWNER, STRANGER = "demo-owner", "demo-stranger"
S21_ASK = "How many children does Dana Whitfield have?"
FACT = "Dana Whitfield has one kid, Mika."

_OPEN = []


@pytest.fixture(autouse=True)
async def _close_dbs():
    yield
    while _OPEN:
        await _OPEN.pop().close()


async def _new_db():
    db = await aiosqlite.connect(":memory:")
    _OPEN.append(db)
    db.row_factory = aiosqlite.Row
    await db.execute(
        """CREATE TABLE people (
            id TEXT PRIMARY KEY, user_id TEXT NOT NULL, name TEXT NOT NULL,
            relationship TEXT, email TEXT, phone TEXT, birthday TEXT, how_we_met TEXT,
            first_met_date TEXT, notes TEXT, circle TEXT, context TEXT, visibility TEXT,
            deleted INTEGER NOT NULL DEFAULT 0, is_partial INTEGER NOT NULL DEFAULT 0,
            introduced_by_person_id TEXT, last_contacted_at TEXT, created_at TEXT, updated_at TEXT)"""
    )
    await db.execute(
        """CREATE TABLE person_relationships (
            id TEXT PRIMARY KEY, user_id TEXT NOT NULL, person_a_id TEXT NOT NULL,
            person_b_id TEXT NOT NULL, rel_type TEXT NOT NULL, rel_a_to_b TEXT NOT NULL,
            rel_b_to_a TEXT NOT NULL, rel_group TEXT NOT NULL, notes TEXT,
            created_at TEXT, updated_at TEXT, valid_from TEXT, valid_to TEXT, superseded_by TEXT)"""
    )
    await db.execute("CREATE TABLE user_portraits (user_id TEXT, portrait_text TEXT)")
    await db.execute(
        """CREATE TABLE person_important_dates (id TEXT PRIMARY KEY, person_id TEXT, user_id TEXT,
           label TEXT, date_type TEXT, month INTEGER, day INTEGER, year INTEGER, mem_id TEXT)"""
    )
    return db


async def _person(db, pid, user, name, *, rel="friend", deleted=0, last=None, visibility="family"):
    await db.execute(
        "INSERT INTO people (id, user_id, name, relationship, visibility, deleted, last_contacted_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (pid, user, name, rel, visibility, deleted, last),
    )


async def _edge(db, user, child, parent, updated):
    await db.execute(
        "INSERT INTO person_relationships (id, user_id, person_a_id, person_b_id, rel_type, rel_a_to_b, "
        "rel_b_to_a, rel_group, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (f"e-{child}-{parent}", user, child, parent, "parent", "Parent", "Child", "family", updated),
    )


@pytest.fixture
async def store(monkeypatch):
    """Owner: Dana Whitfield (friend, contacted long ago) + her son Mika + ten more people contacted
    more recently; a deleted contact; and a STRANGER with a family-visible contact of their own."""
    db = await _new_db()
    await _person(db, "p-dana", OWNER, "Dana Whitfield", last="2020-01-01")
    await _person(db, "p-mika", OWNER, "Mika", rel="son", last="2020-01-02")
    await _person(db, "p-ghost", OWNER, "Ghost Person", deleted=1)
    for i in range(10):
        await _person(db, f"p-filler{i}", OWNER, f"Filler{chr(65 + i)} Person", last=f"2026-0{1 + i % 9}-1{i % 9}")
        await _edge(db, OWNER, f"p-filler{i}", "p-filler0" if i else "p-dana", f"2026-05-0{1 + i % 9}")
    await _edge(db, OWNER, "p-mika", "p-dana", "2020-02-02")
    await db.execute("INSERT INTO person_important_dates VALUES ('d1','p-dana',?, 'Birthday','birthday',3,9,NULL,NULL)",
                     (OWNER,))
    await _person(db, "p-other", STRANGER, "Other Person")
    await db.commit()

    @contextlib.asynccontextmanager
    async def ctx():
        yield db

    monkeypatch.setattr(db_pool, "get_db_ctx", ctx)
    monkeypatch.delenv(mg.PERSON_FLOOR_ENV, raising=False)
    monkeypatch.setenv("ZOE_MEMORY_COMPOSE_ENABLED", "1")
    return db


# ── user isolation: the asker's OWN people, never the household union ─────────

async def test_known_people_are_the_askers_own_live_rows(store):
    names = [n for _, n in await prf._known_people(OWNER)]
    assert "Dana Whitfield" in names and "Mika" in names and "Ghost Person" not in names
    assert [n for _, n in await prf._known_people(STRANGER)] == ["Other Person"]


async def test_a_stranger_naming_the_owners_family_visible_contact_gets_nothing(store, monkeypatch):
    """Dana is visibility='family' - the relational block would show her to the household - but
    the floor resolves against the asker's own rows only, so the stranger's turn is unchanged."""
    async def none_pending(uid, cands):
        return []

    monkeypatch.setattr(prf, "_pending_entity_names", none_pending)
    assert await prf.resolve_named_people(S21_ASK, STRANGER) == []
    got = await prf.resolve_named_people(S21_ASK, OWNER)
    assert [(p.name, p.person_id) for p in got] == [("Dana Whitfield", "p-dana")]


async def test_a_deleted_contact_is_not_named(store, monkeypatch):
    async def none_pending(uid, cands):
        return []

    monkeypatch.setattr(prf, "_pending_entity_names", none_pending)
    assert await prf.resolve_named_people("How old is Ghost Person?", OWNER) == []


class _EntitySvc:
    """list_by_entity scoped to the asker, as MemoryService's is."""

    def __init__(self):
        self.rows = {(OWNER, "slug:priya_nair"): [MemoryRef("m1", "Priya Nair likes tea.", {"entity_id": "slug:priya_nair"})]}
        self.calls = []

    async def list_by_entity(self, user_id, entity_ids, *, status="approved"):
        self.calls.append((user_id, list(entity_ids), status))
        return [r for e in entity_ids for r in self.rows.get((user_id, e), [])]


async def test_a_person_fact_entity_is_named_for_its_owner_only(store, monkeypatch):
    svc = _EntitySvc()
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: svc)
    q = "When is Priya Nair's job?"
    got = await prf.resolve_named_people(q, OWNER)
    assert got == [prf.NamedPerson("Priya Nair", "", "memory")]
    assert await prf.resolve_named_people(q, STRANGER) == []
    assert svc.calls[0] == (OWNER, ["slug:priya_nair"], "approved")
    assert await prf.resolve_named_people("When is Zed Quill's job?", OWNER) == []


# ── the relational block: the named person LEADS, within the same caps ────────

def _text(block):
    return "\n".join(block["lines"])


async def test_without_a_focus_the_named_person_is_crowded_out_of_the_block(store):
    """The gap the focus closes: ten more recently contacted people fill the 8-row caps."""
    block = await zmc.compose_relational_block(OWNER, S21_ASK, store)   # "children" is relational
    assert "Dana Whitfield (friend)" not in _text(block)
    assert "Mika's parent is Dana Whitfield" not in _text(block)


async def test_the_focus_leads_the_block_with_the_person_her_edges_and_dates(store):
    block = await zmc.compose_relational_block(OWNER, S21_ASK, store, focus_ids=["p-dana"])
    lines = block["lines"]
    assert lines[0].startswith("- Dana Whitfield (friend)")
    text = _text(block)
    assert "Mika's parent is Dana Whitfield" in text and "Dana Whitfield's Birthday: March 9" in text
    assert sum("[people]" in ln for ln in lines) == 1, "about Dana, not the ten people contacted last"
    assert sum("[relationship]" in ln for ln in lines) == 2, "only the edges that touch her"
    assert "Filler" not in "".join(ln for ln in lines if "[people]" in ln)
    assert len(block["refs"]) == len(lines)


async def test_a_focus_id_that_is_not_the_askers_reads_nothing(store):
    out = {"people": [], "relationships": [], "dates": []}
    await zmc._lead_with_focus(store, STRANGER, ["p-dana", "p-mika"], out, "")
    assert out == {"people": [], "relationships": [], "dates": []}


async def test_a_named_person_makes_any_wording_relational_and_nobody_else_does(store):
    q = "What is Dana Whitfield's job?"
    assert not zmc.needs_relational(q)
    assert await zmc.compose_relational_block(OWNER, q, store) is None
    assert await zmc.compose_packet(OWNER, q) is None
    block = await zmc.compose_packet(OWNER, q, focus_ids=["p-dana"])
    assert block["lines"][0].startswith("- Dana Whitfield (friend)")


async def test_the_flag_still_governs_the_block(store, monkeypatch):
    monkeypatch.delenv("ZOE_MEMORY_COMPOSE_ENABLED")
    assert await zmc.compose_packet(OWNER, S21_ASK, focus_ids=["p-dana"]) is None


# ── the for-prompt endpoint ──────────────────────────────────────────────────

class _Svc:
    def __init__(self):
        self.searched = []

    async def load_for_prompt(self, user_id, *, limit):
        return [MemoryRef("m1", FACT, {"status": "approved", "memory_type": "fact"})]

    async def search(self, query, *, user_id, limit=6, **_):
        self.searched.append(query)
        return []


@pytest.fixture
def svc(monkeypatch):
    s = _Svc()
    for k in ("ZOE_EMOTIONAL_RECALL_ENABLED", "ZOE_PERSON_SUGGEST_ENABLED", "ZOE_RECALL_EVIDENCE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(memories, "_svc", lambda: s)
    return s


JOB_ASK = "What is Dana Whitfield's job?"


async def test_the_endpoint_skips_the_search_for_a_question_the_gate_does_not_know(store, svc):
    assert not mg.message_needs_memory(JOB_ASK)
    res = await memories.memory_for_prompt(user_id=OWNER, message=JOB_ASK, limit=12, _=None)
    assert svc.searched == []
    assert "## People & important dates" not in res["packet"]


async def test_force_recall_and_focus_run_the_search_and_lead_the_relational_block(store, svc):
    res = await memories.memory_for_prompt(user_id=OWNER, message=JOB_ASK, limit=12, _=None,
                                           force_recall=True, focus_people=["p-dana"])
    assert svc.searched == [JOB_ASK]
    assert FACT in res["packet"]
    rel = res["packet"].split("## People & important dates\n", 1)[1]
    assert rel.startswith("- Dana Whitfield (friend)")
    assert res["relational"] >= 1


# ── end to end: the seam, the real endpoint, the real SQL ────────────────────

@pytest.fixture
def seam(store, svc, monkeypatch):
    monkeypatch.setenv("ZOE_SEAM_RECALL_INJECT", "1")
    monkeypatch.delenv("ZOE_SEAM_CONTINUITY_INJECT", raising=False)
    entities = _EntitySvc()
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: entities)
    return svc


async def test_the_s21_ask_reaches_memory_end_to_end(seam, caplog):
    caplog.set_level(logging.INFO)
    block = await zc._recall_context_block(S21_ASK, OWNER)
    assert block.startswith(zc._RECALL_BLOCK_OPEN) and block.endswith(zc._RECALL_BLOCK_CLOSE)
    assert FACT in block, "the corrected record is read"
    assert "Mika's parent is Dana Whitfield [relationship]" in block, "the people graph rides along"
    assert seam.searched == [S21_ASK]
    assert sum(1 for ln in block.splitlines() if ln.startswith("- ")) <= zc._RECALL_MAX_BULLETS
    assert len(block) <= zc._RECALL_MAX_CHARS + 200
    msgs = [r.getMessage() for r in caplog.records]
    assert any("RECALL_FLOOR reason=named_person" in m and "forced=1" in m for m in msgs)
    assert any("SEAM_RECALL" in m and "shape=named_person" in m for m in msgs)


async def test_the_same_ask_from_a_stranger_gets_nothing_end_to_end(seam):
    assert await zc._recall_context_block(S21_ASK, STRANGER) == ""
    assert seam.searched == []


async def test_an_unnamed_question_gets_nothing_end_to_end(seam):
    assert await zc._recall_context_block("How many children does my friend have?", OWNER) == ""
    assert seam.searched == []


@pytest.mark.parametrize("mode", ["off", "shadow"])
async def test_off_and_shadow_force_nothing_end_to_end(seam, monkeypatch, mode):
    monkeypatch.setenv(mg.PERSON_FLOOR_ENV, mode)
    assert await zc._recall_context_block(S21_ASK, OWNER) == ""
    assert seam.searched == []


# ── the voice recall packet (routers.voice_tts) ──────────────────────────────

async def test_the_voice_packet_gets_the_relational_lines_for_a_named_person(store, monkeypatch):
    import routers.voice_tts as v

    monkeypatch.setattr(memory_service, "get_memory_service", lambda: _EntitySvc())
    lines = await v._voice_relational_lines(JOB_ASK, OWNER)
    assert lines and lines[0].startswith("Dana Whitfield (friend)")
    assert await v._voice_relational_lines(JOB_ASK, STRANGER) == [], "a stranger's turn is unchanged"
    monkeypatch.setenv(mg.PERSON_FLOOR_ENV, "off")
    assert await v._voice_relational_lines(JOB_ASK, OWNER) == [], "off = the word-gated behaviour"
