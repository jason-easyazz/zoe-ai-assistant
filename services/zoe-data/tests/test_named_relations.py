"""Names listed after a relationship noun are kept, not summarised to a count.

Bar scenario S21 (scripts/perf/samantha_bar.py) seeds "My friend Dana Whitfield has two kids,
Mika and Biscuit." and the live store held only "User's friend is named Dana Whitfield" and
"Dana Whitfield has two kids": the children's NAMES were dropped, so "what are Dana's kids
called?" could not be answered. Root causes pinned here (synthetic names only):

  1. person_extractor had no reader for a list after a relationship noun (named_relations.py);
  2. the role guard refused a listed name as an "unstated role" (people_roles);
  3. the person-LLM prompt had no rule against reducing a list to a count.

Real in-memory SQLite for the people graph, an in-memory fake for the memory store. Negative
controls (each goes red when its fix is reverted) are listed in the PR body.
"""
import aiosqlite
import pytest

pytestmark = pytest.mark.ci_safe

import named_relations as nr
import people_roles as pr
import person_extractor as pe
import person_extractor_llm as pel
from memory_service import MemoryRef

USER = "demo_named_relations_user"  # a DEMO user - never a real person
SAID = "My friend Dana Whitfield has two kids, Mika and Biscuit."
FACT = "Dana Whitfield has two kids, Mika and Biscuit."


# -- the pure reader ------------------------------------------------------------------------

def _one(text):
    rels = nr.extract_named_relations(text)
    assert len(rels) == 1, rels
    return rels[0]


def test_the_s21_sentence_keeps_both_names_and_the_count():
    rel = _one(SAID)
    assert (rel.owner, rel.group, rel.names) == ("Dana Whitfield", "child", ("Mika", "Biscuit"))
    assert rel.fact() == FACT


@pytest.mark.parametrize("text,owner,group,names,fact", [
    ("Dana Whitfield's kids are Mika and Biscuit", "Dana Whitfield", "child", ("Mika", "Biscuit"),
     FACT),
    ("Dana has three kids named Mika, Biscuit and Juno", "Dana", "child", ("Mika", "Biscuit", "Juno"),
     "Dana has three kids, Mika, Biscuit and Juno."),
    ("Dana Whitfield, who has a son called Mika, lives in Perth", "Dana Whitfield", "child", ("Mika",),
     "Dana Whitfield has a son, Mika."),
    ("Dana's son is Mika", "Dana", "child", ("Mika",), "Dana has a son, Mika."),
    ("Dana has two sisters: Ana and Bea", "Dana", "sibling", ("Ana", "Bea"),
     "Dana has two sisters, Ana and Bea."),
    ("Dana's parents are Tomas and Ines", "Dana", "parent", ("Tomas", "Ines"),
     "Dana's parents are Tomas and Ines."),
    ("Then Dana has two kids, Mika and Biscuit.", "Dana", "child", ("Mika", "Biscuit"),
     "Dana has two kids, Mika and Biscuit."),
    ("Dana has two dogs called Rex and Fido", "Dana", "pet", ("Rex", "Fido"),
     "Dana has two dogs, Rex and Fido."),
])
def test_named_owner_shapes(text, owner, group, names, fact):
    rel = _one(text)
    assert (rel.owner, rel.group, rel.names) == (owner, group, names)
    assert rel.fact() == fact


@pytest.mark.parametrize("text,group,names,fact", [
    ("I have two kids, Mika and Biscuit.", "child", ("Mika", "Biscuit"),
     "User has two kids, Mika and Biscuit."),
    ("my kids are Mika and Biscuit", "child", ("Mika", "Biscuit"),
     "User has two kids, Mika and Biscuit."),
    ("We have two dogs, Rex and Fido.", "pet", ("Rex", "Fido"), "User has two dogs, Rex and Fido."),
    ("my two sisters, Ana and Bea, are visiting", "sibling", ("Ana", "Bea"),
     "User has two sisters, Ana and Bea."),
])
def test_speaker_owned_lists_have_no_owner_node(text, group, names, fact):
    rel = _one(text)
    assert rel.owner is None and rel.group == group and rel.names == names
    assert rel.fact() == fact


@pytest.mark.parametrize("text", [
    "Dana has two kids",                        # a count with no names: nothing to keep
    "Dana has two kids and they are great",     # no capitalised names
    "Her kids are Mika and Biscuit",            # a pronoun owner needs an antecedent
    "Dana's parents are Italian",               # a nationality is not a name
    "my parents are Greek",
    "my kids are Awesome",
    "my son is Mika",                           # single-name speaker shapes: memory_extractor's templates
    "my dog is Rex",
    "Dana's kids are 7 and 9",
    "Here are my friend's family details, along with a partner and two children.\n\n"
    "Callum Reyes - 14/03/1980\nAnika Reyes - 02/11/1985",      # S22: roles are never guessed
])
def test_non_lists_are_not_read_as_lists(text):
    assert nr.extract_named_relations(text) == []


