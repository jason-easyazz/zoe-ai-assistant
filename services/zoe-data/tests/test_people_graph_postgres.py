"""The people graph's invariants on REAL PostgreSQL (advisory locks, transactions, the 0043 trigger and indexes - none of
which SQLite can show). Opt-in: set ``ZOE_TEST_PG_URL`` to a SCRATCH database migrated to head (never the live one; CI's
service container is picked up via ``POSTGRES_URL`` when ``CI`` is set). Synthetic user, removed in teardown."""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest

import people_graph as pg

URL = os.environ.get("ZOE_TEST_PG_URL") or (os.environ.get("POSTGRES_URL") if os.environ.get("CI") else None)
pytestmark = pytest.mark.skipif(not URL, reason="no scratch PostgreSQL (ZOE_TEST_PG_URL)")
asyncpg = pytest.importorskip("asyncpg")


class _Conn:
    """A raw asyncpg connection plus the synthetic user it writes as (asyncpg connections take no new attributes)."""

    def __init__(self, raw, uid):
        self.raw, self.uid = raw, uid

    def __getattr__(self, name):
        return getattr(self.raw, name)


@pytest.fixture
async def conn():
    raw = await asyncpg.connect(URL)
    c = _Conn(raw, "pgtest-" + uuid.uuid4().hex[:10])
    await raw.execute("INSERT INTO users (id, name) VALUES ($1, 'pg test')", c.uid)
    yield c
    await raw.execute("DELETE FROM people WHERE user_id = $1", c.uid)   # edges go with their people (ON DELETE CASCADE)
    await raw.execute("DELETE FROM users WHERE id = $1", c.uid)
    await raw.close()


def _compat(c):
    import db_pool

    return db_pool.AsyncpgCompat(c.raw if isinstance(c, _Conn) else c)


async def _people(c, *names):
    ids = []
    for n in names:
        ids.append(uuid.uuid4().hex)
        await c.execute("INSERT INTO people (id, user_id, name) VALUES ($1, $2, $3)", ids[-1], c.uid, n)
    return ids


def _spec(rel):
    return pg.EdgeSpec(rel, rel, rel, "family", authority="user_stated", origin="conversation",
                       evidence=pg.Evidence("ut-1", "0:4:abc", 4))


async def _current(c, a, b):
    return await c.fetch("SELECT id, rel_type, superseded_by FROM person_relationships WHERE user_id=$1 AND person_a_id=$2 "
                         "AND person_b_id=$3 AND valid_to IS NULL", c.uid, a, b)


async def test_crash_between_close_and_insert_keeps_the_old_edge(conn, monkeypatch):
    a, b = await _people(conn, "Ann Reyes", "Bo Reyes")
    db = _compat(conn)
    await pg.replace_current_edge(db, conn.uid, a, b, _spec("friend"))

    async def boom(*args, **kw):
        raise RuntimeError("crash after the close")

    monkeypatch.setattr(pg, "insert_edge", boom)
    with pytest.raises(RuntimeError):
        await pg.replace_current_edge(db, conn.uid, a, b, _spec("spouse"))
    (row,) = await _current(conn, a, b)
    assert row["rel_type"] == "friend" and row["superseded_by"] is None
    # instrument: the legacy autocommit pair under the same crash leaves NO current edge
    await db.execute("UPDATE person_relationships SET valid_to = ? WHERE id = ?", pg.now_iso(), row["id"])
    assert await _current(conn, a, b) == []


async def test_two_concurrent_writers_end_with_one_current_edge_and_a_whole_chain(conn, monkeypatch):
    a, b = await _people(conn, "Ann Reyes", "Bo Reyes")
    await pg.replace_current_edge(_compat(conn), conn.uid, a, b, _spec("friend"))
    real = pg.current_edge

    async def slow_read(*args, **kw):          # widen the read-then-write window: both writers see the SAME current edge
        got = await real(*args, **kw)          # unless the pair's advisory lock keeps the second one out until the first commits
        await asyncio.sleep(0.3)
        return got

    monkeypatch.setattr(pg, "current_edge", slow_read)
    other = await asyncpg.connect(URL)
    try:
        res = await asyncio.gather(pg.replace_current_edge(_compat(conn), conn.uid, a, b, _spec("spouse")),
                                   pg.replace_current_edge(_compat(other), conn.uid, a, b, _spec("partner")))
    finally:
        await other.close()
    assert sorted(r.status for r in res) == ["superseded", "superseded"]
    assert len(await _current(conn, a, b)) == 1
    rows = await conn.fetch("SELECT id, valid_to, superseded_by FROM person_relationships WHERE user_id=$1", conn.uid)
    ids = {r["id"] for r in rows}
    assert len(rows) == 3 and all(r["superseded_by"] in ids for r in rows if r["valid_to"])     # no dangling pointer


async def test_the_timestamptz_columns_follow_both_text_forms(conn):
    a, b = await _people(conn, "Ann Reyes", "Bo Reyes")
    for eid, vf, vt in (("e1", "2020-01-01T10:00:00.123456Z", "2021-02-03 04:05:06.5+00"), ("e2", "2022-01-01T00:00:00Z", "not a date")):
        await conn.execute("INSERT INTO person_relationships (id, user_id, person_a_id, person_b_id, rel_type, rel_a_to_b, rel_b_to_a, "
                           "rel_group, created_at, updated_at, valid_from, valid_to) VALUES ($1,$2,$3,$4,'friend','F','F','friend',$5,$5,$5,$6)",
                           eid + conn.uid, conn.uid, a, b, vf, vt)
    r1, r2 = await conn.fetch("SELECT valid_from_ts, valid_to_ts, recorded_ts FROM person_relationships WHERE user_id=$1 ORDER BY id", conn.uid)
    assert (r1["valid_from_ts"].year, r1["valid_to_ts"].month, r1["recorded_ts"].hour) == (2020, 2, 10)
    assert r2["valid_from_ts"].year == 2022 and r2["valid_to_ts"] is None            # unparseable: NULL, the write succeeded


async def test_the_roster_read_is_an_index_scan(conn):
    await _people(conn, "Ann Reyes", "Joanna Reyes")
    async with conn.transaction():
        await conn.execute("SET LOCAL enable_seqscan = off")         # a 2-row table is always a seq scan otherwise
        plan = "\n".join(r[0] for r in await conn.fetch(
            "EXPLAIN SELECT id, name FROM people WHERE user_id = $1 AND deleted = 0", conn.uid))
    assert "people_user_live_idx" in plan
    assert (await pg.resolve_person(_compat(conn), conn.uid, "Ann")).status == "unique"


async def test_evidence_and_close_reason_round_trip(conn):
    a, b = await _people(conn, "Ann Reyes", "Bo Reyes")
    db = _compat(conn)
    ch = await pg.replace_current_edge(db, conn.uid, a, b, _spec("friend"))
    await pg.replace_current_edge(db, conn.uid, a, b, _spec("spouse"), close_reason="user_edited")
    rows = {r["rel_type"]: r for r in await conn.fetch("SELECT * FROM person_relationships WHERE user_id=$1", conn.uid)}
    assert rows["friend"]["close_reason"] == "user_edited" and rows["friend"]["id"] == ch.edge_id
    assert (rows["spouse"]["turn_id"], rows["spouse"]["quote_span"], rows["spouse"]["speaker_rank"]) == ("ut-1", "0:4:abc", 4)
    edges = await pg.edges_for_person(db, conn.uid, a, history=True)
    assert [e["rel_type"] for e in edges] == ["spouse", "friend"]
