"""End-to-end INTEGRATION test for the relationship-memory features (flags ON).

The unit suites prove each feature in isolation; this proves they COMPOSE — the
exact interaction the flag-enable gate cares about:

  temporal supersession → graph traversal over those edges → person-merge
  re-pointing temporal edges under the partial current-edge unique index →
  re-traversal stays coherent.

Runs on a demo user in an isolated in-memory SQLite DB (slim-dep, no live system,
no model loads). All three flags are enabled via monkeypatch:
``ZOE_TEMPORAL_RELATIONSHIPS_ENABLED`` / ``ZOE_RELATIONSHIP_GRAPH_ENABLED`` /
``ZOE_PERSON_MERGE_ENABLED``.
"""
import uuid

import aiosqlite
import pytest

pytestmark = pytest.mark.ci_safe  # slim-dep-safe → GitHub -m ci_safe lane (see tests/AGENTS.md)

import person_extractor as pe
import relationship_graph as rg
import person_merge as pm

USER = "demo_lab_user"  # a DEMO user — never a real person


async def _open_db():
    """In-memory SQLite with the person schema (0007 + 0015 temporal columns +
    the partial current-edge index) and the person-merge satellite tables."""
    db = await aiosqlite.connect(":memory:")
    db.row_factory = aiosqlite.Row
    await db.execute(
        """CREATE TABLE people (
            id TEXT PRIMARY KEY, user_id TEXT NOT NULL, name TEXT NOT NULL,
            relationship TEXT, how_we_met TEXT, first_met_date TEXT, notes TEXT,
            circle TEXT, context TEXT, visibility TEXT, deleted INTEGER NOT NULL DEFAULT 0,
            is_partial INTEGER NOT NULL DEFAULT 0, introduced_by_person_id TEXT,
            created_at TEXT, updated_at TEXT)"""
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
    for t in ("person_activities", "person_important_dates", "person_gift_ideas", "person_bucket_list"):
        await db.execute(f"CREATE TABLE {t} (id TEXT PRIMARY KEY, person_id TEXT, user_id TEXT, description TEXT)")
    await db.commit()
    return db


async def _pid(db, name):
    async with db.execute("SELECT id FROM people WHERE user_id=? AND name=? AND deleted=0", (USER, name)) as c:
        r = await c.fetchone()
    return r[0] if r else None


@pytest.mark.asyncio
async def test_relationship_features_compose_with_flags_on(monkeypatch):
    monkeypatch.setenv("ZOE_TEMPORAL_RELATIONSHIPS_ENABLED", "1")
    monkeypatch.setenv("ZOE_RELATIONSHIP_GRAPH_ENABLED", "1")
    monkeypatch.setenv("ZOE_PERSON_MERGE_ENABLED", "1")
    assert pe.temporal_relationships_enabled()
    assert rg.relationship_graph_enabled()
    assert pm.person_merge_enabled()

    db = await _open_db()
    try:
        # 1. TEMPORAL — a relationship, then a CHANGED one supersedes it.
        await pe._write_relationship(USER, "Sarah", "Tom", "sibling", "family", db)
        sarah, tom = await _pid(db, "Sarah"), await _pid(db, "Tom")
        assert sarah and tom, "stubs auto-created for unknown names"

        await pe._write_relationship(USER, "Sarah", "Tom", "spouse", "family", db)  # rel_type changes
        async with db.execute(
            "SELECT rel_type, valid_to, superseded_by, id FROM person_relationships WHERE user_id=?", (USER,)
        ) as c:
            edges = [dict(r) for r in await c.fetchall()]
        current = [e for e in edges if e["valid_to"] is None]
        superseded = [e for e in edges if e["valid_to"] is not None]
        assert len(current) == 1 and current[0]["rel_type"] == "spouse"
        assert len(superseded) == 1 and superseded[0]["rel_type"] == "sibling"
        assert superseded[0]["superseded_by"] == current[0]["id"]
        assert len(edges) == 2, "history preserved (no data loss on change)"

        # 2. GRAPH — multi-hop traversal over those edges, owner-scoped.
        await pe._write_relationship(USER, "Tom", "Bob", "friend", "personal", db)
        bob = await _pid(db, "Bob")
        hood = {h["person_id"]: h["depth"] for h in await rg.neighbors(db, USER, sarah, max_depth=2, limit=50)}
        assert hood.get(tom) == 1 and hood.get(bob) == 2
        await pe._write_relationship("other_user", "Sarah", "Zed", "friend", "personal", db)  # foreign edge
        hood2 = {h["person_id"] for h in await rg.neighbors(db, USER, sarah, max_depth=2, limit=50)}
        assert hood2 == {tom, bob}, "owner-scoped: another user's edges never traversed"

        # 3. PERSON-MERGE — fold the Tom stub into a real contact.
        await db.execute(
            "INSERT INTO person_activities (id, person_id, user_id, description) VALUES ('a1', ?, ?, 'x')",
            (tom, USER),
        )
        real_tom = "real_tom_" + uuid.uuid4().hex[:8]
        await db.execute(
            "INSERT INTO people (id, user_id, name, is_partial, deleted, visibility) VALUES (?,?,?,0,0,'family')",
            (real_tom, USER, "Tom Smith"),
        )
        await db.commit()
        result = await pm.merge_person(db, USER, tom, real_tom)
        async with db.execute("SELECT person_id FROM person_activities WHERE id='a1'") as c:
            assert (await c.fetchone())[0] == real_tom, "satellite re-pointed stub→real"
        async with db.execute("SELECT deleted, updated_at FROM people WHERE id=?", (tom,)) as c:
            stub = await c.fetchone()
        assert stub["deleted"] == 1 and stub["updated_at"] is not None
        assert isinstance(result["repointed"], dict)

        # 4. POST-MERGE — graph reaches the REAL Tom, not the deleted stub.
        ids = {h["person_id"] for h in await rg.neighbors(db, USER, sarah, max_depth=2, limit=50)}
        assert real_tom in ids and tom not in ids
    finally:
        await db.close()
