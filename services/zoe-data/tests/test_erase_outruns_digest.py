"""A member erase wins over the post-turn and background memory writers that were already scheduled for them.

The bug (seen live with a synthetic demo member, n=1): ``MemoryService.delete_user`` ran ~40 s after the member's last turn, removed
0 rows, and ~20 s later that turn's digest STORED a row for them. Nothing re-runs the erase, so the row stays.

The rule: every background writer snapshots the member's erase generation (``user_erase_gate``) when its work is SCHEDULED, and the
store drops a write whose snapshot is stale - checked under the store's own per-user lock, which ``delete_user`` holds while it
enumerates. Each test here runs the REAL ``delete_user`` against a fake collection (no model, no network, no database) and has a
control that bypasses the guard and shows the row landing. Synthetic placeholder text only.
"""
from __future__ import annotations

import ast
import asyncio
import contextlib
import logging
from pathlib import Path

import pytest

import memory_digest
import memory_idle_consolidation as mic
import memory_quality
import memory_service
import turn_forget
import user_erase_gate as erase_gate
from test_memory_authority import _Col
from test_memory_digest_row_log import FACT, T1
from test_memory_implicit_supersede import UID, _flag, _patch_llm, _svc

pytestmark = pytest.mark.ci_safe

OTHER = "member-b"
ZOE_DATA = Path(__file__).resolve().parents[1]


class _DelCol(_Col):
    def delete(self, *, ids):
        for i in ids:
            self.rows.pop(i, None)


class _Audit:
    def get(self, **_kw):
        return {"ids": [], "documents": [], "metadatas": []}

    def upsert(self, **_kw):
        pass

    def delete(self, **_kw):
        pass


