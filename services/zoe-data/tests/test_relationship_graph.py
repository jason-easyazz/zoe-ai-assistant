"""ADR increment 3 — bounded, read-only multi-hop relationship traversal.

These run the REAL ``WITH RECURSIVE`` engine (``relationship_graph.neighbors``)
against a REAL in-memory SQLite seeded with ``people`` + ``person_relationships``
— not a fake SQL matcher — so the recursion, cycle guard, depth/limit bounds, and
owner-scoping are proven against an actual SQL engine (the same portable SQL that
runs on production PostgreSQL via AsyncpgCompat's ?->$N rewrite).

Proven invariants:
  * 1-hop and 2-hop neighbours returned at the correct depth;
  * ``max_depth`` bound respected (a 3-hop node is excluded at depth=2);
  * cycle safety — a triangle A-B-C-A terminates; each node once, at min depth;
  * ``limit`` respected; hard caps clamp runaway input (depth=99 -> no runaway);
  * owner-scoping — another user's edges are never traversed/returned;
  * unknown/empty start -> empty result (not an error);
  * endpoint: flag OFF -> disabled 404; flag ON -> correct payload;
    require_feature_access enforced (guest blocked).
"""
from __future__ import annotations

import aiosqlite
import pytest
import pytest_asyncio

import relationship_graph as rg


# ── Real in-memory SQLite fixture ──────────────────────────────────────────


async def _make_db(edges, *, people=None, extra_users=None):
    """Open an aiosqlite conn seeded with people + person_relationships.

    ``edges`` = list of (rel_id, user_id, a_id, b_id, label_ab, label_ba).
    People are auto-created (owner = 'jason') for every id mentioned unless an
    explicit ``people`` mapping is given. ``extra_users`` seeds people owned by
    other users so cross-user scoping can be checked.
    """
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await conn.execute(
        "CREATE TABLE people ("
        " id TEXT PRIMARY KEY, user_id TEXT, name TEXT,"
        " visibility TEXT DEFAULT 'family',"
        " deleted INTEGER DEFAULT 0, is_partial INTEGER DEFAULT 0)"
    )
    await conn.execute(
        "CREATE TABLE person_relationships ("
        " id TEXT PRIMARY KEY, user_id TEXT, person_a_id TEXT, person_b_id TEXT,"
        " rel_type TEXT, rel_a_to_b TEXT, rel_b_to_a TEXT, rel_group TEXT,"
        " notes TEXT, created_at TEXT, updated_at TEXT)"
    )

    seeded: dict[str, tuple] = {}
    if people:
        for pid, meta in people.items():
            seeded[pid] = (
                pid,
                meta.get("user_id", "jason"),
                meta.get("name", pid),
                meta.get("deleted", 0),
                meta.get("is_partial", 0),
            )
    # Auto-seed any person referenced by an edge but not explicitly given.
    for (_rid, uid, a, b, _lab, _lba) in edges:
        for pid in (a, b):
            seeded.setdefault(pid, (pid, uid, pid.upper(), 0, 0))
    for pid, name in (extra_users or {}).items():
        seeded[pid] = (pid, "eve", name, 0, 0)

    for vals in seeded.values():
        await conn.execute(
            "INSERT OR IGNORE INTO people (id, user_id, name, deleted, is_partial)"
            " VALUES (?,?,?,?,?)",
            vals,
        )
    for (rid, uid, a, b, lab, lba) in edges:
        await conn.execute(
            "INSERT INTO person_relationships"
            " (id, user_id, person_a_id, person_b_id, rel_type, rel_a_to_b,"
            "  rel_b_to_a, rel_group, notes, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (rid, uid, a, b, "rel", lab, lba, "family", None, "t", "t"),
        )
    await conn.commit()
    return conn


# ── Engine: depths ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_one_and_two_hop_depths():
    # A - B - C - D  (a chain); start at A.
    db = await _make_db([
        ("r1", "jason", "A", "B", "Friend", "Friend"),
        ("r2", "jason", "B", "C", "Colleague", "Colleague"),
        ("r3", "jason", "C", "D", "Neighbour", "Neighbour"),
    ])
    try:
        res = await rg.neighbors(db, "jason", "A", max_depth=2, limit=50)
    finally:
        await db.close()
    by_id = {r["person_id"]: r["depth"] for r in res}
    assert by_id == {"B": 1, "C": 2}  # D is 3 hops -> excluded at depth=2
    # via_label surfaced (best-effort) for the direct edge.
    b = next(r for r in res if r["person_id"] == "B")
    assert b["name"] == "B" and b["via_label"] == "Friend"


@pytest.mark.asyncio
async def test_bidirectional_traversal():
    # Edge stored as B->A; starting at A must still reach B (edges are a<->b).
    db = await _make_db([("r1", "jason", "B", "A", "Parent", "Child")])
    try:
        res = await rg.neighbors(db, "jason", "A", max_depth=2)
    finally:
        await db.close()
    assert [r["person_id"] for r in res] == ["B"]
    assert res[0]["depth"] == 1


