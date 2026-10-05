"""The speaker's OWN relations are kept, with the names (ZMB B9).

"I have two kids, Mika and Sam" dropped both names. Root causes, each pinned here (synthetic
names only; real in-memory SQLite for the people graph; no network, no model):

  1. memory_extractor (the per-turn deterministic stage) had no reader for a LIST of new
     names - its templates only know "my <role> is named <X>" - so the regex stage returned
     nothing; named_relations (#1874) read the list but only person_extractor called it, and
     that pass needs the database;
  2. memory_quality.user_relationship_claim_unsupported - the anchor guard every model writer
     (turn digest, person LLM, consolidation) passes through - accepted "my <role>" only, so
     "User has two kids, Mika and Sam." was dropped as "the extractor guessed the anchor"
     although the speaker said it in the first person ("I have two kids ...");
  3. the speaker has no node in the people graph, so the listed people got no row at all.

Controls: a NAMED owner's list ("my friend Dana has two kids, Mika and Biscuit") works exactly
as in #1874 (graph rows linked to Dana); a question / supposition stores nothing; pets are never
minted as people; a name that already belongs to a different contact is not re-linked.
"""
import aiosqlite
import pytest

pytestmark = pytest.mark.ci_safe

import memory_extractor
import memory_quality
import named_relations as nr
import people_roles as pr
import person_extractor as pe
from memory_service import MemoryRef

USER = "demo_speaker_relations_user"  # a DEMO user - never a real person

# (what the speaker says, the one fact stored, the listed people)
SPEAKER_LISTS = [
    ("I have two kids, Mika and Sam.", "User has two kids, Mika and Sam.", ["Mika", "Sam"]),
    ("my sisters are Ana and Bea", "User has two sisters, Ana and Bea.", ["Ana", "Bea"]),
    ("I have two sisters, Ana and Bea", "User has two sisters, Ana and Bea.", ["Ana", "Bea"]),
    ("my kids are Mika and Sam", "User has two kids, Mika and Sam.", ["Mika", "Sam"]),
    ("we have two dogs, Rex and Fido", "User has two dogs, Rex and Fido.", []),   # pets are not people
]


# -- stage 1: the per-turn deterministic extractor -------------------------------------------------

@pytest.mark.parametrize("said,fact,_people", SPEAKER_LISTS)
def test_the_regex_stage_keeps_the_speakers_list_with_every_name(said, fact, _people):
    cands = memory_extractor.extract_candidates(said, "")
    assert [c.text for c in cands] == [fact]
    assert all(memory_quality.is_storable_fact(c.text)[0] for c in cands)


def test_my_dog_is_biscuit_keeps_the_name():
    assert [c.text for c in memory_extractor.extract_candidates("my dog is Biscuit", "")] == [
        "User's dog is named Biscuit"]


def test_the_zmb_b9_cell_shape():
    """The exact cell: one owner_typed turn, the facts probe wants a stored fact carrying each name."""
    texts = [c.text for c in memory_extractor.extract_candidates("I have two kids, Mika and Sam.", "")]
    assert any("Mika" in t for t in texts) and any("Sam" in t for t in texts)


def test_control_a_named_owners_list_is_not_read_as_the_speakers():
    """"my friend Dana has two kids, Mika and Biscuit": Dana's kids, kept by person_extractor
    (graph rows linked to Dana) - the regex stage must NOT file them under the speaker."""
    said = "my friend Dana has two kids, Mika and Biscuit"
    assert [c.text for c in memory_extractor.extract_candidates(said, "")] == []


@pytest.mark.parametrize("said", [
    "do I have two kids, Mika and Sam?",
    "do I have two kids, Mika and Sam",
    "if I have two kids, Mika and Sam we should go",
    "I wonder whether I have two kids, Mika and Sam?",
])
def test_a_question_or_supposition_is_not_a_statement(said):
    assert nr.extract_named_relations(said) == []
    assert memory_extractor.extract_candidates(said, "") == []


