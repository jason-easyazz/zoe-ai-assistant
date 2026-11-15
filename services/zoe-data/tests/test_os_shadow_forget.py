"""A member forget reaches the Zoe OS shadow kernel (os_shadow.forget_user / erase_entity / erase_topic), and fails closed.

The kernel is the same in-process FAKE as test_os_shadow.py (the v1 socket protocol, written from the spec).
"""
from __future__ import annotations

import ast
import json
import time
from pathlib import Path

import pytest

import os_shadow
from test_os_shadow import ITEM, MEMBER, FakeKernel, add, kernel, settle  # noqa: F401  (kernel is a fixture)

pytestmark = pytest.mark.ci_safe

SVC = Path(__file__).resolve().parents[1]


def forgets(k):
    return [(s, a) for s, op, a in k.calls if op == "forget"]


@pytest.mark.asyncio
async def test_forget_user_reaches_the_kernel_as_the_member_through_the_panel_surface(kernel):
    assert await os_shadow.forget_user(MEMBER) == 3
    assert forgets(kernel) == [("panel", {"subject": MEMBER, "actor": MEMBER})]


@pytest.mark.asyncio
async def test_entity_and_topic_forgets_are_scoped_in_the_kernel(kernel):
    await os_shadow.erase_entity(MEMBER, "Dana")
    await os_shadow.erase_topic(MEMBER, "garden shed")
    assert forgets(kernel) == [("panel", {"subject": MEMBER, "actor": MEMBER, "entity": "Dana"}),
                               ("panel", {"subject": MEMBER, "actor": MEMBER, "topic": "garden shed"})]


@pytest.mark.asyncio
async def test_a_scope_the_kernel_cannot_match_widens_to_the_whole_member(kernel):
    kernel.forget_mode = "refuse_scoped"
    assert await os_shadow.erase_entity(MEMBER, "Dana") == 3
    assert [a for _, a in forgets(kernel)] == [{"subject": MEMBER, "actor": MEMBER, "entity": "Dana"},
                                               {"subject": MEMBER, "actor": MEMBER}]


@pytest.mark.asyncio
async def test_a_member_the_kernel_never_knew_is_a_no_op_not_a_failure(kernel):
    kernel.known = {MEMBER}
    assert await os_shadow.forget_user("someone-else") == 0                      # the KERNEL says it never knew them ...
    assert forgets(kernel) == [("panel", {"subject": "someone-else", "actor": "someone-else"})]   # ... it was asked, by id


@pytest.mark.asyncio
async def test_a_member_taken_off_the_roster_is_still_forgotten_by_id(kernel):
    """Review finding: the roster says who may be proposed NOW, not who has records. A member removed from people.json after the
    kernel recorded them must still be forgotten."""
    (kernel.state / "people.json").write_text(json.dumps({"people": ["member-b"]}))   # MEMBER is no longer in the roster
    os_shadow._roster_cache = (0.0, None, frozenset())
    assert MEMBER not in os_shadow.roster()
    assert await os_shadow.forget_user(MEMBER) == 3
    assert await os_shadow.erase_entity(MEMBER, "Dana") == 3
    assert [a["subject"] for _, a in forgets(kernel)] == [MEMBER, MEMBER]


@pytest.mark.asyncio
async def test_a_refusal_that_is_not_never_knew_them_still_fails_closed(kernel):
    kernel.forget_mode = "refuse_all"
    with pytest.raises(os_shadow.ShadowForgetUnavailable):
        await os_shadow.forget_user("member-z")


@pytest.mark.asyncio
async def test_a_proposal_queued_before_the_erase_is_sent_first_and_so_removed_by_it(kernel):
    kernel.delay = 0.1
    add()
    await os_shadow.forget_user(MEMBER)
    ops = [op for _, op, _ in kernel.calls]
    assert ops == ["publish", "act", "forget"]                       # FIFO: the add the member made before the forget cannot outlive it


@pytest.mark.asyncio
async def test_shadow_on_fails_closed_when_the_kernel_cannot_confirm(kernel):
    kernel.forget_mode = "refuse_all"
    with pytest.raises(os_shadow.ShadowForgetUnavailable):
        await os_shadow.forget_user(MEMBER)
    kernel.forget_mode = "ok"
    kernel.srv.close()
    Path(kernel.path).unlink()
    os_shadow._reset_for_tests()                                       # drop the worker's cached connections
    with pytest.raises(os_shadow.ShadowForgetUnavailable):
        await os_shadow.forget_user(MEMBER)
    with pytest.raises(os_shadow.ShadowForgetUnavailable):
        await os_shadow.erase_entity(MEMBER, "Dana")
    with pytest.raises(os_shadow.ShadowForgetUnavailable):
        await os_shadow.erase_topic(MEMBER, "garden shed")


