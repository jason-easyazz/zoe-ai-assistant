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
import contextlib
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


# ── the snapshot is taken when the post-turn capture is QUEUED, not when apply runs (review thread 2 on #2481) ───────────────
#
# ``value_correction.apply`` is reached only from ``memory_extractor.extract_and_ingest``, which runs inside a post-turn hook decorated
# with ``user_erase_gate.scheduled_at_call`` (chat ``_persist_memory_candidates``, voice ``_run_voice_memory_passes``, the legacy
# ``zoe_agent._background_memory_save``). ``_registry_snapshot`` now reads that hook's snapshot; a direct call snapshots at entry.

SAID = "juno is 8 not 7"
STORED = "User's daughter Juno is 7 years old"


class _Row:
    def __init__(self, i, text, member):
        self.id, self.text, self.metadata = i, text, {"user_id": member}


class _Palace:
    """The palace ``apply`` edits (its own fake: ``apply`` takes ``svc=``)."""

    def __init__(self, member):
        self.member, self.rows = member, [_Row(f"zoe_{member}_{'0' * 23}1", STORED, member)]

    async def list_by_status(self, **_k):
        return list(self.rows)

    async def review(self, rid, decision=None, edits=None, **_k):
        return _Row(f"zoe_{self.member}_{'0' * 23}2", edits, self.member)


@pytest.fixture
def capture(db, svc, monkeypatch):  # noqa: F811 - after ``svc``, which points db_compat at the real (absent) store
    import correction_apply
    import user_prefs

    async def opted_in(_u):
        return False

    monkeypatch.setattr(user_prefs, "is_memory_opted_out", opted_in)
    monkeypatch.setattr(correction_apply, "_is_unidentified", lambda _u: False)
    monkeypatch.setenv(reg.FLAG, "shadow")
    value_correction.reset_state()

    @contextlib.asynccontextmanager
    async def sqlite():
        yield db

    monkeypatch.setattr(db_compat, "get_compat_db", sqlite)      # apply resolves the person on the registry's SQLite

    @contextlib.contextmanager
    def no_store_for_the_erase():
        """The erase's OTHER stores see no database (as everywhere in this lane); the registry keeps its SQLite (``reg._db``)."""
        monkeypatch.setattr(db_compat, "get_compat_db", _REAL_COMPAT_DB)
        try:
            yield
        finally:
            monkeypatch.setattr(db_compat, "get_compat_db", sqlite)

    @erase_gate.scheduled_at_call("user_id")
    async def post_turn(user_id, text, go):
        """A post-turn hook: queued (snapshot taken) when CALLED, reaches ``apply`` only once ``go`` is set."""
        await go.wait()
        return await value_correction.apply(text, user_id, None, svc=_Palace(user_id))

    return post_turn, no_store_for_the_erase


@pytest.mark.parametrize("snapshot_at", ["queue", "control-apply-entry"])
def test_a_capture_queued_before_a_full_erase_records_nothing_when_it_reaches_apply_after_it(db, svc, capture, monkeypatch, caplog,  # noqa: F811
                                                                                          snapshot_at):
    post_turn, no_store = capture
    if snapshot_at != "queue":
        monkeypatch.setattr(erase_gate, "scheduled_snapshot", lambda _u: None)      # the entry-time snapshot this replaces

    async def go():
        go_apply = asyncio.Event()
        task = asyncio.ensure_future(post_turn(A, SAID, go_apply))        # the turn queues its capture
        await asyncio.sleep(0)
        with no_store():
            await svc.delete_user(A, actor="test", reason="synthetic erase")   # the WHOLE erase runs to completion
        assert not erase_gate.in_progress(A)
        go_apply.set()                                                    # only now does the capture reach apply
        return await asyncio.wait_for(task, 10)

    asyncio.run(go())
    if snapshot_at != "queue":
        assert _rows(db, A) == 2, "control: a snapshot taken at apply entry records after the erase"
        return
    assert _rows(db, A) == 0
    assert _drops(caplog) == ["ERASE_GATE_DROP lane=value_registry code=erased_since_scheduled"]