def test_the_owner_is_never_listed_as_their_own_child():
    assert nr.extract_named_relations("Dana has two kids, Dana and Mika") == [] or \
        "Dana" not in _one("Dana has two kids, Dana and Mika").names


def test_a_trailing_stop_word_is_not_part_of_a_name():
    assert _one("Dana has two kids, Mika and Biscuit Friday.").names == ("Mika", "Biscuit")


# -- the write path: people graph + stored fact ---------------------------------------------

class FakeSvc:
    """MemoryService stand-in: real ingest / review(edit) / list_by_status scoping contract,
    no embeddings (search finds nothing, so reconciliation fail-opens to ADD)."""

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

    async def review(self, mem_id, *, decision, actor, edits=None, note=None, metadata=None,
                     source_excerpt=None, **kw):
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
    await db.execute(
        "CREATE UNIQUE INDEX person_relationships_pair_active "
        "ON person_relationships(user_id, person_a_id, person_b_id) WHERE valid_to IS NULL"
    )
    await db.execute("CREATE TABLE user_portraits (user_id TEXT, portrait_text TEXT)")
    await db.execute(
        """CREATE TABLE person_important_dates (id TEXT PRIMARY KEY, person_id TEXT, user_id TEXT,
           label TEXT, date_type TEXT, month INTEGER, day INTEGER, year INTEGER, mem_id TEXT)"""
    )
    await db.commit()
    return db


async def _edges(db):
    cur = await db.execute(
        "SELECT a.name, b.name, r.rel_type, r.rel_a_to_b FROM person_relationships r "
        "JOIN people a ON a.id=r.person_a_id JOIN people b ON b.id=r.person_b_id "
        "WHERE r.user_id=? AND r.valid_to IS NULL ORDER BY a.name", (USER,))
    return [tuple(r) for r in await cur.fetchall()]


async def _names(db):
    cur = await db.execute("SELECT name FROM people WHERE user_id=? ORDER BY name", (USER,))
    return [r[0] for r in await cur.fetchall()]


async def test_the_s21_sentence_lands_names_in_the_graph_and_in_the_fact(svc):
    db = await _open_db()
    n = await pe.process_text(SAID, user_id=USER, source="conversation", db=db)
    assert n >= 1
    # the fact keeps every name, in the shape correction_apply rewrites
    assert FACT in svc.approved()
    # named people rows linked to the parent: a = the child, b = the parent (as "Mika is Dana's son")
    assert await _names(db) == ["Biscuit", "Dana Whitfield", "Mika"]
    assert await _edges(db) == [
        ("Biscuit", "Dana Whitfield", "parent", "Parent"),
        ("Mika", "Dana Whitfield", "parent", "Parent"),
    ]
    # the fact is linked to the parent's people row, not left as a name slug
    row = next(r for r in svc.rows.values() if r.text == FACT)
    cur = await db.execute("SELECT id FROM people WHERE name='Dana Whitfield'")
    assert row.metadata.get("entity_id") == (await cur.fetchone())[0]
    assert row.metadata.get("entity_type") == "person"


async def test_restating_the_list_does_not_duplicate_people_or_edges(svc):
    db = await _open_db()
    await pe.process_text(SAID, user_id=USER, db=db)
    await pe.process_text(SAID, user_id=USER, db=db)
    assert await _names(db) == ["Biscuit", "Dana Whitfield", "Mika"]
    assert len(await _edges(db)) == 2


async def test_the_recall_question_is_answered_from_the_stored_data(svc, monkeypatch):
    """"What are Dana Whitfield's kids called?" - the cited relational packet (people graph)
    and the stored fact each carry both names."""
    import zoe_memory_compose as zmc

    monkeypatch.setenv("ZOE_MEMORY_COMPOSE_ENABLED", "1")
    db = await _open_db()
    await pe.process_text(SAID, user_id=USER, db=db)
    question = "what are Dana Whitfield's kids called?"
    assert zmc.needs_relational(question)
    block = await zmc.compose_relational_block(USER, question, db)
    packet = "\n".join(block["lines"])
    assert "Mika" in packet and "Biscuit" in packet and "Dana Whitfield" in packet
    assert "[relationship]" in packet
    fact = next(t for t in svc.approved() if "Dana Whitfield" in t)
    assert "Mika" in fact and "Biscuit" in fact