@pytest.mark.asyncio
async def test_max_depth_bound_excludes_deeper():
    db = await _make_db([
        ("r1", "jason", "A", "B", "x", "x"),
        ("r2", "jason", "B", "C", "x", "x"),
        ("r3", "jason", "C", "D", "x", "x"),
    ])
    try:
        d1 = await rg.neighbors(db, "jason", "A", max_depth=1)
        d3 = await rg.neighbors(db, "jason", "A", max_depth=3)
    finally:
        await db.close()
    assert [r["person_id"] for r in d1] == ["B"]
    assert {r["person_id"] for r in d3} == {"B", "C", "D"}


# ── Engine: cycle safety ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_triangle_cycle_terminates_no_dupes():
    # Triangle A-B-C-A. Must terminate; each node once at its min depth; start
    # never reported back as its own neighbour.
    db = await _make_db([
        ("r1", "jason", "A", "B", "x", "x"),
        ("r2", "jason", "B", "C", "x", "x"),
        ("r3", "jason", "C", "A", "x", "x"),
    ])
    try:
        res = await rg.neighbors(db, "jason", "A", max_depth=4, limit=200)
    finally:
        await db.close()
    ids = [r["person_id"] for r in res]
    assert sorted(ids) == ["B", "C"]        # no duplicates, A excluded
    assert len(ids) == len(set(ids))
    by_id = {r["person_id"]: r["depth"] for r in res}
    assert by_id["B"] == 1 and by_id["C"] == 1  # both reachable in one hop


@pytest.mark.asyncio
async def test_dense_cycles_still_terminate():
    # Fully-connected quad K4 — many cycles; must still return a bounded result.
    ids = ["A", "B", "C", "D"]
    edges = []
    k = 0
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            k += 1
            edges.append((f"r{k}", "jason", ids[i], ids[j], "x", "x"))
    db = await _make_db(edges)
    try:
        res = await rg.neighbors(db, "jason", "A", max_depth=4, limit=200)
    finally:
        await db.close()
    got = sorted(r["person_id"] for r in res)
    assert got == ["B", "C", "D"]           # each once, all at depth 1


# ── Engine: limit + hard caps ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_limit_respected():
    # Star: A connected to 5 leaves.
    edges = [(f"r{i}", "jason", "A", f"L{i}", "x", "x") for i in range(5)]
    db = await _make_db(edges)
    try:
        res = await rg.neighbors(db, "jason", "A", max_depth=1, limit=3)
    finally:
        await db.close()
    assert len(res) == 3  # limit honoured


@pytest.mark.asyncio
async def test_hard_caps_clamp_runaway_input():
    # Depth 99 / limit 9999 must be clamped, not run away. Chain of 6 (A..F);
    # with depth clamped to 4, F (5 hops) is excluded.
    chain = ["A", "B", "C", "D", "E", "F"]
    edges = [
        (f"r{i}", "jason", chain[i], chain[i + 1], "x", "x")
        for i in range(len(chain) - 1)
    ]
    db = await _make_db(edges)
    try:
        res = await rg.neighbors(db, "jason", "A", max_depth=99, limit=9999)
    finally:
        await db.close()
    by_id = {r["person_id"]: r["depth"] for r in res}
    # depth clamped to _MAX_DEPTH_CAP (4): B..E reachable, F (5 hops) excluded.
    assert set(by_id) == {"B", "C", "D", "E"}
    assert "F" not in by_id
    assert max(by_id.values()) == rg._MAX_DEPTH_CAP == 4


def test_clamp_helpers_are_bounded():
    assert rg._clamp_depth(99) == 4
    assert rg._clamp_depth(0) == 1
    assert rg._clamp_depth(-5) == 1
    assert rg._clamp_depth("nope") == rg._DEFAULT_DEPTH
    assert rg._clamp_limit(10_000) == 200
    assert rg._clamp_limit(0) == 1
    assert rg._clamp_limit("nope") == rg._DEFAULT_LIMIT


# ── Engine: owner-scoping ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_owner_scoping_never_traverses_other_user():
    # jason: A-B. eve: A-Z (SHARED person id 'A' but eve's own edge/people).
    db = await _make_db(
        [
            ("r1", "jason", "A", "B", "x", "x"),
            ("r2", "eve", "A", "Z", "x", "x"),
        ],
        people={
            "A": {"user_id": "jason", "name": "Alice"},
            "B": {"user_id": "jason", "name": "Bob"},
            "Z": {"user_id": "eve", "name": "SecretZed"},
        },
    )
    try:
        res = await rg.neighbors(db, "jason", "A", max_depth=4, limit=200)
    finally:
        await db.close()
    ids = {r["person_id"] for r in res}
    assert ids == {"B"}              # eve's edge to Z never traversed
    assert "Z" not in ids
    assert all(r["name"] != "SecretZed" for r in res)