@pytest.mark.asyncio
async def test_flag_off_with_no_kernel_ever_installed_touches_nothing(kernel, monkeypatch):
    monkeypatch.setenv("ZOE_OS_LIST_SHADOW", "off")
    (kernel.state / "creds.json").unlink()
    assert await os_shadow.forget_user(MEMBER) == 0
    assert kernel.connections == 0 and kernel.calls == []


@pytest.mark.asyncio
async def test_flag_off_with_a_kernel_down_parks_the_forget_and_replays_it_before_the_next_proposal(kernel, monkeypatch):
    monkeypatch.setenv("ZOE_OS_LIST_SHADOW", "off")
    real_path = kernel.path
    (kernel.state / "creds.json").write_text(json.dumps({"socket": str(kernel.dir / "gone.sock"),
                                                         "creds": {s: c.hex() for s, c in kernel.creds.items()}}))
    assert await os_shadow.forget_user(MEMBER) == 0                    # the erase is not blocked ...
    assert json.loads((kernel.state / "pending-forgets.json").read_text()) == [MEMBER]   # ... and is not lost
    assert oct((kernel.state / "pending-forgets.json").stat().st_mode & 0o777) == "0o600"
    os_shadow._reset_for_tests()
    (kernel.state / "creds.json").write_text(json.dumps({"socket": real_path, "creds": {s: c.hex() for s, c in kernel.creds.items()}}))
    monkeypatch.setenv("ZOE_OS_LIST_SHADOW", "shadow")
    assert os_shadow.revive()                                                       # start-up
    add()
    settle(1)
    assert [op for _, op, _ in kernel.calls] == ["forget", "publish", "act"]        # the parked forget went first
    assert not (kernel.state / "pending-forgets.json").exists()


# ---- the parked forgets: locked, atomic, durable, fail closed -------------------------------------------------------------------------
def _pending(kernel):
    return json.loads((kernel.state / "pending-forgets.json").read_text())


def test_concurrent_parks_never_overwrite_each_other(kernel):
    import threading

    ids = [f"member-{i:02d}" for i in range(24)]
    barrier = threading.Barrier(len(ids))
    workers = [threading.Thread(target=lambda m=m: (barrier.wait(), os_shadow._pending_add(m))) for m in ids]
    for w in workers:
        w.start()
    for w in workers:
        w.join(5)
    assert _pending(kernel) == sorted(ids)                    # an unlocked read-modify-write loses some of these


def test_a_replay_keeps_a_forget_parked_while_it_ran(kernel, monkeypatch):
    os_shadow._pending_add("member-a")
    real = os_shadow._kernel_forget

    def slow_forget(k, member, entity, topic):
        os_shadow._pending_add("member-late")                 # another erase parks while the replay is talking to the kernel
        return real(k, member, entity, topic)

    monkeypatch.setattr(os_shadow, "_kernel_forget", slow_forget)
    assert os_shadow._replay_pending(os_shadow._Kernel()) is False
    assert _pending(kernel) == ["member-late"]                # the old replay rewrote the file with its own snapshot: lost


@pytest.mark.asyncio
async def test_a_park_that_cannot_be_made_durable_fails_the_erase_closed(kernel, monkeypatch):
    monkeypatch.setenv("ZOE_OS_LIST_SHADOW", "off")
    (kernel.state / "creds.json").write_text(json.dumps({"socket": str(kernel.dir / "gone.sock"),
                                                         "creds": {s: c.hex() for s, c in kernel.creds.items()}}))

    def no_disk(member):
        raise OSError("disk full")

    monkeypatch.setattr(os_shadow, "_pending_add", no_disk)
    with pytest.raises(os_shadow.ShadowForgetUnavailable):    # the old code swallowed this and the delete said "done"
        await os_shadow.forget_user(MEMBER)


@pytest.mark.asyncio
async def test_an_unreadable_park_file_is_never_overwritten_and_fails_closed(kernel, monkeypatch):
    monkeypatch.setenv("ZOE_OS_LIST_SHADOW", "off")
    (kernel.state / "creds.json").write_text(json.dumps({"socket": str(kernel.dir / "gone.sock"),
                                                         "creds": {s: c.hex() for s, c in kernel.creds.items()}}))
    (kernel.state / "pending-forgets.json").write_text("not json")
    with pytest.raises(os_shadow.ShadowForgetUnavailable):
        await os_shadow.forget_user(MEMBER)
    assert (kernel.state / "pending-forgets.json").read_text() == "not json"