def test_the_two_writers_state_one_fact_text():
    """Regex stage and person pass must write the SAME sentence so ingest collapses them."""
    said = "I have two kids, Mika and Sam."
    assert [c.text for c in memory_extractor.extract_candidates(said, "")] == [nr.extract_named_relations(said)[0].fact()]


# -- stage 2: the anchor guard every model writer passes through ------------------------------------

@pytest.mark.parametrize("fact,turn", [
    ("User has two kids, Mika and Sam.", "I have two kids, Mika and Sam"),
    ("User's kids are Mika and Sam.", "I have two kids, Mika and Sam"),
    ("User has two sisters, Ana and Bea.", "I have two sisters, Ana and Bea"),
    ("User has a younger sister, Ana.", "I've got a younger sister called Ana"),
    ("User has two kids, Mika and Sam.", "we have two kids, Mika and Sam"),
    ("User has two sisters, Ana and Bea.", "my sisters are Ana and Bea"),
])
def test_a_first_person_statement_supports_the_users_own_relatives(fact, turn):
    assert not memory_quality.user_relationship_claim_unsupported(fact, turn)
    assert not pr.named_role_claim_unsupported(fact, turn)


@pytest.mark.parametrize("fact,turn", [
    ("User's friend Dana has two kids, Mika and Biscuit.", "my friend Dana has two kids, Mika and Biscuit"),
    ("User has a sister.", "I don't have a sister"),
    ("User has a sister.", "I have a friend whose sister is Ana"),
    ("User's wife is Emily.", "Emily is the wife"),
    ("User has two kids, Mika and Sam.", "Dana has two kids, Mika and Sam"),
])
def test_control_an_anchor_the_speaker_never_stated_is_still_dropped(fact, turn):
    assert memory_quality.user_relationship_claim_unsupported(fact, turn)


# -- stage 3: the write path (person pass) ----------------------------------------------------------

class FakeSvc:
    def __init__(self):
        self.rows = dict()
        self.n = 0

    def _add(self, text, **meta):
        self.n += 1
        rid = f"m{self.n}"
        md = dict(status="approved")
        md.update(meta)
        self.rows[rid] = MemoryRef(id=rid, text=text, metadata=md)
        return self.rows[rid]

    async def search(self, text, user_id, limit=3, **kw):
        return []

    async def get(self, mem_id):
        return self.rows.get(mem_id)

    async def ingest(self, text, *, user_id, source, **kw):
        return self._add(text, source=source, user_id=user_id, **kw)

    async def relink_entity(self, user_id, mem_id, entity_type, entity_id):
        self.rows[mem_id].metadata.update(entity_type=entity_type, entity_id=entity_id)
        return True

    async def list_by_status(self, *, user_id, status="pending", limit=100, offset=0):
        rows = [r for r in self.rows.values()
                if r.metadata.get("status") == status and r.metadata.get("user_id") == user_id]
        return rows[offset:offset + limit]

    async def review(self, mem_id, *, decision, actor, edits=None, **kw):
        old = self.rows[mem_id]
        old.metadata["status"] = "superseded"
        return self._add(edits, supersedes=mem_id, user_id=old.metadata["user_id"])

    def approved(self):
        return [r.text for r in self.rows.values() if r.metadata.get("status") == "approved"]


@pytest.fixture
def svc(monkeypatch):
    fake = FakeSvc()
    import memory_service

    monkeypatch.setattr(memory_service, "get_memory_service", lambda: fake)
    return fake


_OPEN = []


@pytest.fixture(autouse=True)
async def _close_dbs():
    yield
    while _OPEN:
        await _OPEN.pop().close()


async def _open_db():
    from test_named_relations import _open_db as open_db

    db = await open_db()
    _OPEN.append(db)
    return db


async def _people(db):
    cur = await db.execute("SELECT name, relationship, user_id, is_partial, visibility FROM people ORDER BY name")
    return [tuple(r) for r in await cur.fetchall()]


