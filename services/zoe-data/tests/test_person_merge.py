"""Person merge / entity resolution — roadmap item 4 of ADR-relationship-memory.

Proves ``person_merge.merge_person`` folds an ``is_partial`` stub into a real contact
and re-points ALL of its data, on a real in-memory SQLite DB built from the 0006/0007
person schema + the 0015 temporal (partial current-edge index) migration — no fakes for
the storage engine, no model loads, no PostgreSQL. Covers:

  * satellite re-point (important_dates / activities / gift_ideas / bucket_list moved
    source→target);
  * ``introduced_by_person_id`` self-FK re-pointed;
  * relationship edges re-pointed;
  * **self-edge dropped** (source related to target → after merge no self-edge);
  * **duplicate CURRENT edge de-duped** (source & target both have a current edge to a
    third person C → exactly one survives, the partial unique index stays intact);
  * people fields merged (target NULL filled from source; is_partial resolved);
  * source soft-deleted;
  * **owner-scoping** (cannot merge another user's person → PersonMergeError, no
    cross-user re-point);
  * endpoint flag OFF → disabled (404 before DB work); flag ON → correct payload +
    feature-access enforced.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import aiosqlite
import pytest

import person_merge

# Slim-dep-safe (real in-memory sqlite, no mempalace/model loads) → runs on the
# GitHub CI lane via -m ci_safe. See tests/AGENTS.md (marker-based selection).
pytestmark = pytest.mark.ci_safe

VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"


# ── DB scaffolding: 0006/0007 person schema + 0015 partial index on SQLite ───


async def _open_db():
    """In-memory SQLite with the people + satellite + relationships schema and the
    0015 partial current-edge unique index — the exact surface merge_person touches."""
    db = await aiosqlite.connect(":memory:")
    db.row_factory = aiosqlite.Row

    await db.execute(
        """
        CREATE TABLE people (
            id           TEXT PRIMARY KEY,
            user_id      TEXT NOT NULL,
            name         TEXT NOT NULL,
            relationship TEXT,
            how_we_met   TEXT,
            first_met_date TEXT,
            notes        TEXT,
            context      TEXT,
            visibility   TEXT,
            deleted      INTEGER NOT NULL DEFAULT 0,
            is_partial   INTEGER NOT NULL DEFAULT 0,
            introduced_by_person_id TEXT
        )
        """
    )
    await db.execute(
        """
        CREATE TABLE person_relationships (
            id           TEXT PRIMARY KEY,
            user_id      TEXT NOT NULL,
            person_a_id  TEXT NOT NULL,
            person_b_id  TEXT NOT NULL,
            rel_type     TEXT NOT NULL,
            rel_a_to_b   TEXT NOT NULL,
            rel_b_to_a   TEXT NOT NULL,
            rel_group    TEXT NOT NULL,
            notes        TEXT,
            valid_from   TEXT,
            valid_to     TEXT,
            superseded_by TEXT,
            created_at   TEXT NOT NULL,
            updated_at   TEXT NOT NULL
        )
        """
    )
    await db.execute(
        "CREATE UNIQUE INDEX person_relationships_pair_active "
        "ON person_relationships(user_id, person_a_id, person_b_id) "
        "WHERE valid_to IS NULL"
    )
    for tbl, extra in (
        ("person_activities", "activity_type TEXT, description TEXT"),
        ("person_important_dates", "label TEXT, date_type TEXT"),
        ("person_gift_ideas", "description TEXT"),
        ("person_bucket_list", "description TEXT"),
    ):
        await db.execute(
            f"CREATE TABLE {tbl} (id TEXT PRIMARY KEY, person_id TEXT NOT NULL, "
            f"user_id TEXT NOT NULL, {extra})"
        )
    await db.commit()
    return db


async def _seed_person(db, pid, user_id="jason", name=None, is_partial=0, **cols):
    name = name or pid
    keys = ["id", "user_id", "name", "is_partial"] + list(cols.keys())
    vals = [pid, user_id, name, is_partial] + list(cols.values())
    ph = ",".join("?" * len(keys))
    await db.execute(
        f"INSERT INTO people ({','.join(keys)}) VALUES ({ph})", tuple(vals)
    )
    await db.commit()


async def _seed_edge(db, eid, a, b, user_id="jason", rel_type="friend", valid_to=None):
    await db.execute(
        "INSERT INTO person_relationships (id, user_id, person_a_id, person_b_id, "
        "rel_type, rel_a_to_b, rel_b_to_a, rel_group, valid_from, valid_to, "
        "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (eid, user_id, a, b, rel_type, "R", "R", "social",
         "2020-01-01", valid_to, "2020-01-01", "2020-01-01"),
    )
    await db.commit()


async def _rows(db, sql, params=()):
    async with db.execute(sql, params) as cur:
        return [dict(r) for r in await cur.fetchall()]


# ── 1. Satellite + introduced_by re-point ────────────────────────────────────


@pytest.mark.asyncio
async def test_satellite_and_introduced_by_repointed():
    db = await _open_db()
    try:
        await _seed_person(db, "src", is_partial=1)
        await _seed_person(db, "tgt", is_partial=0)
        await _seed_person(db, "kid", introduced_by_person_id="src")
        await db.execute(
            "INSERT INTO person_activities (id, person_id, user_id, activity_type, "
            "description) VALUES ('a1','src','jason','hike','trail')"
        )
        await db.execute(
            "INSERT INTO person_important_dates (id, person_id, user_id, label) "
            "VALUES ('d1','src','jason','bday')"
        )
        await db.execute(
            "INSERT INTO person_gift_ideas (id, person_id, user_id, description) "
            "VALUES ('g1','src','jason','socks')"
        )
        await db.execute(
            "INSERT INTO person_bucket_list (id, person_id, user_id, description) "
            "VALUES ('b1','src','jason','skydive')"
        )
        await db.commit()

        result = await person_merge.merge_person(db, "jason", "src", "tgt")

        for tbl in ("person_activities", "person_important_dates",
                    "person_gift_ideas", "person_bucket_list"):
            rows = await _rows(db, f"SELECT person_id FROM {tbl}")
            assert all(r["person_id"] == "tgt" for r in rows), tbl
            assert result["repointed"][tbl] == 1

        kid = await _rows(db, "SELECT introduced_by_person_id FROM people WHERE id='kid'")
        assert kid[0]["introduced_by_person_id"] == "tgt"
        assert result["repointed"]["people.introduced_by_person_id"] == 1
    finally:
        await db.close()


# ── 2. Relationship edge re-point ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_relationship_edges_repointed():
    db = await _open_db()
    try:
        await _seed_person(db, "src")
        await _seed_person(db, "tgt")
        await _seed_person(db, "carol")
        # src—carol (src on a side) and carol—src (src on b side).
        await _seed_edge(db, "e1", "src", "carol")
        await _seed_edge(db, "e2", "carol", "src", rel_type="colleague")

        result = await person_merge.merge_person(db, "jason", "src", "tgt")

        edges = await _rows(db, "SELECT person_a_id, person_b_id FROM person_relationships "
                                "ORDER BY id")
        assert edges == [
            {"person_a_id": "tgt", "person_b_id": "carol"},
            {"person_a_id": "carol", "person_b_id": "tgt"},
        ]
        assert result["repointed"]["person_relationships"] == 2
        assert result["deduped_edges"] == 0
        assert result["dropped_self_edges"] == 0
    finally:
        await db.close()


# ── 3. Self-edge dropped ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_self_edge_dropped():
    db = await _open_db()
    try:
        await _seed_person(db, "src")
        await _seed_person(db, "tgt")
        # The stub was related directly to the real person it turns out to be.
        await _seed_edge(db, "e1", "src", "tgt", rel_type="same_person")

        result = await person_merge.merge_person(db, "jason", "src", "tgt")

        edges = await _rows(db, "SELECT id FROM person_relationships")
        assert edges == []  # a person can't relate to themselves → dropped
        assert result["dropped_self_edges"] == 1
        assert result["repointed"]["person_relationships"] == 0
    finally:
        await db.close()


# ── 4. Duplicate CURRENT edge de-duped (partial index preserved) ─────────────


@pytest.mark.asyncio
async def test_duplicate_current_edge_deduped():
    db = await _open_db()
    try:
        await _seed_person(db, "src")
        await _seed_person(db, "tgt")
        await _seed_person(db, "carol")
        # Both src and tgt have a CURRENT edge to carol → re-point would collide.
        await _seed_edge(db, "e_src", "src", "carol", rel_type="friend")
        await _seed_edge(db, "e_tgt", "tgt", "carol", rel_type="cousin")

        result = await person_merge.merge_person(db, "jason", "src", "tgt")

        current = await _rows(
            db,
            "SELECT id, person_a_id, person_b_id FROM person_relationships "
            "WHERE valid_to IS NULL AND person_a_id='tgt' AND person_b_id='carol'",
        )
        # Exactly ONE current edge for (tgt, carol) → partial unique index intact.
        assert len(current) == 1
        assert current[0]["id"] == "e_tgt"  # target's own edge kept
        assert result["deduped_edges"] == 1
        assert result["repointed"]["person_relationships"] == 0
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_historical_edge_not_treated_as_duplicate():
    """A superseded (valid_to set) source edge never collides — it is re-pointed."""
    db = await _open_db()
    try:
        await _seed_person(db, "src")
        await _seed_person(db, "tgt")
        await _seed_person(db, "carol")
        await _seed_edge(db, "e_tgt", "tgt", "carol")  # tgt's current edge
        await _seed_edge(db, "e_src_old", "src", "carol", valid_to="2021-01-01")  # historical

        result = await person_merge.merge_person(db, "jason", "src", "tgt")

        # Historical source edge re-pointed to tgt; current tgt edge untouched.
        all_edges = await _rows(
            db, "SELECT id, person_a_id, person_b_id, valid_to FROM person_relationships "
                "ORDER BY id")
        assert len(all_edges) == 2
        old = next(e for e in all_edges if e["id"] == "e_src_old")
        assert old["person_a_id"] == "tgt" and old["valid_to"] == "2021-01-01"
        assert result["repointed"]["person_relationships"] == 1
        assert result["deduped_edges"] == 0
        # Partial index still holds: exactly one current (tgt, carol) edge.
        current = await _rows(
            db, "SELECT id FROM person_relationships WHERE valid_to IS NULL")
        assert len(current) == 1
    finally:
        await db.close()


# ── 5. People fields merged + source soft-deleted ────────────────────────────


@pytest.mark.asyncio
async def test_people_fields_merged_and_source_deleted():
    db = await _open_db()
    try:
        # source has data; target has blanks for those fields.
        await _seed_person(
            db, "src", is_partial=0,
            how_we_met="met at gym", first_met_date="2019-05", notes="likes tea",
            relationship="friend",
        )
        await _seed_person(
            db, "tgt", is_partial=1,
            how_we_met="", first_met_date=None, notes="   ", relationship=None,
        )

        await person_merge.merge_person(db, "jason", "src", "tgt")

        tgt = (await _rows(db, "SELECT * FROM people WHERE id='tgt'"))[0]
        assert tgt["how_we_met"] == "met at gym"      # NULL/empty filled from source
        assert tgt["first_met_date"] == "2019-05"
        assert tgt["notes"] == "likes tea"
        assert tgt["relationship"] == "friend"
        # target was partial, source was full → survivor is NOT partial.
        assert tgt["is_partial"] == 0
        assert tgt["deleted"] == 0

        # Source soft-deleted.
        src = (await _rows(db, "SELECT deleted FROM people WHERE id='src'"))[0]
        assert src["deleted"] == 1
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_target_fields_win_over_source():
    db = await _open_db()
    try:
        await _seed_person(db, "src", how_we_met="gym", notes="src notes")
        await _seed_person(db, "tgt", how_we_met="work", notes="tgt notes")

        await person_merge.merge_person(db, "jason", "src", "tgt")

        tgt = (await _rows(db, "SELECT how_we_met, notes FROM people WHERE id='tgt'"))[0]
        # Target already had values → source does NOT overwrite them.
        assert tgt["how_we_met"] == "work"
        assert tgt["notes"] == "tgt notes"
    finally:
        await db.close()


# ── 6. Validation + owner scoping ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_same_id_rejected():
    db = await _open_db()
    try:
        await _seed_person(db, "p1")
        with pytest.raises(person_merge.PersonMergeError):
            await person_merge.merge_person(db, "jason", "p1", "p1")
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_missing_person_rejected():
    db = await _open_db()
    try:
        await _seed_person(db, "tgt")
        with pytest.raises(person_merge.PersonMergeError):
            await person_merge.merge_person(db, "jason", "ghost", "tgt")
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_owner_scoping_no_cross_user_merge():
    """A source owned by another user is invisible → merge rejected, nothing re-pointed."""
    db = await _open_db()
    try:
        # src belongs to mallory, tgt belongs to jason.
        await _seed_person(db, "src", user_id="mallory")
        await _seed_person(db, "tgt", user_id="jason")
        await db.execute(
            "INSERT INTO person_activities (id, person_id, user_id, activity_type, "
            "description) VALUES ('a1','src','mallory','hike','trail')"
        )
        await db.commit()

        with pytest.raises(person_merge.PersonMergeError):
            await person_merge.merge_person(db, "jason", "src", "tgt")

        # mallory's activity was NOT re-pointed.
        act = await _rows(db, "SELECT person_id FROM person_activities WHERE id='a1'")
        assert act[0]["person_id"] == "src"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_owner_scoping_only_own_edges_repointed():
    """Even with a valid same-user merge, another user's edge on the same ids is untouched."""
    db = await _open_db()
    try:
        await _seed_person(db, "src", user_id="jason")
        await _seed_person(db, "tgt", user_id="jason")
        await _seed_person(db, "carol", user_id="jason")
        await _seed_edge(db, "mine", "src", "carol", user_id="jason")
        # A different user happens to have an edge with the same person ids.
        await _seed_edge(db, "theirs", "src", "carol", user_id="mallory")

        await person_merge.merge_person(db, "jason", "src", "tgt")

        theirs = await _rows(
            db, "SELECT person_a_id FROM person_relationships WHERE id='theirs'")
        assert theirs[0]["person_a_id"] == "src"  # untouched
        mine = await _rows(
            db, "SELECT person_a_id FROM person_relationships WHERE id='mine'")
        assert mine[0]["person_a_id"] == "tgt"
    finally:
        await db.close()