async def test_a_later_pet_correction_takes_biscuit_out_of_the_list(svc, monkeypatch):
    """The S21 flow end to end: the list is stored, then Biscuit is their dog edits the row and
    the edge instead of finding nothing to correct."""
    import correction_apply as ca

    monkeypatch.setenv(ca.ENV, "1")
    db = await _open_db()
    await pe.process_text(SAID, user_id=USER, db=db)
    res = await ca.apply_pet_correction("Biscuit is their dog", USER, svc=svc, db=db)
    assert res is not None and res.kind == "pet"
    live = svc.approved()
    assert "Dana Whitfield has one kid, Mika." in live, live
    assert FACT not in live
    assert ("Biscuit", "Dana Whitfield", "pet", "Pet owner") in await _edges(db)
    assert ("Mika", "Dana Whitfield", "parent", "Parent") in await _edges(db)


async def test_a_child_edge_never_overwrites_a_recorded_pet_edge(svc, monkeypatch):
    import correction_apply as ca

    monkeypatch.setenv(ca.ENV, "1")
    monkeypatch.setenv("ZOE_TEMPORAL_RELATIONSHIPS_ENABLED", "1")  # the flag that would supersede
    db = await _open_db()
    await pe.process_text(SAID, user_id=USER, db=db)
    await ca.apply_pet_correction("Biscuit is their dog", USER, svc=svc, db=db)
    await pe.process_text(SAID, user_id=USER, db=db)   # the list is restated later
    assert ("Biscuit", "Dana Whitfield", "pet", "Pet owner") in await _edges(db)
    assert not any(e[0] == "Biscuit" and e[2] == "parent" for e in await _edges(db))


async def test_a_listed_name_that_is_a_different_contact_is_not_linked(svc):
    db = await _open_db()
    await db.execute("INSERT INTO people (id, user_id, name, deleted) VALUES ('x', ?, 'Mikaela Reyes', 0)",
                     (USER,))
    await db.commit()
    await pe.process_text(SAID, user_id=USER, db=db)
    edges = await _edges(db)
    assert not any(e[0] == "Mikaela Reyes" for e in edges)      # Mika did not attach to Mikaela
    assert ("Biscuit", "Dana Whitfield", "parent", "Parent") in edges
    assert FACT in svc.approved()                                # ... and the fact still keeps Mika


async def test_pets_are_facts_only_never_minted_as_people(svc):
    db = await _open_db()
    await pe.process_text("Dana Whitfield has two dogs called Rex and Fido.", user_id=USER, db=db)
    assert await _names(db) == []
    assert await _edges(db) == []
    assert "Dana Whitfield has two dogs, Rex and Fido." in svc.approved()


async def test_the_speakers_own_list_is_a_fact_not_a_graph_node(svc):
    db = await _open_db()
    await pe.process_text("I have two kids, Mika and Biscuit.", user_id=USER, db=db)
    assert await _names(db) == []
    assert "User has two kids, Mika and Biscuit." in svc.approved()


async def test_a_pronoun_owner_writes_nothing(svc):
    db = await _open_db()
    await pe.process_text("Her kids are Mika and Biscuit.", user_id=USER, db=db)
    assert await _names(db) == [] and svc.approved() == []


async def test_a_failing_graph_write_never_loses_the_fact(svc, monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(pe, "_write_relationship", boom)
    db = await _open_db()
    await pe.process_text(SAID, user_id=USER, db=db)
    assert FACT in svc.approved()


# -- the guards that used to drop a listed name ---------------------------------------------

def test_a_listed_name_has_its_role_stated_by_the_user():
    for name in ("Mika", "Biscuit"):
        assert pr.role_assignment_supported(name, "kid", SAID)
        assert pr.role_assignment_supported(name, "child", SAID)
    assert not pr.role_assignment_supported("Dana", "kid", SAID)       # the owner is not her own kid
    assert not pr.named_role_claim_unsupported("One of Dana Whitfield's kids is named Biscuit", SAID)


def test_a_name_with_its_own_clause_is_not_a_listed_role():
    said = "Jordan is my friend, Casey is the wife"
    assert pr.role_assignment_supported("Casey", "wife", said)
    assert not pr.role_assignment_supported("Jordan", "wife", said)


def test_roles_are_still_never_guessed_for_a_pasted_roster():
    said = ("Here are my friend's family details, along with a partner and two children.\n\n"
            "Callum Reyes - 14/03/1980\nAnika Reyes - 02/11/1985\n"
            "Tobias Reyes- 19/06/2014\nInes Reyes - 30/01/2017")
    assert pr.is_unlabelled_roster(said)
    assert not pr.role_assignment_supported("Tobias", "child", said)


def test_both_person_llm_prompts_forbid_reducing_a_list_to_a_count():
    for prompt in (pel._EXTRACTION_PROMPT, pel._EXTRACTION_PROMPT_CONF):
        assert "NEVER drop names" in prompt and "never reduce it to a count" in prompt