async def _edge_count(db):
    cur = await db.execute("SELECT count(*) FROM person_relationships")
    return (await cur.fetchone())[0]


@pytest.mark.parametrize("said,fact,people", SPEAKER_LISTS)
async def test_the_person_pass_keeps_the_fact_and_links_each_name_to_the_owner(svc, said, fact, people):
    db = await _open_db()
    await pe.process_text(said, user_id=USER, db=db)
    assert fact in svc.approved()
    assert [p[0] for p in await _people(db)] == sorted(people)
    for _name, _rel, owner, partial, visibility in await _people(db):
        assert owner == USER and partial == 1 and visibility == "personal"
    assert await _edge_count(db) == 0   # no self node, so no edge


@pytest.mark.parametrize("said,role", [
    ("I have two kids, Mika and Sam.", "child"),
    ("my sisters are Ana and Bea", "sister"),
    ("my brothers are Tom and Ned", "brother"),
    ("my parents are Rosa and Ivo", "parent"),
])
async def test_each_listed_person_states_the_role_to_the_speaker(svc, said, role):
    db = await _open_db()
    await pe.process_text(said, user_id=USER, db=db)
    assert {r for _n, r, *_ in await _people(db)} == {role}


async def test_restating_the_list_changes_nothing(svc):
    db = await _open_db()
    for _ in range(3):
        await pe.process_text("I have two kids, Mika and Sam.", user_id=USER, db=db)
    assert [p[0] for p in await _people(db)] == ["Mika", "Sam"]


async def test_a_name_that_is_a_different_contact_is_not_adopted(svc):
    """"Sam" must not become the speaker's child by substring-matching an existing "Samantha"."""
    db = await _open_db()
    await db.execute("INSERT INTO people (id,user_id,name,relationship,circle,context,visibility,is_partial) "
                     "VALUES ('p1',?,'Samantha','colleague','circle','work','personal',0)", (USER,))
    await db.commit()
    await pe.process_text("I have two kids, Mika and Sam.", user_id=USER, db=db)
    rows = {n: r for n, r, *_ in await _people(db)}
    assert rows["Samantha"] == "colleague" and "Sam" not in rows and rows["Mika"] == "child"
    assert "User has two kids, Mika and Sam." in svc.approved()      # the fact keeps the name regardless


async def test_an_existing_contact_with_no_role_gains_it_and_one_with_a_role_keeps_it(svc):
    db = await _open_db()
    await db.execute("INSERT INTO people (id,user_id,name,relationship,circle,context,visibility,is_partial) "
                     "VALUES ('p1',?,'Mika',NULL,'circle','personal','personal',0)", (USER,))
    await db.execute("INSERT INTO people (id,user_id,name,relationship,circle,context,visibility,is_partial) "
                     "VALUES ('p2',?,'Sam','friend','circle','personal','personal',0)", (USER,))
    await db.commit()
    await pe.process_text("I have two kids, Mika and Sam.", user_id=USER, db=db)
    assert {n: r for n, r, *_ in await _people(db)} == {"Mika": "child", "Sam": "friend"}


@pytest.mark.parametrize("said", ["do I have two kids, Mika and Sam?", "if I have two kids, Mika and Sam"])
async def test_a_question_writes_no_fact_and_no_person(svc, said):
    db = await _open_db()
    await pe.process_text(said, user_id=USER, db=db)
    assert svc.approved() == [] and await _people(db) == []


async def test_control_a_named_owners_list_still_lands_as_in_1874(svc):
    """"my friend Dana has two kids, Mika and Biscuit": the fact keeps the names and the children are
    graph rows linked to Dana (a = the child, b = the parent) - the speaker lane did not touch it."""
    db = await _open_db()
    await pe.process_text("my friend Dana has two kids, Mika and Biscuit.", user_id=USER, db=db)
    assert "Dana has two kids, Mika and Biscuit." in svc.approved()
    cur = await db.execute(
        "SELECT a.name, b.name, r.rel_type FROM person_relationships r "
        "JOIN people a ON a.id=r.person_a_id JOIN people b ON b.id=r.person_b_id ORDER BY a.name")
    assert [tuple(r) for r in await cur.fetchall()] == [("Biscuit", "Dana", "parent"), ("Mika", "Dana", "parent")]