@pytest.fixture
def env(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    erase_gate.reset()
    turn_forget.reset()
    _flag(monkeypatch, False)
    svc, _ = _svc(monkeypatch)
    col = _DelCol()
    svc._collection = lambda: col
    svc._audit_collection = lambda: _Audit()
    monkeypatch.setenv("ZOE_MEMORY_PHYSICAL_ERASE", "0")
    monkeypatch.setattr(memory_service, "_invalidate_agent_user_facts_cache", lambda _u: None)
    _patch_llm(monkeypatch, [{"type": "profile", "fact": FACT}])
    yield svc, col
    erase_gate.reset()
    turn_forget.reset()


def _rows(col, user):
    return [(d, m) for d, m in col.rows.values() if m.get("user_id") == user]


def _drops(caplog):
    return [r for r in caplog.records if r.getMessage().startswith("ERASE_GATE_DROP ")]


def _hold_before_store(monkeypatch):
    """Block the turn digest just before its store (the reconcile step right before ``ingest``), per member."""
    real = memory_quality.reconcile_for_ingest
    gates: dict = {}

    def gate(user):
        if user not in gates:
            gates[user] = (asyncio.Event(), asyncio.Event())
        return gates[user]

    async def held(svc, fact, user_id, **kw):
        reached, release = gate(user_id)
        reached.set()
        await release.wait()
        return await real(svc, fact, user_id, **kw)

    monkeypatch.setattr(memory_quality, "reconcile_for_ingest", held)
    return gate


def _bypass(monkeypatch):
    monkeypatch.setattr(erase_gate, "erased_since_scheduled", lambda _uid: False)


async def _erase(svc, user=UID):
    return await svc.delete_user(user, actor="test", reason="synthetic erase")


# ── the turn digest, held open across an erase ──────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("bypass", [False, True], ids=["guarded", "control-guard-bypassed"])
def test_an_erase_while_the_digest_is_held_before_its_store_leaves_no_row(env, monkeypatch, caplog, bypass):
    svc, col = env
    if bypass:
        _bypass(monkeypatch)
    gate = _hold_before_store(monkeypatch)

    async def go():
        task = asyncio.ensure_future(memory_digest.run_turn_digest(UID, T1, session_id="s-1"))
        reached, release = gate(UID)
        await asyncio.wait_for(reached.wait(), 10)
        removed = await _erase(svc)              # the erase returns at once, unchanged: nothing to remove yet
        release.set()
        return removed, await asyncio.wait_for(task, 10)

    removed, result = asyncio.run(go())
    assert removed == 0
    if bypass:
        assert [d for d, _ in _rows(col, UID)] == [FACT], "control: without the guard the late write lands (the live bug)"
        assert _drops(caplog) == []
        return
    assert _rows(col, UID) == [] and result["new"] == 0
    [rec] = _drops(caplog)
    assert rec.getMessage() == "ERASE_GATE_DROP lane=turn_digest code=erased_since_scheduled"
    text = rec.getMessage() + repr(rec.args)
    assert FACT not in text and T1 not in text and UID not in text, "a code and a lane only"
    assert not [r for r in caplog.records if r.getMessage().startswith("MEMORY_ROW ") and UID in r.getMessage()]


def test_with_no_erase_the_row_is_stored_exactly_as_without_the_guard(env, monkeypatch):
    svc, col = env
    asyncio.run(memory_digest.run_turn_digest(UID, T1, session_id="s-1"))
    guarded = _rows(col, UID)
    asyncio.run(_erase(svc))         # clears the rows AND the store's idempotency caches; the next run is scheduled after it
    assert _rows(col, UID) == []
    _bypass(monkeypatch)
    asyncio.run(memory_digest.run_turn_digest(UID, T1, session_id="s-1"))
    plain = _rows(col, UID)
    assert len(guarded) == 1 and len(plain) == 1 and guarded[0][0] == plain[0][0] == FACT
    (_, a), (_, b) = guarded[0], plain[0]
    differ = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
    assert differ in ([], ["added_at", "added_ts", "last_accessed", "valid_from"]), differ     # only the write's own clock


# ── the snapshot is taken when the work is SCHEDULED, not when it runs ───────────────────────────────────────────────────

@pytest.mark.parametrize("at_schedule", [True, False], ids=["snapshot-at-schedule", "control-snapshot-at-run"])
def test_an_erase_between_scheduling_and_the_body_starting_wins_only_with_a_schedule_time_snapshot(env, at_schedule):
    """The post-turn hook is CALLED when the turn queues it (``ensure_future(hook(...))``); its body starts later. A snapshot taken
    when the body starts (the undecorated control: ``run_turn_digest`` snapshots on entry) misses an erase in that window."""
    svc, col = env

    async def post_turn(user_id, words):
        return await memory_digest.run_turn_digest(user_id, words, session_id="s-1")

    hook = erase_gate.scheduled_at_call("user_id")(post_turn) if at_schedule else post_turn

    async def go():
        queued = hook(UID, T1)          # the turn queues its work ...
        await _erase(svc)               # ... the erase lands before the body runs
        return await queued

    result = asyncio.run(go())
    if at_schedule:
        assert _rows(col, UID) == [] and result["skipped_reason"] == "erased_since_scheduled"
    else:
        assert [d for d, _ in _rows(col, UID)] == [FACT], "a run-time snapshot lets the erased member's row land"


def test_the_chat_and_voice_post_turn_hooks_snapshot_when_they_are_called():
    """Pinned from the source (no service import): both post-turn hooks carry ``scheduled_at_call("user_id")``."""
    for rel, name in (("routers/chat.py", "_persist_memory_candidates"), ("routers/voice_tts.py", "_run_voice_memory_passes")):
        tree = ast.parse((ZOE_DATA / rel).read_text())
        [fn] = [n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == name]
        decos = [ast.unparse(d) for d in fn.decorator_list]
        assert "_erase_gate.scheduled_at_call('user_id')" in decos, (rel, decos)


def test_the_decorated_hook_keeps_its_signature_and_an_earlier_snapshot_wins():
    async def hook(text, user_id, session_id=None):
        return erase_gate._scheduled.get()

    wrapped = erase_gate.scheduled_at_call("user_id")(hook)
    assert str(__import__("inspect").signature(wrapped)) == "(text, user_id, session_id=None)"
    erase_gate.reset()
    assert asyncio.run(wrapped("t", UID)) == (UID, 0)

    async def nested():
        with erase_gate.scheduled(UID, 0):
            erase_gate._generation[UID] = 2          # an erase after the outer work was scheduled
            return await wrapped("t", user_id=UID)
    assert asyncio.run(nested()) == (UID, 0)
    erase_gate.reset()


# ── another member is untouched ──────────────────────────────────────────────────────────────────────────────────────────

def test_another_members_digest_is_unaffected_by_this_members_erase(env, monkeypatch):
    svc, col = env
    gate = _hold_before_store(monkeypatch)

    async def go():
        mine = asyncio.ensure_future(memory_digest.run_turn_digest(UID, T1, session_id="s-1"))
        theirs = asyncio.ensure_future(memory_digest.run_turn_digest(OTHER, T1, session_id="s-2"))
        for u in (UID, OTHER):
            await asyncio.wait_for(gate(u)[0].wait(), 10)
        await _erase(svc, UID)
        for u in (UID, OTHER):
            gate(u)[1].set()
        return await asyncio.wait_for(asyncio.gather(mine, theirs), 10)

    asyncio.run(go())
    assert _rows(col, UID) == []
    assert [d for d, _ in _rows(col, OTHER)] == [FACT]


# ── the store's own check covers review(edit) too ────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("bypass", [False, True], ids=["guarded", "control-guard-bypassed"])
def test_a_stale_scheduled_edit_writes_nothing(env, monkeypatch, bypass):
    svc, col = env
    if bypass:
        _bypass(monkeypatch)

    async def go():
        old = await svc.ingest("User lives in Perth", user_id=UID, source="turn_digest", memory_type="profile",
                               confidence=0.82, status="approved")
        with erase_gate.scheduled(UID, erase_gate.generation(UID)):
            async with erase_gate.erasing(UID):      # an erase lands after the edit was scheduled (its rows are not touched here)
                pass
            return await svc.review(old.id, decision="edit", edits="User lives in Hobart", actor="turn_digest")

    ref = asyncio.run(go())
    texts = sorted(d for d, _ in _rows(col, UID))
    if bypass:
        assert ref is not None and "User lives in Hobart" in texts
    else:
        assert ref is None and texts == ["User lives in Perth"]


# ── the nightly digest and idle consolidation: snapshot when the pass starts ─────────────────────────────────────────────

def _member_write(svc, user, text):
    return svc.ingest(text, user_id=user, source="digest", memory_type="profile", confidence=0.82, status="approved")


@pytest.mark.parametrize("bypass", [False, True], ids=["guarded", "control-guard-bypassed"])
def test_the_nightly_pass_drops_a_write_for_a_member_erased_while_it_ran(env, monkeypatch, bypass):
    svc, col = env
    if bypass:
        _bypass(monkeypatch)
    reached, release = {}, {}

    async def users(*_a, **_k):
        return [UID, OTHER]

    async def one(uid, db=None):
        reached[uid].set()
        await release[uid].wait()
        await _member_write(svc, uid, f"User keeps bees ({uid})")
        return {"user_id": uid}

    monkeypatch.setattr(memory_digest, "_list_user_ids", users)
    monkeypatch.setattr(memory_digest, "run_memory_digest", one)

    async def go():
        for u in (UID, OTHER):
            reached[u], release[u] = asyncio.Event(), asyncio.Event()
        task = asyncio.ensure_future(memory_digest.run_digest_for_all_active_users(db=object()))
        await asyncio.wait_for(reached[UID].wait(), 10)
        await _erase(svc, UID)
        for u in (UID, OTHER):
            release[u].set()
        await asyncio.wait_for(task, 10)

    asyncio.run(go())
    assert [d for d, _ in _rows(col, OTHER)] == [f"User keeps bees ({OTHER})"]
    assert (_rows(col, UID) != []) is bypass


@pytest.mark.parametrize("bypass", [False, True], ids=["guarded", "control-guard-bypassed"])
def test_idle_consolidation_drops_a_write_for_a_member_erased_during_the_sweep(env, monkeypatch, bypass):
    svc, col = env
    if bypass:
        _bypass(monkeypatch)
    import db_pool

    real_ctx, calls = db_pool.get_db_ctx, []

    @contextlib.asynccontextmanager
    async def ctx():
        calls.append(1)
        if len(calls) == 1:          # the sweep's own listing connection; the erasers' lookups get the real (absent) pool
            yield object()
            return
        async with real_ctx() as conn:
            yield conn

    async def none(*_a, **_k):
        return None

    async def sessions(_conn):
        return [{"session_id": "s-1", "user_id": UID, "since": None}]

    hold = {}

    async def consolidate(session_id, user_id, since=None, **_kw):
        hold["reached"].set()
        await hold["release"].wait()
        return 1 if await _member_write(svc, user_id, "User keeps bees") is not None else 0

    monkeypatch.setattr(mic, "_enabled", lambda: True)
    monkeypatch.setattr(db_pool, "get_db_ctx", ctx)
    monkeypatch.setattr(mic, "_ensure_state_table", none)
    monkeypatch.setattr(mic, "find_idle_sessions", sessions)
    monkeypatch.setattr(mic, "consolidate_session", consolidate)

    async def go():
        hold["reached"], hold["release"] = asyncio.Event(), asyncio.Event()
        task = asyncio.ensure_future(mic.run_idle_consolidation_sweep())
        await asyncio.wait_for(hold["reached"].wait(), 10)
        await _erase(svc)
        hold["release"].set()
        return await asyncio.wait_for(task, 10)

    res = asyncio.run(go())
    assert res["stored"] == (1 if bypass else 0)
    assert (_rows(col, UID) != []) is bypass


# ── a narrower forget: "forget that" already walls the same writer, without the erase gate ──────────────────────────────

def test_forget_that_mid_digest_is_already_closed_by_the_turn_wall_not_the_erase_gate(env, monkeypatch):
    """"Forget that" retracts the turn INSTANCE (``turn_forget``); the chat capture runs inside ``capture_scope``, so the digest's late
    write is refused by ``blocks_write`` in ``ingest`` even though no erase generation moved."""
    svc, col = env
    gate = _hold_before_store(monkeypatch)
    turn_forget.note_user_turn(UID, T1)

    async def go():
        with turn_forget.capture_scope(UID, T1):
            task = asyncio.ensure_future(memory_digest.run_turn_digest(UID, T1, session_id="s-1"))
        reached, release = gate(UID)
        await asyncio.wait_for(reached.wait(), 10)
        turn_forget.retract(UID, turn_forget.previous_turn(UID))
        release.set()
        return await asyncio.wait_for(task, 10)

    asyncio.run(go())
    assert erase_gate.generation(UID) == 0, "the erase gate played no part"
    assert _rows(col, UID) == []
