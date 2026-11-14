"""ZOE_MEMORY_TICK_DEFER_S: a prompt read's access tick is queued and written once, later (default 0 = at once, unchanged).

The final metadata must equal what the per-read ticks would have written (access_count +1 per read that returned the
row; the same capped distinct-query bookkeeping); only WHEN, and in how many writes, changes. Synthetic, in-memory
collection; no store.
"""
from __future__ import annotations

import asyncio

import pytest

import memory_service as ms

pytestmark = pytest.mark.ci_safe

ENV = "ZOE_MEMORY_TICK_DEFER_S"


@pytest.fixture(autouse=True)
def _off(monkeypatch):
    monkeypatch.delenv(ENV, raising=False)


@pytest.mark.parametrize("raw,expected", [(None, 0.0), ("0", 0.0), ("1.0", 1.0), ("0.25", 0.25), ("-1", 0.0),
                                          ("abc", 0.0), ("11", 0.0), ("10", 10.0)])
def test_reader(monkeypatch, raw, expected):
    if raw is not None:
        monkeypatch.setenv(ENV, raw)
    assert ms._tick_defer_s() == expected


class _Col:
    """The two calls the tick makes: get(ids, metadatas) and a metadata-only update. Counts the writes."""

    def __init__(self, rows):
        self.metas = {rid: dict(m) for rid, m in rows.items()}
        self.updates = 0

    def get(self, ids, include):
        got = [i for i in ids if i in self.metas]
        return {"ids": got, "metadatas": [dict(self.metas[i]) for i in got]}

    def update(self, ids, metadatas):
        self.updates += 1
        for i, m in zip(ids, metadatas):
            self.metas[i] = dict(m)


def _svc(col):
    svc = ms.MemoryService(data_dir="/nonexistent/zoe-test-tick-defer")
    svc._collection = lambda: col

    async def run_sync(fn, *args):
        return fn(*args)

    svc._run_sync = run_sync
    return svc


def _rows():
    return {"r1": {"user_id": "u", "access_count": 2, "_query_hashes": ""}, "r2": {"user_id": "u"}}


READS = [(["r1", "r2"], None), (["r1"], "do I drink coffee?"), (["r1", "r2"], "Do I drink coffee? "),
         (["r2"], "what about tea")]


async def _sequential(col):
    svc = _svc(col)
    for ids, q in READS:
        if q is None:
            await svc._tick_access("u", ids)
        else:
            await svc.tick_access("u", ids, query=q)


def _strip_time(metas):
    return {k: {kk: vv for kk, vv in v.items() if kk != "last_accessed"} for k, v in metas.items()}


async def test_coalesced_write_equals_the_per_read_ticks(monkeypatch):
    seq = _Col(_rows())
    await _sequential(seq)
    monkeypatch.setenv(ENV, "0.05")
    co = _Col(_rows())
    svc = _svc(co)
    for ids, q in READS:
        svc._schedule_tick("u", ids, query=q, name="t")
    assert co.updates == 0                                      # nothing written while the packet is being built
    await asyncio.sleep(0.2)
    assert co.updates == 1 and seq.updates == len(READS)        # one write instead of four
    assert _strip_time(co.metas) == _strip_time(seq.metas)
    assert co.metas["r1"]["access_count"] == 5 and co.metas["r1"]["unique_query_count"] == 1
    assert co.metas["r2"]["access_count"] == 3 and co.metas["r2"]["unique_query_count"] == 2


async def test_default_writes_at_once_per_read():
    co = _Col(_rows())
    svc = _svc(co)
    for ids, q in READS:
        svc._schedule_tick("u", ids, query=q, name="t")
    for _ in range(10):
        await asyncio.sleep(0)
    assert co.updates == len(READS) and svc._pending_ticks == {}   # negative control: unset = as before


async def test_the_query_hash_cap_still_freezes(monkeypatch):
    monkeypatch.setenv(ENV, "0.01")
    full = ",".join(f"h{i:015d}" for i in range(ms._MAX_QUERY_HASHES))
    co = _Col({"r1": {"_query_hashes": full, "unique_query_count": ms._MAX_QUERY_HASHES}})
    svc = _svc(co)
    svc._schedule_tick("u", ["r1"], query="a brand new question", name="t")
    await asyncio.sleep(0.1)
    assert co.metas["r1"]["unique_query_count"] == ms._MAX_QUERY_HASHES and co.metas["r1"]["access_count"] == 1


async def test_a_flush_waits_for_reads_in_flight(monkeypatch):
    monkeypatch.setenv(ENV, "0.05")
    co = _Col(_rows())
    svc = _svc(co)
    with ms._PromptRead():
        assert ms._PROMPT_READS[0] == 1
        svc._schedule_tick("u", ["r1"], name="t")
        await asyncio.sleep(0.2)                                # past the delay, but a read is in flight
        assert co.updates == 0
    await asyncio.sleep(0.2)
    assert co.updates == 1 and ms._PROMPT_READS[0] == 0


def test_reads_are_not_counted_when_ticks_are_not_deferred():
    with ms._PromptRead():
        assert ms._PROMPT_READS[0] == 0


class _Ref:
    def __init__(self, i):
        self.id = f"r{i}"


async def test_load_for_prompt_routes_its_tick_through_the_scheduler(monkeypatch):
    svc = ms.MemoryService(data_dir="/nonexistent/zoe-test-tick-defer")
    seen = []

    async def run_sync(fn, *args):
        return [_Ref(1), _Ref(2)]

    monkeypatch.setattr(svc, "_run_sync", run_sync)
    monkeypatch.setattr(svc, "_schedule_tick", lambda user_id, ids, *, name, query=None: seen.append((user_id, ids, name)))
    rows = await svc.load_for_prompt("synthetic-member", limit=2)
    assert [r.id for r in rows] == ["r1", "r2"]
    assert seen == [("synthetic-member", ["r1", "r2"], "memory_tick_access_prompt")]