# ── 7. Flag gate ─────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clear_flag():
    prev = os.environ.pop("ZOE_PERSON_MERGE_ENABLED", None)
    yield
    if prev is None:
        os.environ.pop("ZOE_PERSON_MERGE_ENABLED", None)
    else:
        os.environ["ZOE_PERSON_MERGE_ENABLED"] = prev


def test_person_merge_enabled_flag():
    assert person_merge.person_merge_enabled() is False
    os.environ["ZOE_PERSON_MERGE_ENABLED"] = "1"
    assert person_merge.person_merge_enabled() is True
    os.environ["ZOE_PERSON_MERGE_ENABLED"] = "off"
    assert person_merge.person_merge_enabled() is False


# ── 8. Endpoint: flag gate + feature access + payload ────────────────────────


class _FakeBroadcaster:
    def __init__(self):
        self.calls = []

    async def broadcast(self, *a, **k):
        self.calls.append((a, k))


@pytest.mark.asyncio
async def test_endpoint_flag_off_returns_404(monkeypatch):
    from fastapi import HTTPException
    import routers.people as people_router

    called = {"feature_access": False}

    async def _fake_access(*a, **k):
        called["feature_access"] = True

    monkeypatch.setattr(people_router, "require_feature_access", _fake_access)
    os.environ.pop("ZOE_PERSON_MERGE_ENABLED", None)

    with pytest.raises(HTTPException) as exc:
        await people_router.merge_person_endpoint(
            "src", "tgt", user={"user_id": "jason"}, db=object()
        )
    assert exc.value.status_code == 404
    # Disabled BEFORE any DB work / feature-access call.
    assert called["feature_access"] is False