# ---- the forget paths call it, and the inventory knows the store -----------------------------------------------------------------------
def _calls_in(path: str, func: str, attr: str) -> list:
    tree = ast.parse((SVC / path).read_text("utf-8"))
    fn = next(n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == func)
    return [n for n in ast.walk(fn) if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == attr
            and getattr(n.func.value, "id", "") == "os_shadow"]


def test_delete_user_forgets_the_kernel_record_in_a_fail_closed_block():
    assert _calls_in("memory_service.py", "delete_user", "forget_user")
    src = (SVC / "memory_service.py").read_text("utf-8")
    assert 'raise MemoryServiceError(f"delete_user failed: Zoe OS shadow erasure failed' in src


def test_the_topic_forget_runs_before_the_first_destructive_step_and_marks_a_retry():
    assert _calls_in("forget_topic.py", "_forget", "erase_topic")
    src = (SVC / "forget_topic.py").read_text("utf-8")
    assert src.index("os_shadow.erase_topic") < src.index("_erase_words(user_id, groups)") and '["os_shadow"]' in src


@pytest.mark.asyncio
async def test_the_entity_cascade_step_reaches_the_kernel_and_a_failure_is_recorded_not_swallowed(kernel, monkeypatch):
    import memory_forget_cascade as cascade
    monkeypatch.setattr(cascade, "RETRY_DELAY_S", 0)
    res = cascade.CascadeResult()
    await cascade._clear(MEMBER, None, None, res, only=frozenset({"os_shadow"}), name="Dana")
    assert res.failed == [] and forgets(kernel)[0][1].get("entity") == "Dana"
    kernel.forget_mode = "refuse_all"
    res = cascade.CascadeResult()
    await cascade._clear(MEMBER, None, None, res, only=frozenset({"os_shadow"}), name="Dana")
    assert res.failed == ["os_shadow"]                               # the cascade will not say "forgotten", and leaves the marker
    kernel.forget_mode = "ok"
    n = len(forgets(kernel))
    res = cascade.CascadeResult()
    await cascade._clear(MEMBER, None, None, res, only=frozenset({"os_shadow"}))      # the retry: no name stored -> whole member
    assert res.failed == [] and forgets(kernel)[n][1] == {"subject": MEMBER, "actor": MEMBER}
    assert "os_shadow" in cascade.ALL_LABELS


def test_the_inventory_lists_the_kernel_store_as_reached_by_both_erasers():
    import forget_reach as fr
    s = fr.BY_KEY["zoe_os_shadow_kernel"]
    assert s.status == fr.REACHED and s.member == "os_shadow.forget_user" and s.entity == "os_shadow.erase_entity"


def test_write_roster_is_ids_only_and_private(tmp_path):
    dest = tmp_path / "state" / "people.json"
    assert os_shadow.write_roster(["b", " a ", "", "a"], dest) == ["a", "b"]
    assert json.loads(dest.read_text()) == {"people": ["a", "b"]} and oct(dest.stat().st_mode & 0o777) == "0o600"
    assert os_shadow.main(["--write-roster"]) == 2


