"""List item correction on REAL PostgreSQL: ``list_service.rename_item_on_list`` under the per-list advisory lock (the ci_safe fakes
cannot show it). Opt-in like ``test_list_add_postgres.py``: set ``ZOE_TEST_PG_URL`` to a SCRATCH database migrated to head (never
the live one). Not ``ci_safe``; skips cleanly without a database. Synthetic user + list, removed in teardown."""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest

import db_pool
import list_service

URL = os.environ.get("ZOE_TEST_PG_URL") or (os.environ.get("POSTGRES_URL") if os.environ.get("CI") else None)
REQUIRED = bool(os.environ.get("ZOE_REQUIRE_PG_TESTS"))
if REQUIRED:
    assert URL, "ZOE_REQUIRE_PG_TESTS is set but no scratch PostgreSQL URL (ZOE_TEST_PG_URL) was supplied"
    import asyncpg
else:
    asyncpg = pytest.importorskip("asyncpg")
pytestmark = pytest.mark.skipif(not URL, reason="no scratch PostgreSQL (ZOE_TEST_PG_URL)")


@pytest.fixture
async def world(monkeypatch):
    uid = "pgtest-" + uuid.uuid4().hex[:10]
    conns = [await asyncpg.connect(URL) for _ in range(2)]
    await conns[0].execute("INSERT INTO users (id, name) VALUES ($1, 'pg test')", uid)
    list_id = str(uuid.uuid4())
    await conns[0].execute("INSERT INTO lists (id, user_id, name, list_type, visibility) VALUES ($1, $2, $3, 'shopping', 'personal')",
                           list_id, uid, "pgtest " + uuid.uuid4().hex[:6])
    for text in ("noodles", "milk"):
        await conns[0].execute("INSERT INTO list_items (id, list_id, text) VALUES ($1, $2, $3)", str(uuid.uuid4()), list_id, text)
    receipts = []
    monkeypatch.setattr(list_service.write_receipts, "record", lambda *a, **k: receipts.append(a))
    dbs = [db_pool.AsyncpgCompat(c) for c in conns]
    yield type("W", (), {"uid": uid, "list_id": list_id, "conns": conns, "dbs": dbs, "receipts": receipts})
    await conns[0].execute("DELETE FROM list_items WHERE list_id=$1", list_id)
    await conns[0].execute("DELETE FROM lists WHERE id=$1", list_id)
    await conns[0].execute("DELETE FROM users WHERE id=$1", uid)
    for c in conns:
        await c.close()


async def _open(w):
    rows = await w.conns[0].fetch("SELECT text FROM list_items WHERE list_id=$1 AND deleted=0", w.list_id)
    return sorted(r["text"] for r in rows)


async def test_rename_commits_and_records_one_receipt(world):
    out = await list_service.rename_item_on_list(world.dbs[0], user_id=world.uid, list_id=world.list_id,
                                                 old_text="Noodle", new_text="rice noodles")
    assert out["status"] == "renamed"
    assert await _open(world) == ["milk", "rice noodles"]
    assert [r[3] for r in world.receipts] == ["rename_item"]


async def test_rename_into_an_existing_item_retires_the_old_row(world):
    out = await list_service.rename_item_on_list(world.dbs[0], user_id=world.uid, list_id=world.list_id,
                                                 old_text="noodles", new_text="Milk")
    assert out["status"] == "merged"
    assert await _open(world) == ["milk"]


async def test_two_overlapping_corrections_serialise_on_the_list_lock(world):
    """Both rename different items to the SAME new text at once: exactly one ends up with it (the loser merges into the winner)."""
    a, b = await asyncio.gather(
        list_service.rename_item_on_list(world.dbs[0], user_id=world.uid, list_id=world.list_id, old_text="noodles", new_text="rice"),
        list_service.rename_item_on_list(world.dbs[1], user_id=world.uid, list_id=world.list_id, old_text="milk", new_text="rice"),
    )
    assert sorted([a["status"], b["status"]]) == ["merged", "renamed"]
    assert await _open(world) == ["rice"]


async def test_a_non_owner_cannot_rename_on_a_personal_list(world):
    out = await list_service.rename_item_on_list(world.dbs[0], user_id="someone-else", list_id=world.list_id,
                                                 old_text="noodles", new_text="rice noodles")
    assert out["status"] == "forbidden"
    assert await _open(world) == ["milk", "noodles"]
