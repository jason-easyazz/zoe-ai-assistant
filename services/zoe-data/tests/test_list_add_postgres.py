"""List adds on REAL PostgreSQL: the per-list advisory lock + transaction in ``list_service.list_write_txn`` (which the ci_safe
fakes cannot show). Opt-in: set ``ZOE_TEST_PG_URL`` to a SCRATCH database migrated to head (never the live one; CI's service
container is picked up via ``POSTGRES_URL`` when ``CI`` is set). Not ``ci_safe`` (the slim lane has no database); skips cleanly
without one. Synthetic user + list, removed in teardown."""
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
    name = "pgtest list " + uuid.uuid4().hex[:8]
    conns = [await asyncpg.connect(URL) for _ in range(3)]
    await conns[0].execute("INSERT INTO users (id, name) VALUES ($1, 'pg test')", uid)
    receipts = []

    # Resolve ONLY this test's own list (by owner + random name): the real resolver prefers any family-visible list of the type,
    # which in a scratch database that already has data would send these adds into an unrelated list.
    async def own_list_only(db, *, user_id, list_type, name):
        row = await db.fetchrow("SELECT id FROM lists WHERE user_id=$1 AND name=$2 AND deleted=0 LIMIT 1", user_id, name)
        return row["id"] if row else None

    monkeypatch.setattr(list_service, "find_list_id", own_list_only)
    monkeypatch.setattr(list_service.write_receipts, "record", lambda *a, **k: receipts.append(a))
    dbs = [db_pool.AsyncpgCompat(c) for c in conns]
    yield type("W", (), {"uid": uid, "name": name, "conns": conns, "dbs": dbs, "receipts": receipts})
    await conns[0].execute("DELETE FROM list_items WHERE list_id IN (SELECT id FROM lists WHERE user_id=$1)", uid)
    await conns[0].execute("DELETE FROM lists WHERE user_id=$1", uid)
    await conns[0].execute("DELETE FROM users WHERE id=$1", uid)
    for c in conns:
        await c.close()


async def _seed_list(w):
    out = await list_service.add_item_to_list(
        w.dbs[0], user_id=w.uid, list_type="shopping", list_name=w.name, text="seed", new_list_visibility="personal")
    return out["list_id"]


async def _open_texts(w, list_id):
    rows = await w.conns[0].fetch("SELECT text FROM list_items WHERE list_id=$1 AND deleted=0", list_id)
    return sorted(r["text"] for r in rows)


def _add(w, i, text):
    return list_service.add_items_to_list(
        w.dbs[i], user_id=w.uid, list_type="shopping", list_name=w.name, text=text, new_list_visibility="personal")


async def test_add_then_already_then_plural_variant_on_real_pg(world):
    list_id = await _seed_list(world)
    out = await _add(world, 0, "cookies")
    assert out["added"] == ["cookies"] and out["already"] == []
    again = await _add(world, 0, "Cookie")
    assert again["added"] == [] and again["already"] == ["cookies"]
    assert await _open_texts(world, list_id) == ["cookies", "seed"]


async def test_concurrent_adds_of_the_same_item_make_exactly_one_row(world):
    list_id = await _seed_list(world)
    for n in range(12):      # many rounds: a single race can be won by luck, twelve cannot
        item = f"oat milk {n}"
        a, b = await asyncio.gather(_add(world, 0, item), _add(world, 1, item.upper()))
        assert sorted([len(a["added"]), len(b["added"])]) == [0, 1], (n, a["added"], b["added"])
    assert len(await _open_texts(world, list_id)) == 13   # seed + one row per round


async def test_concurrent_multi_item_adds_neither_duplicate_nor_deadlock(world):
    list_id = await _seed_list(world)
    # opposite item orders, same list: a per-item lock order would deadlock; one per-list lock cannot
    a, b = await asyncio.wait_for(
        asyncio.gather(_add(world, 0, "eggs and milk and bread"), _add(world, 1, "bread and milk and eggs")), timeout=20)
    texts = await _open_texts(world, list_id)
    assert len(texts) == 4          # seed + eggs + milk + bread, each once
    assert len(a["added"]) + len(b["added"]) == 3


async def test_failing_second_insert_rolls_back_the_first_and_records_no_receipt(world, monkeypatch):
    list_id = await _seed_list(world)
    world.receipts.clear()
    real = list_service.create_item_record
    calls = {"n": 0}

    async def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("induced write failure")
        return await real(*args, **kwargs)

    monkeypatch.setattr(list_service, "create_item_record", flaky)
    with pytest.raises(RuntimeError):
        await _add(world, 0, "eggs and milk")
    assert await _open_texts(world, list_id) == ["seed"]
    assert world.receipts == []
    # the connection is usable afterwards (no aborted transaction left open)
    monkeypatch.setattr(list_service, "create_item_record", real)
    ok = await _add(world, 0, "bread")
    assert ok["added"] == ["bread"]


async def test_concurrent_first_adds_to_a_list_that_does_not_exist_make_one_list_one_item(world):
    # no _seed_list: the list is created by the race itself
    a, b = await asyncio.gather(_add(world, 0, "tea bags"), _add(world, 1, "Tea Bag"))
    assert sorted([len(a["added"]), len(b["added"])]) == [0, 1]
    lists = await world.conns[0].fetch("SELECT id FROM lists WHERE user_id=$1 AND name=$2", world.uid, world.name)
    assert len(lists) == 1
    assert len(await _open_texts(world, lists[0]["id"])) == 1