async def test_a_failing_people_insert_never_costs_the_fact(svc, monkeypatch):
    async def boom(*_a, **_k):
        raise RuntimeError("db down")

    db = await _open_db()
    monkeypatch.setattr(nr, "_mint_owned_people", boom)
    await pe.process_text("I have two kids, Mika and Sam.", user_id=USER, db=db)
    assert "User has two kids, Mika and Sam." in svc.approved()


# -- the two writers collapse to ONE row in the real store ----------------------------------------

class _Col:
    def __init__(self):
        self.rows = {}

    def upsert(self, *, ids, documents, metadatas, **_kw):
        for i, d, m in zip(ids, documents, metadatas):
            self.rows[i] = (d, dict(m))

    def update(self, *, ids, metadatas, **_kw):
        for i, m in zip(ids, metadatas):
            self.rows[i] = (self.rows[i][0], dict(m))

    def get(self, *, ids=None, where=None, include=None, **_kw):
        keys = [i for i in ids if i in self.rows] if ids is not None else list(self.rows)
        return {"ids": keys, "documents": [self.rows[i][0] for i in keys],
                "metadatas": [dict(self.rows[i][1]) for i in keys]}

    def query(self, *, query_texts, n_results=10, where=None, **_kw):
        """Token overlap stands in for the embedding: identical text = distance 0."""
        import math
        import re

        out = {"ids": [], "documents": [], "metadatas": [], "distances": []}
        for q in query_texts:
            qt = set(re.findall(r"[a-z0-9']+", q.lower()))
            scored = []
            for i, (d, m) in self.rows.items():
                dt = set(re.findall(r"[a-z0-9']+", d.lower()))
                scored.append((1.0 - len(qt & dt) / math.sqrt(max(len(qt), 1) * max(len(dt), 1)), i))
            top = sorted(scored)[:n_results]
            out["ids"].append([i for _s, i in top])
            out["documents"].append([self.rows[i][0] for _s, i in top])
            out["metadatas"].append([dict(self.rows[i][1]) for _s, i in top])
            out["distances"].append([sc for sc, _i in top])
        return out

    def count(self):
        return len(self.rows)


async def test_regex_stage_and_person_pass_land_one_row_in_the_real_store(monkeypatch):
    import memory_service

    s = memory_service.MemoryService(data_dir="/nonexistent/zoe-test-speaker-relations")
    col = _Col()
    s._collection = lambda: col

    async def no_audit(**_kw):
        return None

    async def opted_in(_uid):
        return False

    s._append_audit = no_audit
    monkeypatch.setattr(memory_service, "_user_opted_out", opted_in)
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: s)
    monkeypatch.setattr(memory_extractor, "_memory_opted_out", opted_in)
    said = "I have two kids, Mika and Sam."
    db = await _open_db()
    await memory_extractor.extract_and_ingest(said, user_id=USER, source="chat_regex", prev_user_message="")
    await pe.process_text(said, user_id=USER, source="conversation", db=db)
    live = [d for d, m in col.rows.values() if m.get("status") == "approved"]
    assert live == ["User has two kids, Mika and Sam."], live


async def test_break_the_fix_control_without_the_stage_the_names_are_lost(monkeypatch):
    """With the speaker-list reader removed the regex stage returns nothing: the cells above measure it."""
    monkeypatch.setattr(memory_extractor, "_speaker_list_candidates", lambda *_a: [])
    assert memory_extractor.extract_candidates("I have two kids, Mika and Sam.", "") == []