@pytest.mark.parametrize("snapshot_at", ["queue", "control-apply-entry"])
def test_a_capture_queued_before_forget_that_records_nothing_after_it(db, capture, monkeypatch, caplog, snapshot_at):  # noqa: F811
    """The partial epoch gets the same treatment: "forget that" (``erase_for_turn``) runs between queue and apply."""
    post_turn, _ = capture
    if snapshot_at != "queue":
        monkeypatch.setattr(erase_gate, "scheduled_snapshot", lambda _u: None)

    async def go():
        go_apply = asyncio.Event()
        task = asyncio.ensure_future(post_turn(A, SAID, go_apply))
        await asyncio.sleep(0)
        await reg.erase_for_turn(A, SAID)          # finds nothing yet: the record has not been written
        go_apply.set()
        return await asyncio.wait_for(task, 10)

    asyncio.run(go())
    if snapshot_at != "queue":
        assert _rows(db, A) == 2, "control: the entry-time partial epoch lets the forgotten turn's record land"
        return
    assert _rows(db, A) == 0
    assert _drops(caplog) == ["ERASE_GATE_DROP lane=value_registry code=partial_erase"]


def test_a_queued_capture_with_no_erase_in_between_records(db, capture):  # noqa: F811
    post_turn, _ = capture

    async def go():
        go_apply = asyncio.Event()
        task = asyncio.ensure_future(post_turn(A, SAID, go_apply))
        await asyncio.sleep(0)
        go_apply.set()
        return await asyncio.wait_for(task, 10)

    assert asyncio.run(go()) == 1 and _rows(db, A) == 2


def test_a_direct_apply_still_snapshots_at_entry_and_records(db, svc, capture):  # noqa: F811
    _, no_store = capture

    async def go():
        with no_store():
            await svc.delete_user(A, actor="test", reason="synthetic erase")      # an erase BEFORE the direct call: not stale
        assert erase_gate.scheduled_snapshot(A) is None
        return await value_correction.apply(SAID, A, None, svc=_Palace(A))

    assert asyncio.run(go()) == 1 and _rows(db, A) == 2


def test_another_members_queued_capture_is_unaffected_by_this_members_erase(db, svc, capture):  # noqa: F811
    post_turn, no_store = capture

    async def go():
        go_apply = asyncio.Event()
        theirs = asyncio.ensure_future(post_turn(B, SAID, go_apply))
        await asyncio.sleep(0)
        with no_store():
            await svc.delete_user(A, actor="test", reason="synthetic erase")
        go_apply.set()
        return await asyncio.wait_for(theirs, 10)

    assert asyncio.run(go()) == 1 and _rows(db, B) == 2


def test_every_path_to_apply_is_a_queued_hook():
    """``apply`` has ONE production caller (``extract_and_ingest``), and every production caller of that is a decorated hook."""
    import ast
    from pathlib import Path

    svc_dir = Path(__file__).resolve().parents[1]
    files = list(svc_dir.glob("*.py")) + list((svc_dir / "routers").glob("*.py"))
    callers = sorted(p.relative_to(svc_dir).as_posix() for p in files if "value_correction.apply(" in p.read_text(encoding="utf-8"))
    assert callers == ["memory_extractor.py"], callers
    for rel, name in (("routers/chat.py", "_persist_memory_candidates"), ("routers/voice_tts.py", "_run_voice_memory_passes"),
                      ("zoe_agent.py", "_background_memory_save")):
        tree = ast.parse((svc_dir / rel).read_text(encoding="utf-8"))
        [fn] = [n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == name]
        assert "_erase_gate.scheduled_at_call('user_id')" in [ast.unparse(d) for d in fn.decorator_list], rel
