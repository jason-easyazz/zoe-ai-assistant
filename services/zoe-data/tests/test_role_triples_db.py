"""``role_triples.fetch`` - the SQL half: the owner's own people graph, current edges only, user isolation, a schema without the authority
column. REAL SQL against an in-memory database reached through the same ``db_pool.get_db_ctx`` seam production uses. Synthetic names."""
from __future__ import annotations

import contextlib

import pytest

aiosqlite = pytest.importorskip("aiosqlite")

import db_pool  # noqa: E402
import role_triples as rt  # noqa: E402

pytestmark = pytest.mark.ci_safe

OWNER, STRANGER = "demo-owner", "demo-stranger"
_OPEN = []


@pytest.fixture(autouse=True)
async def _close():
    yield
    while _OPEN:
        await _OPEN.pop().close()


async def _db(*, authority: bool):
    db = await aiosqlite.connect(":memory:")
    _OPEN.append(db)
    db.row_factory = aiosqlite.Row
    await db.execute("CREATE TABLE people (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, name TEXT NOT NULL, relationship TEXT, "
                     "deleted INTEGER NOT NULL DEFAULT 0)")
    await db.execute("CREATE TABLE person_relationships (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, person_a_id TEXT NOT NULL, "
                     "person_b_id TEXT NOT NULL, rel_type TEXT NOT NULL, rel_a_to_b TEXT NOT NULL, rel_b_to_a TEXT NOT NULL, "
                     "rel_group TEXT NOT NULL, valid_to TEXT" + (", authority TEXT" if authority else "") + ")")
    for pid, user, name, rel, deleted in (
            ("p-anika", OWNER, "Anika Reyes", "Mother", 0), ("p-callum", OWNER, "Callum Reyes", "", 0),
            ("p-ghost", OWNER, "Ghost Person", "Sister", 1), ("p-other", STRANGER, "Other Person", "Wife", 0)):
        await db.execute("INSERT INTO people (id, user_id, name, relationship, deleted) VALUES (?,?,?,?,?)", (pid, user, name, rel, deleted))

    async def edge(eid, user, a, b, rel_type, valid_to=None, auth=None):
        cols = "id,user_id,person_a_id,person_b_id,rel_type,rel_a_to_b,rel_b_to_a,rel_group,valid_to" + (",authority" if authority else "")
        vals = [eid, user, a, b, rel_type, "x", "y", "family", valid_to] + ([auth] if authority else [])
        await db.execute(f"INSERT INTO person_relationships ({cols}) VALUES ({','.join('?' * len(vals))})", vals)

    await edge("e1", OWNER, "p-anika", "p-callum", "spouse", auth="user_stated")             # current
    await edge("e2", OWNER, "p-callum", "p-anika", "friend", valid_to="2026-01-01")           # CLOSED: history, not a triple
    await edge("e3", OWNER, "p-anika", "p-ghost", "friend", auth="inferred")                  # a model's guess: counts for nothing
    await edge("e4", STRANGER, "p-other", "p-anika", "spouse")                                # another user's edge
    await db.commit()

    @contextlib.asynccontextmanager
    async def ctx():
        yield db

    return db, ctx


@pytest.mark.parametrize("authority", [True, False])
async def test_fetch_reads_the_owners_current_graph_only(monkeypatch, authority):
    db, ctx = await _db(authority=authority)
    monkeypatch.setattr(db_pool, "get_db_ctx", ctx)
    got = await rt.fetch(OWNER)
    assert got.complete is True
    kinds = {(t.person_id, t.kin, t.owner) for t in got.triples}
    assert ("p-anika", "mother", "user") in kinds                         # people.relationship: the account owner's edge
    assert ("p-callum", "spouse", "p-anika") in kinds and ("p-anika", "spouse", "p-callum") in kinds     # the current edge, both ways
    assert ("p-callum", "friend", "p-anika") not in kinds                  # the closed edge
    assert not any(t.person_id in ("p-ghost", "p-other") for t in got.triples)      # a deleted person; another user's person
    assert set(got.names) == {"p-anika", "p-callum"}
    assert got.supports("p-anika", "mum", "user") and not got.supports("p-anika", "wife", "user")
    other = await rt.fetch(STRANGER)
    assert {t.person_id for t in other.triples} == {"p-other"} and set(other.names) == {"p-other"}


async def test_the_inferred_edge_is_dropped_when_the_schema_has_the_authority_column(monkeypatch):
    db, ctx = await _db(authority=True)
    monkeypatch.setattr(db_pool, "get_db_ctx", ctx)
    got = await rt.fetch(OWNER)
    assert not any(t.person_id == "p-ghost" or t.owner == "p-ghost" for t in got.triples)
