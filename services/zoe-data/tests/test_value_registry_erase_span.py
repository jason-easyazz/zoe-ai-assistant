"""The superseded-value registry drops a correction that a whole-member erase outran (review thread on #2481).

``value_correction.apply`` snapshots ``(partial_at, scheduled_at)`` when it starts (``_registry_snapshot``) and records into the
registry later. A correction that started INSIDE a running ``MemoryService.delete_user`` (after ``memory_value_registry.forget_user``
swept the member) snapshots the post-sweep generation, so the generation check alone lets it write after the sweep. ``record`` now
also drops while an erase span (``user_erase_gate.erase_span``, #2569) is running for the member, and the span's closing bump stales
any snapshot taken inside it. Real ``delete_user`` (fake palace collection), the real migration 0080 on SQLite, events only.
Synthetic placeholder members only.
"""
from __future__ import annotations

import asyncio
import logging

import pytest

import alarms
import db_compat
import memory_service
import memory_value_registry as reg
import user_erase_gate as erase_gate
import value_correction
from memory_service import MemoryService
from test_erase_outruns_digest import _Audit, _DelCol, _arm, _pause_first
from test_memory_value_registry import A, B, C, JUNO, OTHER_JUNO, T0, _age, db  # noqa: F401 - db is the fixture

pytestmark = pytest.mark.ci_safe


_REAL_COMPAT_DB = db_compat.get_compat_db       # captured at import, before any fixture patches it


@pytest.fixture
def svc(db, monkeypatch, tmp_path, caplog):  # noqa: F811
    caplog.set_level(logging.INFO)
    sqlite = db_compat.get_compat_db                 # the registry fixture's SQLite: for the registry ONLY
    monkeypatch.setattr(reg, "_db", lambda: sqlite())
    monkeypatch.setattr(db_compat, "get_compat_db", _REAL_COMPAT_DB)     # every other eraser sees no store, as in production tests
    s = MemoryService(data_dir=str(tmp_path / "palace"))
    col = _DelCol()
    s._collection = lambda: col
    s._audit_collection = lambda: _Audit()
    monkeypatch.setenv("ZOE_MEMORY_PHYSICAL_ERASE", "0")
    monkeypatch.setattr(memory_service, "_invalidate_agent_user_facts_cache", lambda _u: None)
    return s


def _record(member, subject, snap, *, at=T0):
    partial_at, scheduled_at = snap
    return reg.record(member, subject, "age", superseded=[_age(7)], asserted=_age(8), said_at=at, by_claim=C(int(at)),
                      partial_at=partial_at, scheduled_at=scheduled_at, now=at)


def _rows(db, member):  # noqa: F811
    return db.q("SELECT COUNT(*) FROM memory_value_registry WHERE user_id = ?", (member,))[0][0]


def _drops(caplog):
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith("ERASE_GATE_DROP lane=value_registry")]


@pytest.mark.parametrize("bypass", [False, True], ids=["guarded", "control-span-check-bypassed"])
def test_a_correction_started_inside_the_erase_after_the_sweep_records_nothing(db, svc, monkeypatch, caplog, bypass):  # noqa: F811
    """``delete_user`` is held after the registry was swept (at the in-lock alarms step); a correction STARTS there (its snapshot is
    the post-sweep generation) and records at once, while the span is still running."""
    if bypass:
        monkeypatch.setattr(reg, "_member_erased_since", lambda uid, gen: erase_gate.generation(uid) != gen)    # generation only
    st = _pause_first(monkeypatch, alarms, "forget_user")

    async def go():
        _arm(st)
        erase = asyncio.ensure_future(svc.delete_user(A, actor="test", reason="synthetic erase"))
        await asyncio.wait_for(st["reached"].wait(), 10)
        snap = value_correction._registry_snapshot(A)          # the correction starts inside the span
        n = await asyncio.wait_for(_record(A, JUNO, snap), 10)
        st["release"].set()
        await asyncio.wait_for(erase, 10)
        return n

    n = asyncio.run(go())
    if bypass:
        assert n == 2 and _rows(db, A) == 2, "control: the generation alone lets it write after the sweep"
        return
    assert n == 0 and _rows(db, A) == 0
    assert _drops(caplog) == ["ERASE_GATE_DROP lane=value_registry code=erased_since_scheduled"]


def test_a_record_held_open_across_a_real_member_erase_leaves_nothing(db, svc, monkeypatch):  # noqa: F811
    """The record holds the member's erase gate (waiting inside its subject check); the erase queues behind it; once released the
    record commits, then the erase sweeps it: nothing of the member stays."""
    real, hold = reg._person_live, {}

    async def held(conn, uid, pid):
        hold["in"].set()
        await hold["go"].wait()
        return await real(conn, uid, pid)

    monkeypatch.setattr(reg, "_person_live", held)

    async def go():
        hold["in"], hold["go"] = asyncio.Event(), asyncio.Event()
        rec = asyncio.ensure_future(_record(A, JUNO, value_correction._registry_snapshot(A)))
        await asyncio.wait_for(hold["in"].wait(), 10)
        erase = asyncio.ensure_future(svc.delete_user(A, actor="test", reason="synthetic erase"))
        await asyncio.sleep(0)
        assert erase_gate.in_progress(A)
        hold["go"].set()
        await asyncio.wait_for(rec, 10)
        await asyncio.wait_for(erase, 10)

    asyncio.run(go())
    assert _rows(db, A) == 0


def test_a_record_scheduled_before_the_erase_and_run_after_it_is_dropped(db, svc, caplog):  # noqa: F811
    async def go():
        snap = value_correction._registry_snapshot(A)
        await svc.delete_user(A, actor="test", reason="synthetic erase")
        return await _record(A, JUNO, snap)

    assert asyncio.run(go()) == 0 and _rows(db, A) == 0
    assert _drops(caplog) == ["ERASE_GATE_DROP lane=value_registry code=erased_since_scheduled"]


def test_a_record_scheduled_just_after_the_erase_finished_is_recorded(db, svc):  # noqa: F811
    async def go():
        await svc.delete_user(A, actor="test", reason="synthetic erase")
        assert not erase_gate.in_progress(A)
        return await _record(A, JUNO, value_correction._registry_snapshot(A))

    assert asyncio.run(go()) == 2 and _rows(db, A) == 2


def test_another_members_record_during_the_erase_is_unaffected(db, svc, monkeypatch):  # noqa: F811
    st = _pause_first(monkeypatch, alarms, "forget_user")

    async def go():
        _arm(st)
        erase = asyncio.ensure_future(svc.delete_user(A, actor="test", reason="synthetic erase"))
        await asyncio.wait_for(st["reached"].wait(), 10)
        n = await asyncio.wait_for(_record(B, OTHER_JUNO, value_correction._registry_snapshot(B)), 10)
        st["release"].set()
        await asyncio.wait_for(erase, 10)
        return n

    assert asyncio.run(go()) == 2 and _rows(db, B) == 2 and _rows(db, A) == 0