@pytest.mark.asyncio
async def test_soft_deleted_neighbour_dropped():
    db = await _make_db(
        [("r1", "jason", "A", "B", "x", "x")],
        people={
            "A": {"user_id": "jason", "name": "Alice"},
            "B": {"user_id": "jason", "name": "Ghost", "deleted": 1},
        },
    )
    try:
        res = await rg.neighbors(db, "jason", "A", max_depth=2)
    finally:
        await db.close()
    assert res == []  # B is soft-deleted -> resolved name missing -> dropped


# ── Engine: unknown / empty start ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_unknown_start_is_empty_not_error():
    db = await _make_db([("r1", "jason", "A", "B", "x", "x")])
    try:
        assert await rg.neighbors(db, "jason", "does-not-exist", max_depth=3) == []
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_empty_start_id_returns_empty():
    db = await _make_db([("r1", "jason", "A", "B", "x", "x")])
    try:
        assert await rg.neighbors(db, "jason", "", max_depth=2) == []
        assert await rg.neighbors(db, "", "A", max_depth=2) == []
    finally:
        await db.close()


# ── Flag helper ────────────────────────────────────────────────────────────


def test_flag_defaults_off(monkeypatch):
    monkeypatch.delenv("ZOE_RELATIONSHIP_GRAPH_ENABLED", raising=False)
    assert rg.relationship_graph_enabled() is False
    monkeypatch.setenv("ZOE_RELATIONSHIP_GRAPH_ENABLED", "1")
    assert rg.relationship_graph_enabled() is True
    monkeypatch.setenv("ZOE_RELATIONSHIP_GRAPH_ENABLED", "0")
    assert rg.relationship_graph_enabled() is False


# ── Endpoint: flag gating + require_feature_access enforcement ──────────────


@pytest_asyncio.fixture
async def _endpoint(monkeypatch):
    """Wire the people router over a real seeded aiosqlite DB via dep overrides.

    Grants people.read to the admin role and denies guest, so
    require_feature_access is exercised for real.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import guest_policy
    from auth import get_current_user
    from database import get_db
    from routers.people import router as people_router

    db = await _make_db(
        [
            ("r1", "jason", "A", "B", "Father", "Son"),
            ("r2", "jason", "B", "C", "Brother", "Brother"),
        ],
        people={
            "A": {"user_id": "jason", "name": "Alice"},
            "B": {"user_id": "jason", "name": "Bob"},
            "C": {"user_id": "jason", "name": "Cara"},
        },
    )

    async def _grant_matrix(_db, role):
        if role == "guest":
            return {"features": {"people": {"read": False}}}
        return {"features": {"people": {"read": True}}}

    monkeypatch.setattr(guest_policy, "get_matrix_for_role", _grant_matrix)

    app = FastAPI()
    app.include_router(people_router)

    state = {"user": {"user_id": "jason", "role": "admin"}}
    app.dependency_overrides[get_current_user] = lambda: state["user"]

    async def _yield_db():
        yield db

    app.dependency_overrides[get_db] = _yield_db

    client = TestClient(app)
    try:
        yield client, state
    finally:
        await db.close()


def test_endpoint_flag_off_disabled(monkeypatch, _endpoint):
    client, _state = _endpoint
    monkeypatch.delenv("ZOE_RELATIONSHIP_GRAPH_ENABLED", raising=False)
    resp = client.get("/api/people/A/graph?depth=2")
    assert resp.status_code == 404
    assert "disabled" in resp.json()["detail"].lower()


def test_endpoint_flag_on_returns_graph(monkeypatch, _endpoint):
    client, _state = _endpoint
    monkeypatch.setenv("ZOE_RELATIONSHIP_GRAPH_ENABLED", "1")
    resp = client.get("/api/people/A/graph?depth=2&limit=50")
    assert resp.status_code == 200
    body = resp.json()
    assert body["start"] == {"id": "A", "name": "Alice"}
    by_id = {n["person_id"]: n["depth"] for n in body["nodes"]}
    assert by_id == {"B": 1, "C": 2}
    assert body["count"] == 2


def test_endpoint_unknown_person_404(monkeypatch, _endpoint):
    client, _state = _endpoint
    monkeypatch.setenv("ZOE_RELATIONSHIP_GRAPH_ENABLED", "1")
    resp = client.get("/api/people/nope/graph")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Person not found"


def test_endpoint_guest_blocked(monkeypatch, _endpoint):
    client, state = _endpoint
    monkeypatch.setenv("ZOE_RELATIONSHIP_GRAPH_ENABLED", "1")
    state["user"] = None  # -> role 'guest' -> require_feature_access denies
    resp = client.get("/api/people/A/graph")
    assert resp.status_code == 403