def test_a_failing_replay_never_costs_the_add_behind_it_its_line(kernel, monkeypatch, caplog):
    import logging

    caplog.set_level(logging.INFO, logger="os_shadow")
    os_shadow._reset_for_tests()
    monkeypatch.setattr(os_shadow, "_replay_pending", lambda k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert os_shadow.revive()
    add()
    settle(1)
    # the park file was never read, so whether a forget is parked is unknown: the add fails closed (never sent ahead of a forget)
    # and still writes its one line
    assert os_shadow.counters() == {"dropped_erased": 1}
    (line,) = [r.getMessage() for r in caplog.records if r.getMessage().startswith("OS_SHADOW_ACT verb=list_add ")]
    assert " reason=pending_forget " in line


# ---- forgotten means forever: a parked forget reaches the kernel before any later add, and a forget never blocks the loop ----------
def _creds(kernel, sock):
    (kernel.state / "creds.json").write_text(json.dumps({"socket": sock, "creds": {s: c.hex() for s, c in kernel.creds.items()}}))


@pytest.mark.asyncio
async def test_a_forget_that_failed_as_the_first_job_still_goes_before_the_next_add_in_the_same_process(kernel, monkeypatch):
    monkeypatch.setattr(os_shadow, "CIRCUIT_S", 0.0)
    monkeypatch.setenv("ZOE_OS_LIST_SHADOW", "off")
    _creds(kernel, str(kernel.dir / "gone.sock"))
    assert await os_shadow.forget_user(MEMBER) == 0                       # the worker's FIRST job: failed, parked
    assert _pending(kernel) == [MEMBER]
    _creds(kernel, kernel.path)
    monkeypatch.setenv("ZOE_OS_LIST_SHADOW", "shadow")                    # same worker, no restart
    add()
    settle(1)
    assert [op for _, op, _ in kernel.calls] == ["forget", "publish", "act"]
    assert not (kernel.state / "pending-forgets.json").exists() and os_shadow._pending_members == frozenset()


@pytest.mark.asyncio
async def test_while_the_kernel_is_still_down_a_parked_members_add_is_dropped_never_sent_first(kernel, monkeypatch, caplog):
    import logging

    caplog.set_level(logging.INFO, logger="os_shadow")
    monkeypatch.setenv("ZOE_OS_LIST_SHADOW", "off")
    _creds(kernel, str(kernel.dir / "gone.sock"))
    assert await os_shadow.forget_user(MEMBER) == 0                       # parked; the failed connect opened the 5 s circuit
    _creds(kernel, kernel.path)
    monkeypatch.setenv("ZOE_OS_LIST_SHADOW", "shadow")
    add()
    settle(1)
    assert os_shadow.counters() == {"dropped_erased": 1} and [op for _, op, _ in kernel.calls if op == "act"] == []
    assert any(" result=dropped_erased reason=pending_forget " in r.getMessage() for r in caplog.records)
    assert _pending(kernel) == [MEMBER]                                   # still parked: replayed once the circuit closes


def test_a_parked_forget_the_kernel_refuses_keeps_that_members_adds_out(kernel):
    os_shadow._pending_add(MEMBER)
    os_shadow._reset_for_tests()                                          # a restart: the park file is read before the first job
    kernel.forget_mode = "refuse_all"
    assert os_shadow.revive()
    add()
    settle(1)
    assert os_shadow.counters() == {"dropped_erased": 1}
    assert [op for _, op, _ in kernel.calls] == ["forget"]                # tried first; refused; the add never went
    assert _pending(kernel) == [MEMBER]


@pytest.mark.asyncio
async def test_after_a_restart_the_parked_forget_is_replayed_before_the_first_job_even_a_forget(kernel, monkeypatch):
    os_shadow._pending_add(MEMBER)
    os_shadow._reset_for_tests()
    assert os_shadow._pending_members is None                             # this process has not read the file yet
    assert os_shadow.revive()
    assert await os_shadow.forget_user("member-b") == 3
    assert [a["subject"] for _, a in forgets(kernel)] == [MEMBER, "member-b"]


@pytest.mark.asyncio
@pytest.mark.parametrize("flag", ["off", "shadow"])
async def test_a_full_queue_never_blocks_the_loop_and_the_forget_is_parked_not_lost(kernel, monkeypatch, flag):
    import asyncio
    import queue

    monkeypatch.setenv("ZOE_OS_LIST_SHADOW", flag)
    ticks = []

    async def ticker():
        while True:
            ticks.append(time.perf_counter())
            await asyncio.sleep(0.005)

    full: queue.Queue = queue.Queue(maxsize=1)
    full.put_nowait(("stop",))
    with monkeypatch.context() as m:
        m.setattr(os_shadow, "_q", full)
        t = asyncio.create_task(ticker())
        await asyncio.sleep(0.02)
        before, t0 = len(ticks), time.perf_counter()
        if flag == "off":
            assert await os_shadow.forget_user(MEMBER) == 0              # deferred, and said so
        else:
            with pytest.raises(os_shadow.ShadowForgetUnavailable):       # the flag on: not confirmed is never "forgotten"
                await os_shadow.forget_user(MEMBER)
        took = time.perf_counter() - t0
        await asyncio.sleep(0.02)
        t.cancel()
    assert took < 0.5, f"the forget held the loop {took:.2f} s"         # the old blocking put held it 2 s
    assert len(ticks) > before                                            # other tasks ran
    assert _pending(kernel) == [MEMBER]
    monkeypatch.setenv("ZOE_OS_LIST_SHADOW", "shadow")
    add()
    settle(1)
    assert [op for _, op, _ in kernel.calls] == ["forget", "publish", "act"]


def test_no_forget_coroutine_makes_a_blocking_queue_or_park_call():
    """Structure: inside an async function of os_shadow.py, no ``.put(`` / ``.get(`` with a wait, and the park (flock + fsync) only
    through an executor."""
    tree = ast.parse((SVC / "os_shadow.py").read_text("utf-8"))
    for fn in [n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)]:
        for c in ast.walk(fn):
            if isinstance(c, ast.Call):
                name = getattr(c.func, "attr", getattr(c.func, "id", ""))
                assert name not in ("put", "get", "_pending_add", "_pending_done", "_pending_write", "join", "sleep"), (fn.name, name)