@pytest.mark.asyncio
async def test_endpoint_flag_on_merges_and_enforces_access(monkeypatch):
    import routers.people as people_router

    db = await _open_db()
    try:
        await _seed_person(db, "src", is_partial=1)
        await _seed_person(db, "tgt", is_partial=0)
        await db.execute(
            "INSERT INTO person_activities (id, person_id, user_id, activity_type, "
            "description) VALUES ('a1','src','jason','hike','trail')"
        )
        await db.commit()

        access = {"checked": False}

        async def _fake_access(_db, _user, feature, action):
            access["checked"] = (feature, action)

        fake_bc = _FakeBroadcaster()
        monkeypatch.setattr(people_router, "require_feature_access", _fake_access)
        monkeypatch.setattr(people_router, "broadcaster", fake_bc)
        os.environ["ZOE_PERSON_MERGE_ENABLED"] = "1"

        result = await people_router.merge_person_endpoint(
            "src", "tgt", user={"user_id": "jason"}, db=db
        )

        assert result["source_id"] == "src" and result["target_id"] == "tgt"
        assert result["repointed"]["person_activities"] == 1
        assert access["checked"] == ("people", "update")
        assert fake_bc.calls  # a people:merged broadcast fired
        # source soft-deleted through the endpoint path.
        src = await _rows(db, "SELECT deleted FROM people WHERE id='src'")
        assert src[0]["deleted"] == 1
    finally:
        await db.close()
