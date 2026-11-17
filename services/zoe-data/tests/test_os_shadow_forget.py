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
from test_erase_outruns_digest import env  # noqa: F401  (a fixture: a MemoryService over a fake memory store)
from test_os_shadow import ITEM, MEMBER, FakeKernel, add, kernel, settle  # noqa: F401  (kernel is a fixture)
from test_value_correction_forget import A as TOPIC_MEMBER
from test_value_correction_forget import _Svc as _TopicSvc
from test_value_correction_forget import forget_env  # noqa: F401  (a fixture: the topic forget's stores, in memory)

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
@pytest.mark.parametrize("where", ["absent", "empty"])
async def test_flag_off_with_no_shadow_state_at_all_touches_nothing(kernel, monkeypatch, tmp_path, where):
    """The ONE no-op branch: the flag off and no state (an absent or empty state directory). Removing only creds.json is NOT this
    branch (people.json etc. still count as state: parked, see the credential-gone tests)."""
    import threading

    monkeypatch.setenv("ZOE_OS_LIST_SHADOW", "off")
    d = tmp_path / "state"
    if where == "empty":
        d.mkdir()
    monkeypatch.setenv("ZOE_OS_SHADOW_DIR", str(d))
    os_shadow._reset_for_tests()                                       # no worker before the call
    for call in (lambda: os_shadow.forget_user(MEMBER), lambda: os_shadow.erase_entity(MEMBER, "Dana"),
                 lambda: os_shadow.erase_topic(MEMBER, "garden shed")):
        assert await call() == 0
    assert kernel.connections == 0 and kernel.calls == []
    assert (not d.exists()) if where == "absent" else list(d.iterdir()) == []          # no file was created
    assert os_shadow._worker is None and "os-shadow" not in [t.name for t in threading.enumerate()]   # no worker was started


@pytest.mark.asyncio
async def test_flag_off_with_a_kernel_down_parks_the_forget_and_replays_it_before_the_next_proposal(kernel, monkeypatch):
    monkeypatch.setenv("ZOE_OS_LIST_SHADOW", "off")
    real_path = kernel.path
    (kernel.state / "creds.json").write_text(json.dumps({"socket": str(kernel.dir / "gone.sock"),
                                                         "creds": {s: c.hex() for s, c in kernel.creds.items()}}))
    with pytest.raises(os_shadow.ShadowForgetUnavailable):             # not confirmed, flag off too: the erase fails closed ...
        await os_shadow.forget_user(MEMBER)
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


FAILURES = {
    "unreachable": lambda k: _creds(k, str(k.dir / "gone.sock")),
    "refusing": lambda k: setattr(k, "forget_mode", "refuse_all"),
    "malformed": lambda k: setattr(k, "forget_mode", "reply:{}"),
}


def _recover(k, mode):
    if mode == "unreachable":
        _creds(k, k.path)
    k.forget_mode = "ok"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", sorted(FAILURES))
async def test_a_member_delete_is_not_confirmed_while_the_shadow_kernel_fails_and_a_retry_completes(kernel, env, mode):  # noqa: F811
    """RUNS the real MemoryService.delete_user (over the fake memory store of test_erase_outruns_digest) with the shadow on and its
    kernel unreachable, refusing, or answering a reply that is not the documented one: the delete raises (not confirmed), and once the
    kernel recovers the retry completes and nothing is left parked."""
    import memory_service

    svc, _col = env
    FAILURES[mode](kernel)
    with pytest.raises(memory_service.MemoryServiceError, match="Zoe OS shadow erasure failed"):
        await svc.delete_user(MEMBER, actor=MEMBER)
    assert _pending(kernel) == [MEMBER]
    _recover(kernel, mode)
    await svc.delete_user(MEMBER, actor=MEMBER)                          # the retry: confirmed
    assert forgets(kernel)[-1] == ("panel", {"subject": MEMBER, "actor": MEMBER})
    assert not (kernel.state / "pending-forgets.json").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", sorted(FAILURES))
async def test_a_topic_forget_stops_before_any_destructive_step_while_the_shadow_kernel_fails(kernel, forget_env, monkeypatch, mode):  # noqa: F811
    """RUNS forget_topic.forget: with the shadow's kernel failing, the topic forget reports store_down, erases no words (the step after
    the shadow's), records the retry marker, and parks the shadow forget; after recovery the same forget completes."""
    import forget_topic
    import memory_forget_cascade

    words, marks = [], []
    real_erase = forget_topic._erase_words

    async def erase_words(user_id, groups):
        words.append(user_id)
        return await real_erase(user_id, groups)

    async def mark(user_id, labels):
        marks.append(tuple(labels))

    monkeypatch.setattr(forget_topic, "_erase_words", erase_words)
    monkeypatch.setattr(memory_forget_cascade, "_mark_pending", mark)
    FAILURES[mode](kernel)
    res = await forget_topic.forget(_TopicSvc(("r1", "The book club meets at the library")), TOPIC_MEMBER, "the book club")
    assert res.store_down and words == [] and marks == [("os_shadow",)] and _pending(kernel) == [TOPIC_MEMBER]
    _recover(kernel, mode)
    res = await forget_topic.forget(_TopicSvc(("r1", "The book club meets at the library")), TOPIC_MEMBER, "the book club")
    assert not res.store_down and words == [TOPIC_MEMBER]


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
    # since round 3 the refused forget was also PARKED, so the worker replays it (whole member) before the retry; the retry itself
    # is the LAST forget and is a whole-member one too, and nothing is left parked
    assert res.failed == [] and forgets(kernel)[-1][1] == {"subject": MEMBER, "actor": MEMBER}
    assert len(forgets(kernel)) == n + 2 and not (kernel.state / "pending-forgets.json").exists()
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
    monkeypatch.setattr(os_shadow, "_replay_pending", lambda k, **kw: (_ for _ in ()).throw(RuntimeError("boom")))   # the worker passes force=
    assert os_shadow.revive()
    add()
    settle(1)
    assert any(r.getMessage() == "OS_SHADOW replay error=RuntimeError" for r in caplog.records)   # the INJECTED error, not a TypeError
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
    with pytest.raises(os_shadow.ShadowForgetUnavailable):                # the worker's FIRST job: failed, parked, not "done"
        await os_shadow.forget_user(MEMBER)
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
    with pytest.raises(os_shadow.ShadowForgetUnavailable):                # parked; the failed connect opened the 5 s circuit
        await os_shadow.forget_user(MEMBER)
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
    real_park = os_shadow._pending_add

    def slow_park(member):                                    # a park that takes 300 ms (a slow disk): it must run OFF the loop
        time.sleep(0.3)
        return real_park(member)

    with monkeypatch.context() as m:
        m.setattr(os_shadow, "_q", full)
        m.setattr(os_shadow, "_pending_add", slow_park)
        t = asyncio.create_task(ticker())
        await asyncio.sleep(0.02)
        t0 = time.perf_counter()
        with pytest.raises(os_shadow.ShadowForgetUnavailable):           # either flag: not confirmed is never "forgotten"
            await os_shadow.forget_user(MEMBER)
        t1 = time.perf_counter()
        t.cancel()
    during = sum(1 for x in ticks if t0 < x < t1)
    assert t1 - t0 >= 0.3                                                 # the slow park really ran inside the forget
    assert during >= 20, f"only {during} ticks while the forget ran: the loop was blocked"   # a blocked loop gets 0; free ~60
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
                assert name not in ("put", "get", "_pending_add", "_pending_done", "_pending_write", "join", "sleep",
                                    "shadow_state_exists", "provisioned", "_pending_read", "stat", "iterdir", "exists", "is_dir",
                                    "open", "read_text", "write_text", "flock", "fsync"), (fn.name, name)


# ---- an add stamped BEFORE a forget never reaches the kernel AFTER it ----------------------------------------------------------------
def _acts(kernel):
    return [(op, a.get("args", {}).get("item")) for _, op, a in kernel.calls if op in ("act", "forget")]


@pytest.mark.asyncio
async def test_adds_stamped_before_and_queued_ahead_of_a_forget_go_first_and_the_forget_removes_them(kernel):
    """The queued path (the claim checked): one FIFO worker. Adds stamped before the forget are queued AHEAD of it, so the kernel
    gets them first and the forget removes them; an add in flight when the forget is enqueued finishes before the worker takes it."""
    import asyncio

    kernel.delay = 0.1                                       # the first add is in flight (mid-socket) when the forget is enqueued
    add(item="in flight")
    await asyncio.sleep(0.02)
    add(item="queued")
    assert await os_shadow.forget_user(MEMBER) == 3
    assert _acts(kernel) == [("act", "in flight"), ("act", "queued"), ("forget", None)]


@pytest.mark.asyncio
async def test_adds_stamped_before_a_parked_forget_are_dropped_not_sent_after_it(kernel, monkeypatch):
    """The real hole (the park path): the queue is full of the member's adds, so the forget is PARKED; the worker replays it before
    the next job, and the adds still queued ahead were stamped before it: sent now, they would re-create the member. Dropped."""
    import queue

    os_shadow._reset_for_tests()
    monkeypatch.setattr(os_shadow, "_q", queue.Queue(maxsize=2))
    kernel.delay = 0.15
    assert os_shadow.revive()
    add(item="first")                                        # the worker takes it and is busy on the socket
    time.sleep(0.05)
    add(item="b")
    add(item="c")                                            # the queue (2) is full
    with pytest.raises(os_shadow.ShadowForgetUnavailable):   # the flag is shadow: not confirmed, parked
        await os_shadow.forget_user(MEMBER)
    settle(3, timeout=5)
    assert _acts(kernel) == [("act", "first"), ("forget", None)]
    assert os_shadow.counters() == {"match": 1, "dropped_erased": 2}
    add(item="after")                                        # made after the forget completed: still a member, still shadowed
    settle(4, timeout=5)
    assert _acts(kernel)[-1] == ("act", "after") and os_shadow.counters()["match"] == 2


@pytest.mark.asyncio
async def test_an_add_made_after_a_forget_completed_is_shadowed(kernel):
    assert await os_shadow.erase_entity(MEMBER, "Dana") == 3      # a scoped forget: the member still acts afterwards
    add()
    settle(1)
    assert os_shadow.counters() == {"match": 1} and _acts(kernel)[-1] == ("act", "oat milk")


@pytest.mark.parametrize("field", ["entity", "topic"])
@pytest.mark.asyncio
async def test_marker_text_in_a_forget_never_reaches_a_log_line_or_a_counter(kernel, caplog, monkeypatch, field):
    import logging

    caplog.set_level(logging.DEBUG)
    mark = "Zq7Marker"
    kernel.forget_mode = "refuse_all"                        # the failure path logs too
    with pytest.raises(os_shadow.ShadowForgetUnavailable):
        await (os_shadow.erase_entity(MEMBER, mark) if field == "entity" else os_shadow.erase_topic(MEMBER, mark))
    assert mark not in " ".join(r.getMessage() for r in caplog.records) and not any(mark in str(k) for k in os_shadow.counters())



# ---- a missing credential is never proof there is nothing to forget -------------------------------------------------------------------
def _db_without_creds(kernel):
    (kernel.state / "creds.json").unlink()
    (kernel.state / "kernel").mkdir(exist_ok=True)
    (kernel.state / "kernel" / "zoe-os.db").write_bytes(b"x")        # the kernel's store outlived its credential


@pytest.mark.asyncio
@pytest.mark.parametrize("call", ["forget_user", "erase_entity", "erase_topic"])
async def test_a_forget_with_the_credential_gone_but_a_store_present_is_parked_then_replayed(kernel, monkeypatch, call):
    creds = (kernel.state / "creds.json").read_text()
    monkeypatch.setenv("ZOE_OS_LIST_SHADOW", "off")
    _db_without_creds(kernel)
    fn = getattr(os_shadow, call)
    with pytest.raises(os_shadow.ShadowForgetUnavailable):               # fails closed (flag off too) AND parked: never a silent "done"
        await (fn(MEMBER) if call == "forget_user" else fn(MEMBER, "Dana"))
    assert _pending(kernel) == [MEMBER]
    (kernel.state / "creds.json").write_text(creds)                       # the credential comes back
    os_shadow._reset_for_tests()
    monkeypatch.setenv("ZOE_OS_LIST_SHADOW", "shadow")
    assert os_shadow.revive()
    add()
    settle(1)
    assert [op for _, op, _ in kernel.calls][:1] == ["forget"] and not (kernel.state / "pending-forgets.json").exists()


@pytest.mark.asyncio
async def test_with_the_shadow_on_a_forget_that_cannot_reach_the_kernel_raises_and_is_parked(kernel):
    _db_without_creds(kernel)
    with pytest.raises(os_shadow.ShadowForgetUnavailable):
        await os_shadow.forget_user(MEMBER)
    assert _pending(kernel) == [MEMBER]


@pytest.mark.asyncio
async def test_only_a_state_directory_with_nothing_in_it_is_a_no_op(kernel, monkeypatch, tmp_path):
    monkeypatch.setenv("ZOE_OS_LIST_SHADOW", "off")
    empty = tmp_path / "empty"
    empty.mkdir()
    for d, expect in ((tmp_path / "absent", False), (empty, False), (kernel.state, True)):
        monkeypatch.setenv("ZOE_OS_SHADOW_DIR", str(d))
        assert os_shadow.shadow_state_exists() is expect, d
    monkeypatch.setenv("ZOE_OS_SHADOW_DIR", str(empty))
    assert await os_shadow.forget_user(MEMBER) == 0 and kernel.calls == [] and not (empty / "pending-forgets.json").exists()
    not_a_dir = tmp_path / "file"
    not_a_dir.write_text("x")
    monkeypatch.setenv("ZOE_OS_SHADOW_DIR", str(not_a_dir))
    assert os_shadow.shadow_state_exists() is True                       # unreadable / not a directory: counts as state
    with pytest.raises(os_shadow.ShadowForgetUnavailable):               # and since it cannot be parked there either: fails closed
        await os_shadow.forget_user(MEMBER)



@pytest.mark.asyncio
@pytest.mark.parametrize("flag", ["off", "shadow"])
async def test_the_state_check_and_the_park_never_run_on_the_event_loop(kernel, monkeypatch, flag):
    """A slow disk: the state check (flag off) and the park (both flags, the kernel down) each take 200 ms. Ticks are counted DURING the
    forget; a blocked loop gets none."""
    import asyncio

    monkeypatch.setenv("ZOE_OS_LIST_SHADOW", flag)
    _creds(kernel, str(kernel.dir / "gone.sock"))                        # the kernel cannot confirm: the forget parks
    real_check, real_park = os_shadow.shadow_state_exists, os_shadow._pending_add
    calls, spans = [], {}

    def slow(name, fn):
        def run(*a):
            t = time.perf_counter()
            time.sleep(0.2)
            spans[name] = (t, time.perf_counter())
            calls.append(name)
            return fn(*a)
        return run

    monkeypatch.setattr(os_shadow, "shadow_state_exists", slow("check", real_check))
    monkeypatch.setattr(os_shadow, "_pending_add", slow("park", real_park))
    ticks = []

    async def ticker():
        while True:
            ticks.append(time.perf_counter())
            await asyncio.sleep(0.005)

    t = asyncio.create_task(ticker())
    await asyncio.sleep(0.02)
    t0 = time.perf_counter()
    with pytest.raises(os_shadow.ShadowForgetUnavailable):
        await os_shadow.forget_user(MEMBER)
    t1 = time.perf_counter()
    t.cancel()
    assert t1 - t0 >= (0.4 if flag == "off" else 0.2)                     # the slow check / park really ran inside the forget
    assert calls == (["check", "park"] if flag == "off" else ["park"]) and _pending(kernel) == [MEMBER]
    for name, (a, b) in spans.items():                                    # ticks DURING each slow step: a blocked loop gets none
        during = sum(1 for x in ticks if a < x < b)
        assert during >= 15, f"only {during} ticks during the {name}: it ran on the event loop"


# ---- only the kernel's DOCUMENTED forget reply is a "forgotten" ---------------------------------------------------------------------
BAD_REPLIES = ['null', '{}', '{"ok": true}', '{"count": "3", "upto": 1}', '{"count": true, "upto": 1}', '{"count": -1, "upto": 1}',
               '{"count": NaN, "upto": 1}', '{"count": 3, "upto": 1.5}', '{"count": 3}', '{"count": 3, "upto": 1, "extra": 1}',
               '{"extra": 1}', '[3, 1]']


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", BAD_REPLIES)
@pytest.mark.parametrize("flag", ["off", "shadow"])
async def test_a_forget_reply_that_is_not_the_documented_one_is_never_a_success(kernel, monkeypatch, reply, flag):
    monkeypatch.setenv("ZOE_OS_LIST_SHADOW", flag)
    kernel.forget_mode = "reply:" + reply
    with pytest.raises(os_shadow.ShadowForgetUnavailable):               # the caller is told it failed, either flag
        await os_shadow.forget_user(MEMBER)
    assert _pending(kernel) == [MEMBER]                                   # the durable retry marker stays
    os_shadow._replay_pending(os_shadow._Kernel())                        # a replay against the same bad reply keeps it too
    assert _pending(kernel) == [MEMBER]
    kernel.forget_mode = "ok"                                             # the real reply clears it
    assert os_shadow._replay_pending(os_shadow._Kernel()) is True and not (kernel.state / "pending-forgets.json").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("call", ["erase_entity", "erase_topic"])
async def test_a_scoped_forget_needs_the_documented_reply_too(kernel, call):
    kernel.forget_mode = 'reply:{"count": 3}'
    with pytest.raises(os_shadow.ShadowForgetUnavailable):
        await getattr(os_shadow, call)(MEMBER, "Dana")
    assert _pending(kernel) == [MEMBER]


def test_forget_ack_reads_exactly_the_documented_shape():
    assert os_shadow.forget_ack({"count": 0, "upto": 0}) == 0 and os_shadow.forget_ack({"count": 3, "upto": 9}) == 3
    for bad in (None, {}, {"count": 3}, {"count": True, "upto": 1}, {"count": 3, "upto": -1}, {"count": 3.0, "upto": 1},
                {"count": float("nan"), "upto": 1}, {"count": 3, "upto": 1, "x": 0}):
        with pytest.raises(os_shadow.KernelProtocol):
            os_shadow.forget_ack(bad)


# ---- no thread start, join, blocking lock, blocking put or file call on the turn's loop ---------------------------------------------
TURN_FACING = ("note_add", "_door", "_finish", "_tally", "_worker_down", "_worker_alive", "list_class", "mode", "_forget", "forget_user",
               "erase_entity", "erase_topic",
               # the widened verbs (#2631): the one hook and its per-verb builders, all called synchronously after a live commit
               "_note", "note_act", "note_list_remove", "note_reminder_create", "note_alarm_create", "_list_raw", "_text", "wants", "verbs")
FORBIDDEN_ON_LOOP = {"Thread", "start", "join", "put", "sleep", "_ensure_worker", "revive", "shadow_state_exists", "_pending_add",
                     "_pending_read", "_pending_write", "_pending_done", "open", "read_text", "write_text", "stat", "iterdir", "exists",
                     "is_dir", "flock", "fsync", "connect", "sendall", "recv"}


def test_the_turn_facing_functions_never_block_the_loop():
    """Structure: in every function a turn (intent_router, list_service, skybridge, the member delete) reaches synchronously, no call
    that starts or joins a thread, takes a lock BLOCKING (``acquire()`` without ``blocking=False``), puts on a queue with a wait, or
    touches a file or socket; those go through ``asyncio.to_thread`` / an executor (passed as an argument, never called here).
    NOT checked here: a ``with <lock>:`` block. The one such lock on these paths is ``_lock`` (``_tally`` / ``counters``), held by
    every holder for in-memory counter updates only, never across I/O."""
    tree = ast.parse((SVC / "os_shadow.py").read_text("utf-8"))
    fns = {n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert set(TURN_FACING) <= set(fns)
    for name in TURN_FACING:
        for c in ast.walk(fns[name]):
            if not isinstance(c, ast.Call):
                continue
            called = getattr(c.func, "attr", getattr(c.func, "id", ""))
            assert called not in FORBIDDEN_ON_LOOP, (name, called)
            if called == "acquire":
                assert any(k.arg == "blocking" and isinstance(k.value, ast.Constant) and k.value.value is False for k in c.keywords), (name, "blocking acquire")


@pytest.mark.asyncio
@pytest.mark.parametrize("flag", ["off", "shadow"])
async def test_a_forget_that_has_to_start_the_worker_never_blocks_the_loop(kernel, monkeypatch, flag):
    """NO pre-started worker (the fixture's is stopped): the forget must start it, and the start takes 250 ms. Ticks are counted
    DURING the start; a start on the loop gets none. An add never starts a thread at all (it drops and counts worker_down)."""
    import asyncio
    import threading
    import types

    monkeypatch.setenv("ZOE_OS_LIST_SHADOW", flag)
    os_shadow._reset_for_tests()
    spans = []

    class SlowThread(threading.Thread):
        def start(self):
            t = time.perf_counter()
            time.sleep(0.25)
            super().start()
            spans.append((t, time.perf_counter()))

    monkeypatch.setattr(os_shadow, "threading", types.SimpleNamespace(Thread=SlowThread, Lock=threading.Lock,
                                                                       get_ident=threading.get_ident))
    await asyncio.to_thread(lambda: None)                                  # the default executor's thread exists already
    ticks = []

    async def ticker():
        while True:
            ticks.append(time.perf_counter())
            await asyncio.sleep(0.005)

    tk = asyncio.create_task(ticker())
    await asyncio.sleep(0.02)
    add()                                                                  # the add: no worker -> worker_down, no thread started
    assert spans == [] and os_shadow.counters() == ({"worker_down": 1} if flag == "shadow" else {})
    assert await os_shadow.forget_user(MEMBER) == 3                        # it starts the worker, which then confirms the forget
    tk.cancel()
    (a, b), = spans
    during = sum(1 for x in ticks if a < x < b)
    assert during >= 15, f"only {during} ticks during the worker start: it ran on the event loop"


# ---- the park file is durable both ways: a write AND the last member's removal fsync the directory -----------------------------------
@pytest.mark.parametrize("members", [["member-a"], []])
def test_every_park_file_change_fsyncs_its_directory(kernel, monkeypatch, members):
    import os

    os_shadow._pending_add("member-z")                                    # something is parked, so the empty write is an unlink
    synced, real = [], os.fsync
    dir_fds = {}
    real_open = os.open

    def track_open(path, flags, *a, **kw):                               # the runner's Python passes dir_fd=: pass everything through
        fd = real_open(path, flags, *a, **kw)
        if str(path) == str(kernel.state):
            dir_fds[fd] = path
        return fd

    monkeypatch.setattr(os, "open", track_open)
    monkeypatch.setattr(os, "fsync", lambda fd: (synced.append(fd in dir_fds), real(fd))[1])
    os_shadow._pending_write(members)
    assert any(synced), "the directory was not fsync'd"
    assert (kernel.state / "pending-forgets.json").exists() is bool(members)


def test_an_empty_write_with_nothing_parked_touches_nothing(kernel, monkeypatch):
    import os

    monkeypatch.setattr(os, "fsync", lambda fd: (_ for _ in ()).throw(AssertionError("fsync with nothing to make durable")))
    os_shadow._pending_write([])


# ---- the reviewer's case: shadow records, then the flag is turned OFF and the kernel STOPPED ---------------------------------------------
def _stop_kernel_with_records(kernel, monkeypatch):
    (kernel.state / "kernel").mkdir(exist_ok=True)
    (kernel.state / "kernel" / "zoe-os.db").write_bytes(b"recorded events and receipts")   # what the shadow recorded
    monkeypatch.setenv("ZOE_OS_LIST_SHADOW", "off")
    kernel.srv.close()
    Path(kernel.path).unlink()                                            # stopped: nothing answers the socket


@pytest.mark.asyncio
async def test_a_member_delete_with_the_shadow_off_and_its_kernel_stopped_is_not_reported_done(kernel, env, monkeypatch):  # noqa: F811
    import memory_service

    svc, _col = env
    _stop_kernel_with_records(kernel, monkeypatch)
    with pytest.raises(memory_service.MemoryServiceError, match="Zoe OS shadow erasure failed"):
        await svc.delete_user(MEMBER, actor=MEMBER)
    assert _pending(kernel) == [MEMBER]                                   # retried later, but the delete itself said NO


@pytest.mark.asyncio
@pytest.mark.parametrize("call", ["erase_entity", "erase_topic"])
async def test_a_scoped_forget_with_the_shadow_off_and_its_kernel_stopped_fails(kernel, monkeypatch, call):
    _stop_kernel_with_records(kernel, monkeypatch)
    with pytest.raises(os_shadow.ShadowForgetUnavailable):
        await getattr(os_shadow, call)(MEMBER, "Dana")


@pytest.mark.asyncio
async def test_a_topic_forget_with_the_shadow_off_and_its_kernel_stopped_reports_store_down(kernel, forget_env, monkeypatch):  # noqa: F811
    import forget_topic

    _stop_kernel_with_records(kernel, monkeypatch)
    res = await forget_topic.forget(_TopicSvc(("r1", "The book club meets at the library")), TOPIC_MEMBER, "the book club")
    assert res.store_down                                                 # not "forgotten" while the kernel's record may remain


# ---- second train review: a cancelled caller, and a newer park of the same member during a replay ----------------------------------
@pytest.mark.asyncio
async def test_a_cancelled_forget_is_still_parked_and_the_cancellation_goes_on(kernel):
    import asyncio

    kernel.stall[("forget", 1)] = 1.0                                    # the kernel sits on the forget: the caller is cancelled first
    kernel.stall[("forget", 2)] = 1.0
    task = asyncio.create_task(os_shadow.forget_user(MEMBER))
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert _pending(kernel) == [MEMBER]                                   # never lost: replayed before the next job


def test_a_newer_park_of_the_same_member_during_a_replay_stays_parked(kernel, monkeypatch):
    os_shadow._pending_add("member-a")
    real = os_shadow._kernel_forget

    def again(k, member, entity, topic):
        out = real(k, member, entity, topic)
        os_shadow._pending_add("member-a")                                # a NEWER unconfirmed forget of the same member, mid-replay
        return out

    monkeypatch.setattr(os_shadow, "_kernel_forget", again)
    assert os_shadow._replay_pending(os_shadow._Kernel()) is False
    assert _pending(kernel) == ["member-a"]                               # the confirmed request cleared, the newer one kept


# ---- EVERY erase entry point reaches the shadow (train review): the choke points and the registry --------------------------------
#: every member-data forget entry point -> the choke it goes through. A NEW forget entry point must reach one of CHOKES or be named
#: here; the scan below proves each named one does.
FORGET_ENTRY_POINTS = {
    "memory_service.py::delete_user": "os_shadow.forget_user (member delete, fail closed)",
    "memory_forget_cascade.py::_clear": "os_shadow.erase_entity / forget_user (entity forget and its retry)",
    "forget_topic.py::_forget": "os_shadow.erase_topic, then erase_rows / exact_words",
    "memory_space.py::forget_row": "erase_rows (the memories-screen row forget)",
    "memory_space.py::guardian_forget": "erase_rows (a guardian's forget)",
    "provenance_answers.py::forget_it": "erase_rows + exact_words (\"forget it\")",
    "intent_router.py::_execute_forget_last": "erase_rows + exact_words (\"forget that\")",
    "intent_router.py::execute_intent": "erase_rows (the list-correction forget)",
    "memory_curation.py::_forget_locked": "erase_rows (a curator's forget)",
    "omi_review_apply.py::unwrite": "erase_rows (the day-review unwrite)",
}
CHOKES = {"erase_rows", "erase_turn", "erase_text", "forget_transcript", "forget_user", "erase_entity", "erase_topic", "forget_for_erase"}
#: erase_rows callers allowed to skip the shadow, with the reason
NO_SHADOW_OK = {
    "memory_service.py::_undo_night_write": "the night's own fresh rows, never shadowed, under the memory lock",
    "memory_digest.py::_synthesis_pass": "synthesis undo of rows it just wrote, never shadowed",
}


def _fn_calls(path, name):
    tree = ast.parse((SVC / path).read_text("utf-8"))
    fn = next(n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)
    return [c for c in ast.walk(fn) if isinstance(c, ast.Call)], fn


def _called(c):
    return getattr(c.func, "attr", getattr(c.func, "id", ""))


def _reaches_choke(source, name):
    fn = next(n for n in ast.walk(ast.parse(source)) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)
    # ast.walk, not _own_walk: an entry point may reach the choke from a helper it defines inside itself (the cascade's per-store
    # closures); a call that exists only in a comment or a string is still no call.
    return bool({_called(c) for c in ast.walk(fn) if isinstance(c, ast.Call)} & CHOKES)


@pytest.mark.parametrize("site", sorted(FORGET_ENTRY_POINTS))
def test_every_forget_entry_point_reaches_a_shadow_choke(site):
    path, name = site.split("::")
    source = (SVC / path).read_text("utf-8")
    assert _reaches_choke(source, name), f"{site} erases member data without reaching the Zoe OS shadow"
    # Keep real comments, replacing all choke calls with strings containing their source.
    tree = ast.parse(source)
    fn = next(n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)
    lines = source.splitlines(keepends=True)
    spans = [(sum(map(len, lines[:n.lineno-1])) + n.col_offset,
              sum(map(len, lines[:n.end_lineno-1])) + n.end_col_offset)
             for n in ast.walk(fn) if isinstance(n, ast.Call) and _called(n) in CHOKES]
    assert spans
    for start, end in sorted(spans, reverse=True):
        source = source[:start] + repr(source[start:end]) + source[end:]
    assert not _reaches_choke(source, name)


def _own_walk(node):
    yield node
    for child in ast.iter_child_nodes(node):
        if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            yield from _own_walk(child)


def _shadow_before_delete(source, name, delete_names):
    """Check executable awaits, including the words helper; comments/literals cannot count.

    Delete callbacks passed to _run_sync count as deletes too. Ignore nested function
    definitions and reject awaits under branches reachable only with reach_shadow false.
    """
    tree = ast.parse(source)
    fn = next(n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)
    aliases = {a.asname or a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
               and n.module == "os_shadow" for a in n.names if a.name == "forget_for_erase"}

    def shadow(call):
        return (isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Name)
                and call.func.value.id == "os_shadow" and call.func.attr == "forget_for_erase"
                or isinstance(call.func, ast.Name) and call.func.id in aliases)

    parents = {child: n for n in ast.walk(fn) for child in ast.iter_child_nodes(n)}

    def reachable(n):
        child = n
        while child in parents:
            parent = parents[child]
            if isinstance(parent, (ast.If, ast.IfExp)):
                # Evaluate this condition with reach_shadow=True where it has no other names. The eval is safe: the expression is a
                # guard parsed from this repository's own source, it is evaluated ONLY when the one name it uses is reach_shadow
                # (the check on the line below), and with empty builtins, so it can call nothing and read nothing else.
                names = {v.id for v in ast.walk(parent.test) if isinstance(v, ast.Name)}
                if names <= {"reach_shadow"}:
                    condition = eval(compile(ast.Expression(parent.test), "<guard>", "eval"),
                                     {"__builtins__": {}}, {"reach_shadow": True})
                    blocked = parent.orelse if condition else parent.body
                    if child in (blocked if isinstance(blocked, list) else [blocked]):
                        return False
            child = parent
        return True

    nodes = list(_own_walk(fn))
    deletes = [c.lineno for c in nodes if isinstance(c, ast.Call)
               and (_called(c) in delete_names or any(isinstance(a, (ast.Name, ast.Attribute))
                    and getattr(a, "attr", getattr(a, "id", "")) in delete_names for a in c.args))]
    awaits = [n for n in nodes if isinstance(n, ast.Await) and isinstance(n.value, ast.Call) and reachable(n)]
    for n in awaits:
        if shadow(n.value) and (not delete_names or deletes and n.lineno < min(deletes)):
            return True
        if _called(n.value) == "_shadow_first" and deletes and n.lineno < min(deletes):
            return _shadow_before_delete(source, "_shadow_first", set())
    return False


def _replace_await(source, name, called, replacement):
    """Replace only the executable await span, leaving the original comments intact."""
    tree = ast.parse(source)
    fn = next(n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)
    node = next(n for n in _own_walk(fn) if isinstance(n, ast.Await) and isinstance(n.value, ast.Call)
                and _called(n.value) == called)
    lines = source.splitlines(keepends=True)
    start = sum(map(len, lines[:node.lineno - 1])) + node.col_offset
    end = sum(map(len, lines[:node.end_lineno - 1])) + node.end_col_offset
    return source[:start] + replacement + source[end:]


@pytest.mark.parametrize("path,name,deletes,call", [
    ("memory_service.py", "erase_rows", {"_delete_ids"}, "forget_for_erase"),
    ("exact_words.py", "erase_turn", {"delete"}, "_shadow_first"),
    ("exact_words.py", "erase_text", {"delete"}, "_shadow_first"),
    ("exact_words.py", "forget_transcript", {"_transcript_candidates", "_set_transcript_metadata"}, "_shadow_first"),
])
def test_the_row_choke_reaches_the_shadow_before_deleting_and_the_word_chokes_too(path, name, deletes, call):
    source = (SVC / path).read_text("utf-8")
    assert _shadow_before_delete(source, name, deletes)
    for replacement in ("None", repr("await os_shadow.forget_for_erase(user_id); await _shadow_first(user_id)"),
                        "(await os_shadow.forget_for_erase(user_id) if not reach_shadow else None)"):
        mutated = _replace_await(source, name, call, replacement)
        mutated += f"\n# await {call}(user_id): removed executable call\n"
        # The false-guard mutation contains an Await inside an IfExp: it must not count.
        assert not _shadow_before_delete(mutated, name, deletes), (name, replacement)
    if call == "_shadow_first":
        for replacement in ("None", repr("await os_shadow.forget_for_erase(user_id)")):
            assert not _shadow_before_delete(_replace_await(source, "_shadow_first", "forget_for_erase", replacement), name, deletes)


def _no_shadow_sites(source_by_path: dict) -> set:
    found = set()
    for path, src in source_by_path.items():
        for fn in [n for n in ast.walk(ast.parse(src)) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            for c in ast.walk(fn):
                if (isinstance(c, ast.Call) and _called(c) == "erase_rows"
                        and any(k.arg == "reach_shadow" and isinstance(k.value, ast.Constant) and k.value.value is False for k in c.keywords)):
                    found.add(f"{path}::{fn.name}")
    return found


def _allowed(site):
    return site in NO_SHADOW_OK


def test_only_the_named_system_undos_skip_the_shadow_and_a_rogue_skip_is_caught():
    sources = {p.relative_to(SVC).as_posix(): p.read_text("utf-8") for p in SVC.rglob("*.py")
               if "tests" not in p.relative_to(SVC).parts}
    sites = _no_shadow_sites(sources)
    assert sites == set(NO_SHADOW_OK) and all(_allowed(s) for s in sites), sorted(s for s in sites if not _allowed(s))
    for site in NO_SHADOW_OK:
        path, name = site.split("::")
        mutated = dict(sources)
        mutated[path] = _replace_await(sources[path], name, "erase_rows",
                                      repr("svc.erase_rows(u, ids, reach_shadow=False)"))
        assert _no_shadow_sites(mutated) == sites - {site}     # real call removed, original comments kept
    rogue = {"rogue.py": "async def wipe(svc, u, ids):\n    await svc.erase_rows(u, ids, actor=u, reach_shadow=False)\n"}
    assert not all(_allowed(s) for s in _no_shadow_sites(rogue))            # negative control: a new skip is flagged
    for decoy in ("# await svc.erase_rows(u, ids, reach_shadow=False)\n    pass", repr("svc.erase_rows(u, ids, reach_shadow=False)")):
        assert _no_shadow_sites({"rogue.py": "async def wipe():\n    " + decoy + "\n"}) == set()
    renamed = {"memory_digest.py": sources["memory_digest.py"].replace("async def _synthesis_pass(", "async def rogue(")}
    assert not all(_allowed(s) for s in _no_shadow_sites(renamed))
    calls, _ = _fn_calls("memory_space.py", "forget_row")
    assert "erase_rows" in {_called(c) for c in calls}
    rogue_entry = "async def forget_everything(u):\n    import exact_words\n    await exact_words.get_backend().delete(u, ['t1'])\n"
    called = {_called(c) for c in ast.walk(ast.parse(rogue_entry)) if isinstance(c, ast.Call)}
    assert not called & CHOKES                                                # negative control: a rogue eraser reaches no choke


@pytest.mark.asyncio
async def test_a_row_forget_with_shadow_records_and_its_kernel_stopped_is_not_confirmed(kernel, env, monkeypatch):  # noqa: F811
    import memory_service

    svc, _col = env
    _stop_kernel_with_records(kernel, monkeypatch)
    with pytest.raises(memory_service.MemoryServiceError, match="Zoe OS shadow erasure failed"):
        await svc.erase_rows(MEMBER, ["r1"], actor=MEMBER)
    assert _pending(kernel) == [MEMBER]
    await svc.erase_rows(MEMBER, ["r1"], actor="night_undo:x", lock_held=False, reach_shadow=False)   # a system undo: not blocked


@pytest.mark.asyncio
async def test_a_words_forget_with_shadow_records_and_its_kernel_stopped_raises(kernel, monkeypatch):
    import exact_words

    exact_words.set_backend(exact_words.MemoryBackend())
    _stop_kernel_with_records(kernel, monkeypatch)
    with pytest.raises(os_shadow.ShadowForgetUnavailable):
        await exact_words.erase_text(MEMBER, "the book club meets on tuesday")
    with pytest.raises(os_shadow.ShadowForgetUnavailable):
        await exact_words.erase_turn(MEMBER, "t1")


@pytest.mark.asyncio
async def test_with_no_shadow_state_a_row_and_a_words_forget_touch_nothing(env, tmp_path):  # noqa: F811
    import exact_words

    svc, _col = env
    exact_words.set_backend(exact_words.MemoryBackend())
    assert (await svc.erase_rows(MEMBER, ["r1"], actor=MEMBER))["rows_removed"] == 0
    assert await exact_words.erase_text(MEMBER, "the book club meets on tuesday") == 0
    assert not (tmp_path / "os-shadow").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("fails", [False, True], ids=["confirmed", "refused"])
async def test_row_erase_waits_for_shadow_and_failure_preserves_rows(env, monkeypatch, fails):  # noqa: F811
    import memory_service
    import memory_value_registry

    svc, col = env
    col.rows["r1"] = ("a synthetic garden fact", {"user_id": MEMBER})
    order = []

    async def forget(user_id):
        assert user_id == MEMBER
        order.append("shadow")
        if fails:
            raise os_shadow.ShadowForgetUnavailable("refused")

    async def registry(*args, **kwargs):
        pass

    real_delete = svc._delete_ids

    def delete(ids):
        order.append("delete")
        return real_delete(ids)

    monkeypatch.setattr(os_shadow, "forget_for_erase", forget)
    monkeypatch.setattr(memory_value_registry, "erase_for_rows", registry)
    monkeypatch.setattr(svc, "_delete_ids", delete)
    if fails:
        with pytest.raises(memory_service.MemoryServiceError, match="shadow erasure failed"):
            await svc.erase_rows(MEMBER, ["r1"], actor=MEMBER)
        assert order == ["shadow"] and "r1" in col.rows
    else:
        assert (await svc.erase_rows(MEMBER, ["r1"], actor=MEMBER))["rows_removed"] == 1
        assert order == ["shadow", "delete"] and "r1" not in col.rows


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["erase_turn", "erase_text", "forget_transcript"])
@pytest.mark.parametrize("fails", [False, True], ids=["confirmed", "refused"])
async def test_word_erasers_wait_for_shadow_and_failure_preserves_words(monkeypatch, name, fails):
    import exact_words

    order = []
    text = "the synthetic garden grows lavender"
    backend = exact_words.MemoryBackend()
    await backend.add(MEMBER, "t1", 1, text, " lavender ", "test")
    real_delete = backend.delete
    metadata = {"t1": "{}"}

    async def forget(user_id):
        assert user_id == MEMBER
        order.append("shadow")
        if fails:
            raise os_shadow.ShadowForgetUnavailable("refused")

    async def delete(user_id, ids):
        order.append("delete")
        return await real_delete(user_id, ids)

    async def candidates(user_id, needle):
        assert user_id == MEMBER
        order.append("candidates")
        return [("t1", text, metadata["t1"])]

    async def update(row_id, value):
        order.append("delete")
        metadata[row_id] = value

    monkeypatch.setattr(os_shadow, "forget_for_erase", forget)
    monkeypatch.setattr(backend, "delete", delete)
    if name == "forget_transcript":
        monkeypatch.setattr(exact_words, "get_backend", lambda: exact_words.SqlBackend())
        monkeypatch.setattr(exact_words, "_transcript_candidates", candidates)
        monkeypatch.setattr(exact_words, "_set_transcript_metadata", update)
    else:
        monkeypatch.setattr(exact_words, "get_backend", lambda: backend)
    call = getattr(exact_words, name)
    if fails:
        with pytest.raises(os_shadow.ShadowForgetUnavailable, match="refused"):
            await call(MEMBER, "t1" if name == "erase_turn" else text)
        assert order == ["shadow"]
        assert (MEMBER, "t1") in backend.rows and metadata == {"t1": "{}"}
    else:
        assert await call(MEMBER, "t1" if name == "erase_turn" else text) == 1
        assert order == (["shadow", "candidates", "delete"] if name == "forget_transcript" else ["shadow", "delete"])
        if name == "forget_transcript":
            assert json.loads(metadata["t1"])["off_record"] is True
        else:
            assert (MEMBER, "t1") not in backend.rows


@pytest.mark.parametrize("statement", [
    "os_shadow.forget_for_erase(user_id)",
    "if not reach_shadow:\n        await os_shadow.forget_for_erase(user_id)",
    "if reach_shadow is False:\n        await os_shadow.forget_for_erase(user_id)",
    "async def unused():\n        await os_shadow.forget_for_erase(user_id)",
    repr("await os_shadow.forget_for_erase(user_id)"),
])
def test_shadow_order_checker_rejects_unawaited_false_only_nested_and_string_decoys(statement):
    source = ("async def erase_rows(user_id, reach_shadow=True):\n"
              "    # os_shadow.forget_for_erase must precede the delete\n"
              f"    {statement}\n    self._delete_ids([])\n")
    assert not _shadow_before_delete(source, "erase_rows", {"_delete_ids"})


def test_shadow_order_checker_accepts_imported_alias_but_rejects_late_calls():
    head = "from os_shadow import forget_for_erase as forget\nasync def erase_rows(user_id):\n"
    assert _shadow_before_delete(head + "    await forget(user_id)\n    self._delete_ids([])\n", "erase_rows", {"_delete_ids"})
    assert not _shadow_before_delete(head + "    self._delete_ids([])\n    await forget(user_id)\n", "erase_rows", {"_delete_ids"})


# ---- the CALLERS of the shared erasers: an unconfirmed shadow forget is never reported as forgotten, and the repeat finishes ----------
# Behaviour only: each test RUNS the caller over a small in-memory store whose ``erase_rows`` does what the real one does first
# (``os_shadow.forget_for_erase``, wrapped in the service's own error), against the fake kernel.
CALLERS = ("last", "explained", "topic", "screen", "guardian", "unwrite", "curation")
#: the callers that confirm the shadow themselves BEFORE their first change (the others' first change is the eraser's own check)
PREFLIGHT = {"topic", "screen", "guardian", "unwrite", "curation"}
WORDS = {"last", "explained", "topic", "screen", "guardian"}          # the callers that also erase the member's verbatim words
SAID = "The synthetic garden grows lavender"


class _StoreError(Exception):
    """Stands for ``MemoryServiceError``: what the row eraser raises, FROM the shadow's own error when that is the cause."""


@pytest.fixture
def callers(kernel, monkeypatch):
    from types import SimpleNamespace

    import ask_to_remember
    import conversation_notes
    import exact_words
    import forget_topic
    import intent_router as ir
    import memory_curation as mc
    import memory_forget_cascade
    import memory_provenance as mp
    import memory_service
    import memory_space
    import memory_value_registry
    import night_mind
    import omi_review_apply
    import provenance_answers as pa
    import turn_forget

    async def noop(*args, **kwargs):
        return 0

    async def value(v):
        return v

    class Store:
        def __init__(self):
            self.rows = {"r1": SimpleNamespace(id="r1", text=SAID, metadata={"user_id": MEMBER, "status": "approved", "source_excerpt": SAID})}
            self.reviews: list = []
            self.before_erase = None        # called as the eraser is entered (the late case: the kernel stops HERE)
            self.after_erase = None         # called once the rows are gone (the kernel stops before the WORDS step)
            self.ordinary_failure = False   # the eraser fails for a reason that is not the shadow

        async def get(self, rid):
            return self.rows.get(rid)

        async def list_by_status(self, *, user_id, status, limit=1000, offset=0):
            return [r for r in self.rows.values() if r.metadata["status"] == status and r.metadata["user_id"] == user_id][offset:offset + limit]

        async def review(self, rid, *, decision, **kwargs):
            self.reviews.append(rid)
            self.rows[rid].metadata.update(status="rejected" if decision == "reject" else "archived", review_note=kwargs.get("note"))
            return self.rows[rid]

        async def erase_rows(self, uid, ids, **kwargs):
            if self.before_erase:
                self.before_erase()
            try:
                await os_shadow.forget_for_erase(uid)
            except Exception as exc:
                await os_shadow.park_unconfirmed(uid, rows=list(ids))     # exactly the real eraser's shadow step (pinned below)
                raise _StoreError("erase_rows failed: Zoe OS shadow erasure failed") from exc
            if self.ordinary_failure:
                raise _StoreError("erase_rows failed: disk")
            n = sum(self.rows.pop(rid, None) is not None for rid in ids)
            if self.after_erase:
                self.after_erase()
            return {"rows_removed": n}

    svc, backend = Store(), exact_words.MemoryBackend()
    exact_words.set_backend(backend)
    turn_forget.reset()
    mp.clear_explained(MEMBER)
    real_retry = memory_forget_cascade.retry_pending
    monkeypatch.setattr(mp, "enabled", lambda: True)
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: svc)
    monkeypatch.setattr(memory_service, "physical_erase_enabled", lambda: True)
    monkeypatch.setattr(memory_service, "is_guest_memory_user", lambda uid: False)
    monkeypatch.setattr(memory_forget_cascade, "RETRY_DELAY_S", 0)
    monkeypatch.setattr(ask_to_remember, "siblings_of", lambda *a, **k: value([]))
    for mod, name in ((conversation_notes, "erase_for_text"), (conversation_notes, "erase_topic"), (memory_value_registry, "erase_for_turn"),
                      (memory_value_registry, "erase_topic"), (night_mind, "erase_words"), (pa, "_speculation_barrier"),
                      (memory_space, "curated_locked"), (forget_topic, "_withdraw_offers"), (memory_forget_cascade, "retry_pending"),
                      (memory_forget_cascade, "_mark_pending"), (mc, "_audited"), (mc, "_finished")):
        monkeypatch.setattr(mod, name, noop)
    monkeypatch.setattr(mc, "policy", lambda *a: value(SimpleNamespace(member=SimpleNamespace(user_id=MEMBER))))
    monkeypatch.setattr(mc, "_one", lambda *a: value(svc.rows["r1"]))
    monkeypatch.setattr(mc, "_earlier_revisions", lambda *a: value([]))

    async def run(caller):
        """One forget through ``caller``: ``(forgotten?, what it returned)``. A raise is "not forgotten"."""
        try:
            if caller == "last":
                out = await ir._execute_forget_last(MEMBER)
                return out.startswith("Done"), out
            if caller == "explained":
                exp = getattr(mp._state(MEMBER, create=False), "explained", None)      # what mark_explained / _rearm left
                assert exp is not None and exp.row_id == "r1", "the 'forget it' target did not survive"
                out = await pa.forget_it(MEMBER, exp, svc=svc)
                return out.startswith("Done"), out
            if caller == "topic":
                res = await forget_topic.forget(svc, MEMBER, "synthetic garden")
                return forget_topic.reply(res, "synthetic garden").startswith("Done"), res
            if caller == "screen":
                return await memory_space.forget_row(MEMBER, "r1", svc=svc) == "forgotten", None
            if caller == "guardian":
                return await memory_space.guardian_forget(MEMBER, "r1", actor="adult", svc=svc) == "forgotten", None
            if caller == "unwrite":
                return await omi_review_apply.unwrite(MEMBER, [{"kind": "person_fact", "written_ref": "memory:r1"}]) == 1, None
            await mc._forget_locked(None, SimpleNamespace(user_id="adult"), MEMBER, "r1", svc=svc)
            return True, None
        except (os_shadow.ShadowForgetUnavailable, _StoreError) as exc:
            return False, exc

    async def said():
        """The member said it: an indexed verbatim turn, the noted previous turn, and the row an answer just named."""
        await backend.add(MEMBER, "t1", 1, SAID, " lavender ", "test")
        turn_forget.note_user_turn(MEMBER, SAID)
        mp.mark_explained(MEMBER, "r1", text=SAID, quote=SAID, turn_id="t1")

    yield SimpleNamespace(svc=svc, backend=backend, run=run, said=said, retry_pending=real_retry)
    exact_words.set_backend(None)
    turn_forget.reset()
    mp.clear_explained(MEMBER)


@pytest.mark.asyncio
@pytest.mark.parametrize("flag", ["shadow", "off"])
@pytest.mark.parametrize("when", ["from-the-start", "late"])
@pytest.mark.parametrize("caller", CALLERS)
async def test_a_caller_never_says_forgotten_while_the_kernel_cannot_confirm_and_the_repeat_finishes(kernel, callers, monkeypatch, caller, when, flag):
    """The kernel holds a record and cannot confirm - from the start, or (``late``) only once the row eraser is entered, AFTER the
    caller's own first confirmation succeeded. Flag on or off: the caller does not report the forget, the row is still in the store,
    the forget is parked, the target survives; and once the kernel answers, the SAME request again erases the row and the words."""
    import turn_forget

    monkeypatch.setenv("ZOE_OS_LIST_SHADOW", flag)
    monkeypatch.setattr(os_shadow, "_kick_erases", lambda: None)      # here the member's REPEAT finishes it; the replay has its own test
    st = callers
    await st.said()
    entered = []

    def stop_kernel():
        entered.append(len(forgets(kernel)))
        kernel.forget_mode = "refuse_all"

    if when == "late":
        st.svc.before_erase = stop_kernel
    else:
        kernel.forget_mode = "refuse_all"
    forgotten, out = await st.run(caller)
    assert not forgotten, out
    assert "r1" in st.svc.rows and st.svc.rows["r1"].text == SAID          # the row was NOT erased
    assert _pending(kernel) == [MEMBER]                                    # the shadow forget is parked, not lost
    if when == "late":
        assert len(entered) == 1                                           # the scenario really happened: the eraser was reached ...
        assert entered[0] >= (1 if caller in PREFLIGHT else 0)             # ... after the caller's own confirmation, where it has one
    elif caller in PREFLIGHT:
        assert st.svc.reviews == [] and st.svc.rows["r1"].metadata["status"] == "approved"      # nothing at all was changed
    if not (caller == "topic" and when == "late"):       # (a topic forget erases the words before the rows, after its confirmation)
        assert (MEMBER, "t1") in st.backend.rows                           # the words were NOT erased
    if caller == "last":
        assert out == __import__("intent_router").FORGET_STORE_DOWN_REPLY
        assert turn_forget.previous_turn(MEMBER).text == SAID              # "forget that" again still means this turn
    if caller == "topic":
        assert out.store_down and out.forgotten == 0
    # the kernel is back: the same request again finishes the forget (run() itself asserts the "forget it" target survived)
    kernel.forget_mode, st.svc.before_erase = "ok", None
    forgotten, out = await st.run(caller)
    assert forgotten, out
    assert st.svc.rows == {}
    if caller in WORDS:
        assert (MEMBER, "t1") not in st.backend.rows
    assert turn_forget.unerased(MEMBER) == []
    await os_shadow._finish_erases()            # an id parked for a row the repeat has since erased is dropped by the next replay
    assert not (kernel.state / "pending-forgets.json").exists() and not (kernel.state / "pending-erases.json").exists()


#: with no shadow state: in-loop stats of the state directory per forget = its shadow checks (the four entry points and the delete's
#: drop for "direct"; the erasers' own checks, plus the caller's confirmation where it has one, for the callers)
STATS = {"direct": 5, "last": 2, "explained": 3, "topic": 2, "screen": 3, "guardian": 3, "unwrite": 2, "curation": 2}


def _spy_on_the_loop(monkeypatch):
    """Record every executor hop and every look at the disk made from the calling (event-loop) thread: ``asyncio.to_thread``, the
    loop's ``run_in_executor``, the shadow's own probe, and pathlib's stat / exists / is_dir / iterdir."""
    import asyncio
    import threading

    seen, me = [], threading.get_ident()

    def spy(owner, name):
        real = getattr(owner, name)

        def wrapped(*args, **kwargs):
            if threading.get_ident() == me:
                seen.append(name)
            return real(*args, **kwargs)

        monkeypatch.setattr(owner, name, wrapped)

    spy(asyncio, "to_thread")
    spy(type(asyncio.get_running_loop()), "run_in_executor")
    spy(os_shadow, "shadow_state_exists")
    if hasattr(os_shadow, "_state_dir_present"):
        spy(os_shadow, "_state_dir_present")
    for name in ("stat", "exists", "is_dir", "is_file", "iterdir"):
        spy(Path, name)
    return seen


@pytest.mark.asyncio
@pytest.mark.parametrize("caller", ["direct", *CALLERS])
async def test_with_no_shadow_state_a_forget_makes_no_executor_hop_and_no_disk_probe_on_the_turn(kernel, callers, monkeypatch, tmp_path, caller):
    """Flag off, no kernel ever installed, the answer established at start-up (``_state_probe``, what the import does): a forget
    through every caller - and the four shadow entry points directly - hops to no executor and looks at no file from the loop, and
    forgets as it always did. The spy's own control is the test below (a forget that is NOT certain is seen probing)."""
    monkeypatch.setenv("ZOE_OS_LIST_SHADOW", "off")
    monkeypatch.setenv("ZOE_OS_SHADOW_DIR", str(tmp_path / "absent"))
    getattr(os_shadow, "_state_probe", lambda: None)()               # start-up, off the turn
    st = callers
    await st.said()
    seen = _spy_on_the_loop(monkeypatch)
    if caller == "direct":
        assert [await os_shadow.forget_for_erase(MEMBER), await os_shadow.forget_user(MEMBER), await os_shadow.erase_entity(MEMBER, "Dana"),
                await os_shadow.erase_topic(MEMBER, "the garden"), await os_shadow.drop_parked_erases(MEMBER),
                await os_shadow.nudge(), await st.retry_pending(MEMBER)] == [0, 0, 0, 0, None, None, None]
    else:
        forgotten, out = await st.run(caller)
        assert forgotten, out
        assert st.svc.rows == {} and (caller not in WORDS or (MEMBER, "t1") not in st.backend.rows)
    # no hop and no probe: the ONLY look at the disk is the forget's own single stat of the state directory, once per shadow check
    # (a forget never confirms from the cached answer alone). A spoken "forget that" makes two checks (the row eraser, the word
    # eraser), "forget it" three.
    assert seen == ["_state_dir_present"] * STATS[caller]
    assert kernel.calls == [] and not (tmp_path / "absent").exists()


@pytest.mark.asyncio
async def test_shadow_state_that_appears_later_is_noticed_and_an_unsure_answer_always_probes(kernel, monkeypatch, tmp_path):
    """Staleness, every way state can appear after start-up, each failing toward CHECKING: (1) nothing established -> the forget
    probes, in a thread; (2) another process writes state -> the poll sees it and the next forget goes to the kernel; (3) this
    process writes state (the roster) -> the very next forget does, with no wait; (4) the poller has stopped -> the cached answer
    expires and the forget probes again."""
    import asyncio
    import threading

    monkeypatch.setenv("ZOE_OS_LIST_SHADOW", "off")
    monkeypatch.setattr(os_shadow, "STATE_POLL_S", 0.05)
    where = []
    real = os_shadow.shadow_state_exists
    monkeypatch.setattr(os_shadow, "shadow_state_exists", lambda: where.append(threading.get_ident()) or real())
    loop_thread = threading.get_ident()

    def fresh(name):
        d = tmp_path / name
        monkeypatch.setenv("ZOE_OS_SHADOW_DIR", str(d))
        return d

    # (1) nothing established for this directory: the forget itself probes, off the loop, and is then answered from memory
    d = fresh("one")
    assert await os_shadow.forget_for_erase(MEMBER) == 0 and where and loop_thread not in where
    # (2) ANOTHER process installs a kernel (a file this process did not write): noticed by the poll, then never skipped
    d.mkdir()
    (d / "creds.json").write_text("{}")
    for _ in range(100):
        if not os_shadow._no_state():
            break
        await asyncio.sleep(0.02)
    assert not os_shadow._no_state()
    with pytest.raises(os_shadow.ShadowForgetUnavailable):
        await os_shadow.forget_for_erase(MEMBER)                       # state, and no kernel answers: fails closed
    # (2b) ... and in the window BEFORE any poll: the cache still says "no state", yet no forget entry confirms from it
    d = fresh("window")
    assert not d.exists()
    assert await os_shadow.forget_for_erase(MEMBER) == 0
    assert not d.exists()                                               # a no-state forget creates nothing
    monkeypatch.setattr(os_shadow, "STATE_POLL_S", 30.0)               # no poll will come within this test
    os_shadow._state_known = (str(d), False, time.monotonic())
    d.mkdir()
    (d / "kernel").mkdir()                                              # shadow storage restored by another process
    assert os_shadow._no_state()                                        # the cache has NOT noticed
    for forget in (lambda: os_shadow.forget_user(MEMBER), lambda: os_shadow.erase_entity(MEMBER, "Dana"),
                   lambda: os_shadow.erase_topic(MEMBER, "the garden"), lambda: os_shadow.forget_for_erase(MEMBER)):
        os_shadow._state_known = (str(d), False, time.monotonic())
        with pytest.raises(os_shadow.ShadowForgetUnavailable):
            await forget()
    assert json.loads((d / "pending-forgets.json").read_text()) == [MEMBER]
    monkeypatch.setattr(os_shadow, "STATE_POLL_S", 0.05)
    # (3) THIS process writes state: the next forget does not skip, without waiting for any poll
    d = fresh("three")
    assert await os_shadow.forget_for_erase(MEMBER) == 0 and os_shadow._no_state()
    monkeypatch.setattr(os_shadow, "STATE_POLL_S", 30.0)               # no poll can help within this test
    os_shadow._state_known = (str(d), False, time.monotonic())
    os_shadow.write_roster([MEMBER])
    assert not os_shadow._no_state()
    with pytest.raises(os_shadow.ShadowForgetUnavailable):
        await os_shadow.forget_for_erase(MEMBER)
    # (4) the poller is gone: the answer is not trusted past three poll intervals, and the forget probes the disk again
    monkeypatch.setattr(os_shadow, "STATE_POLL_S", 0.05)
    d = fresh("four")
    assert await os_shadow.forget_for_erase(MEMBER) == 0 and os_shadow._no_state()
    os_shadow._state_stop.set()
    await asyncio.sleep(0.4)
    assert not os_shadow._no_state()
    del where[:]
    assert await os_shadow.forget_for_erase(MEMBER) == 0 and where and loop_thread not in where


@pytest.mark.asyncio
@pytest.mark.parametrize("caller", ["last", "explained", "topic"])
async def test_an_ordinary_erase_failure_is_still_only_logged_by_the_three_spoken_forgets(kernel, callers, caller):
    """The control for ``os_shadow.unconfirmed``: a row erase that fails for a reason that is NOT the shadow keeps its old outcome
    (the rows stay hidden, the forget is reported) and leaves nothing remembered for a repeat."""
    import turn_forget

    st = callers
    await st.said()
    st.svc.ordinary_failure = True
    forgotten, out = await st.run(caller)
    assert forgotten, out
    assert st.svc.rows["r1"].metadata["status"] in ("rejected", "archived") and turn_forget.unerased(MEMBER) == []
    assert (MEMBER, "t1") not in st.backend.rows


def test_unconfirmed_reads_the_cause_chain_and_nothing_else():
    shadow = os_shadow.ShadowForgetUnavailable("refused (parked)")
    wrapped = _StoreError("erase_rows failed")
    wrapped.__cause__ = shadow
    twice = RuntimeError("outer")
    twice.__context__ = wrapped
    assert os_shadow.unconfirmed(shadow) and os_shadow.unconfirmed(wrapped) and os_shadow.unconfirmed(twice)
    assert not os_shadow.unconfirmed(_StoreError("disk")) and not os_shadow.unconfirmed(None)
    loop = RuntimeError("a")
    loop.__cause__ = loop
    assert not os_shadow.unconfirmed(loop)                                 # bounded: a cycle ends


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["kernel-refusing", "no-shadow-state"])
async def test_a_member_delete_confirms_the_shadow_after_the_watermark_and_before_the_first_store(kernel, env, monkeypatch, tmp_path, state):  # noqa: F811
    """RUNS ``MemoryService.delete_user`` and stops it at its first eraser (the day review): with the kernel refusing, that eraser is
    never reached though the watermark was written; the control (no shadow state) reaches it."""
    import memory_service
    import omi_day_review

    svc, _col = env
    order = []

    async def watermark(*args, **kwargs):
        order.append("watermark")

    async def first_store(*args, **kwargs):
        order.append("first-eraser")
        raise OSError("stop here")

    monkeypatch.setattr(memory_service._erase_watermark, "record", watermark)
    monkeypatch.setattr(omi_day_review, "forget_user", first_store)
    if state == "kernel-refusing":
        kernel.forget_mode = "refuse_all"
    else:
        monkeypatch.setenv("ZOE_OS_LIST_SHADOW", "off")
        monkeypatch.setenv("ZOE_OS_SHADOW_DIR", str(tmp_path / "absent"))
    with pytest.raises(memory_service.MemoryServiceError) as err:
        await svc.delete_user(MEMBER, actor=MEMBER)
    if state == "kernel-refusing":
        assert order == ["watermark"] and "Zoe OS shadow" in str(err.value) and _pending(kernel) == [MEMBER]
    else:
        assert order == ["watermark", "first-eraser"] and kernel.calls == []


class _EntityStore:
    def __init__(self, fail):
        from types import SimpleNamespace

        self.rows = {"r1": SimpleNamespace(id="r1", text="Dana likes lavender", metadata={"user_id": MEMBER, "status": "approved"})}
        self.fail = fail

    async def search(self, query, **kwargs):
        return []

    async def list_by_status(self, *, user_id, status, limit=1000, offset=0):
        return [r for r in self.rows.values() if r.metadata["status"] == status][offset:offset + limit]

    async def review(self, rid, *, decision, **kwargs):
        self.rows[rid].metadata["status"] = "archived"
        return self.rows[rid]

    async def get(self, rid):
        return self.rows.get(rid)

    async def erase_rows(self, uid, ids, **kwargs):
        if self.fail == "shadow":
            self.kernel.forget_mode = "refuse_all"          # the kernel stops HERE, after the entity forget's own confirmation
        try:
            await os_shadow.forget_for_erase(uid)
        except Exception as exc:
            await os_shadow.park_unconfirmed(uid, rows=list(ids))
            raise _StoreError("erase_rows failed: Zoe OS shadow erasure failed") from exc
        if self.fail == "ordinary":
            raise _StoreError("erase_rows failed: disk")
        return {"rows_removed": sum(self.rows.pop(rid, None) is not None for rid in ids)}


@pytest.fixture
def entity(kernel, monkeypatch):
    import exact_words
    import intent_router as ir
    import memory_forget_cascade
    import memory_forgotten
    import memory_service

    cascades, words, marked = [], [], []

    async def mark(user_id, labels, **kwargs):
        marked.append(sorted(labels))
        return True

    async def cascade(user_id, name, **kwargs):
        cascades.append(name)
        return memory_forget_cascade.CascadeResult()

    async def erase_words(user_id, name):
        words.append(name)
        return 0

    async def noop(*args, **kwargs):
        return 0

    monkeypatch.setattr(memory_service, "is_guest_memory_user", lambda uid: False)
    monkeypatch.setattr(memory_service, "physical_erase_enabled", lambda: True)
    monkeypatch.setattr(memory_forgotten, "add", noop)
    monkeypatch.setattr(memory_forget_cascade, "cascade_forget", cascade)
    monkeypatch.setattr(memory_forget_cascade, "_mark_pending", mark)
    monkeypatch.setattr(exact_words, "erase_entity", erase_words)
    monkeypatch.setattr(__import__("night_mind"), "erase_words", noop)

    async def forget(store):
        monkeypatch.setattr(memory_service, "get_memory_service", lambda: store)
        return await ir.execute_intent(ir.Intent("memory_forget_entity", {"name": "Dana"}), MEMBER)

    forget.cascades, forget.words, forget.incomplete, forget.marked = cascades, words, ir._FORGET_CASCADE_INCOMPLETE, marked
    return forget


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["kernel-refusing", "no-shadow-state"])
async def test_an_entity_forget_confirms_the_shadow_before_its_first_erase(kernel, entity, monkeypatch, tmp_path, state):
    """RUNS the spoken entity forget: with the kernel refusing, the FIRST eraser (the member's verbatim words) is never called, the row
    is untouched and the reply is "not forgotten yet"; the control (no shadow state) erases and reports as it always did."""
    store = _EntityStore(fail=None)
    if state == "kernel-refusing":
        kernel.forget_mode = "refuse_all"
    else:
        monkeypatch.setenv("ZOE_OS_LIST_SHADOW", "off")
        monkeypatch.setenv("ZOE_OS_SHADOW_DIR", str(tmp_path / "absent"))
    reply = await entity(store)
    if state == "kernel-refusing":
        assert reply == entity.incomplete and entity.words == [] and entity.cascades == [] and len(entity.marked) == 1
        assert store.rows["r1"].metadata["status"] == "approved" and _pending(kernel) == [MEMBER]
    else:
        assert reply != entity.incomplete and "forgot" in reply.lower()
        assert entity.words == ["Dana"] and entity.cascades == ["Dana"] and store.rows == {} and kernel.calls == []


@pytest.mark.asyncio
async def test_an_entity_forget_whose_row_erase_the_shadow_did_not_confirm_is_parked_and_finished_by_the_replay(kernel, entity):
    """The late case: the entry confirmation succeeded, then the row eraser's own shadow step failed. Not reported, the cascade does
    not run - and two durable records finish it with no repeat: the archived row is parked (finished by the shadow's replay after a
    restart) and every cascade step is left pending (the retry pass). The control: an ORDINARY erase failure keeps its old outcome."""
    import memory_forget_cascade as cascade

    store = _EntityStore(fail="shadow")
    store.kernel = kernel
    assert await entity(store) == entity.incomplete
    assert entity.cascades == [] and store.rows["r1"].metadata["status"] == "archived"
    assert forgets(kernel)[0][1].get("entity") == "Dana"
    assert _parked_rows(kernel) == {MEMBER: {"rows": ["r1"]}}
    assert entity.marked == [sorted(l for l in cascade.ALL_LABELS if l != cascade.VALUE_REGISTRY_TOPIC)]
    _restart(kernel)
    kernel.forget_mode, store.fail = "ok", None
    add()                                                                           # any job: the member says nothing more
    assert await _until(lambda: not (kernel.state / "pending-erases.json").exists()) and store.rows == {}
    ordinary = _EntityStore(fail="ordinary")
    reply = await entity(ordinary)
    assert reply != entity.incomplete and "forgot" in reply.lower() and entity.cascades == ["Dana"]
    assert ordinary.rows["r1"].metadata["status"] == "archived" and len(entity.marked) == 1


@pytest.fixture
def cascade_env(kernel, monkeypatch):
    from types import SimpleNamespace

    import correction_marker
    import memory_forget_cascade as cascade
    import memory_forgotten

    seen = SimpleNamespace(db=[], marked=[], cleared=[], markers=[], pending=[])

    async def run(db, user_id, rx, res, **kwargs):
        seen.db.append(kwargs.get("only"))

    async def mark(user_id, labels, **kwargs):
        seen.marked.append(sorted(labels))
        return True

    async def clear(user_id, labels):
        seen.cleared.append(sorted(labels))
        return len(list(labels))

    async def pending(user_id, labels):
        return list(seen.pending)

    async def pattern(user_id):
        return cascade._name_re("Dana")

    async def noop(*args, **kwargs):
        return 0

    monkeypatch.setattr(cascade, "RETRY_DELAY_S", 0)
    monkeypatch.setattr(cascade, "_run", run)
    monkeypatch.setattr(cascade, "_store_missing", lambda exc: False)
    monkeypatch.setattr(memory_forgotten, "mark_pending", mark)
    monkeypatch.setattr(memory_forgotten, "clear_pending", clear)
    monkeypatch.setattr(memory_forgotten, "pending_labels", pending)
    monkeypatch.setattr(memory_forgotten, "ledger_pattern", pattern)
    monkeypatch.setattr(correction_marker, "forget_user", lambda *a, **k: seen.markers.append(1))
    for name in ("restraint", "commitments", "omi_recordings", "omi_day_review", "conversation_notes", "self_review", "night_extract"):
        mod = __import__(name)
        for fn in ("erase_entity", "erase_matching", "forget_people"):
            if hasattr(mod, fn):
                monkeypatch.setattr(mod, fn, noop)
    monkeypatch.setattr(__import__("local_text_logs"), "erase_entity", lambda rx: 0)
    return seen


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["kernel-refusing", "no-shadow-state"])
async def test_the_cascade_confirms_the_shadow_before_any_store_and_leaves_every_step_pending(kernel, cascade_env, monkeypatch, tmp_path, state):
    """RUNS ``cascade_forget``: with the kernel refusing, no store is touched (not the database, not the pending corrections), the
    result is incomplete and EVERY step is marked pending; the control (no shadow state) runs the whole cascade."""
    import memory_forget_cascade as cascade

    seen = cascade_env
    if state == "kernel-refusing":
        kernel.forget_mode = "refuse_all"
    else:
        monkeypatch.setenv("ZOE_OS_LIST_SHADOW", "off")
        monkeypatch.setenv("ZOE_OS_SHADOW_DIR", str(tmp_path / "absent"))
    res = await cascade.cascade_forget(MEMBER, "Dana", db=object())
    if state == "kernel-refusing":
        assert res.failed == ["os_shadow"] and not res.complete
        assert seen.db == [] and seen.markers == []
        assert seen.marked == [sorted(l for l in cascade.ALL_LABELS if l != cascade.VALUE_REGISTRY_TOPIC)]
    else:
        assert res.complete and seen.db == [None] and len(seen.markers) == 2 and seen.marked == [] and kernel.calls == []


@pytest.mark.asyncio
async def test_a_retry_pass_whose_shadow_step_fails_runs_and_clears_nothing_and_a_healthy_one_finishes(kernel, cascade_env):
    """RUNS ``retry_pending`` with the database AND the shadow pending: while the kernel refuses, the database step is not run and
    no marker is cleared (it was never attempted); once the kernel answers, both are done and both markers go."""
    import memory_forget_cascade as cascade

    seen = cascade_env
    seen.pending = ["database", "os_shadow"]
    kernel.forget_mode = "refuse_all"
    res = await cascade.retry_pending(MEMBER, db=object())
    assert res.failed == ["os_shadow"] and seen.db == [] and seen.cleared == [] and seen.marked == [["os_shadow"]]
    kernel.forget_mode = "ok"
    res = await cascade.retry_pending(MEMBER, db=object())
    assert res.failed == [] and seen.db == [None] and seen.cleared == [["database", "os_shadow"]]
    # a retry that does not carry the shadow label never calls the kernel (it was confirmed by the pass that left the marker)
    seen.pending, n = ["database"], len(forgets(kernel))
    await cascade.retry_pending(MEMBER, db=object())
    assert len(forgets(kernel)) == n and seen.cleared[-1] == ["database"]


# ---- the parked production erase: finished by the replay, across a restart, with no further request from the member ---------------------
def _parked_rows(kernel):
    return json.loads((kernel.state / "pending-erases.json").read_text())


async def _until(cond, timeout=4.0):
    import asyncio

    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        await asyncio.sleep(0.02)
    return cond()


def _restart(kernel):
    """A service restart: the in-process memo and the turn notes are gone, a new worker reads the park files before its first job."""
    import turn_forget

    turn_forget.reset()
    os_shadow._reset_for_tests()
    assert os_shadow.revive()


@pytest.mark.asyncio
@pytest.mark.parametrize("caller", ["last", "explained", "topic", "screen", "guardian", "curation"])
async def test_a_forget_the_shadow_refused_is_finished_by_the_replay_after_a_restart_with_no_further_request(kernel, callers, caller):
    """The reviewer's control. The kernel refuses as the row eraser is entered: the row stays hidden and its id is PARKED on disk
    (ids only). Restart. The kernel recovers and the next job of any kind (here a list add) runs the replay: the kernel forget is
    confirmed and the SAME replay has the service erase the row and the member's words. The member never asks again."""
    import turn_forget

    st = callers
    await st.said()
    st.svc.before_erase = lambda: setattr(kernel, "forget_mode", "refuse_all")
    forgotten, out = await st.run(caller)
    assert not forgotten and "r1" in st.svc.rows
    assert st.svc.rows["r1"].metadata["status"] in ("rejected", "archived")         # hidden, not erased: exactly what must not be left
    assert _parked_rows(kernel) == {MEMBER: {"rows": ["r1"]}} and _pending(kernel) == [MEMBER]
    assert SAID not in (kernel.state / "pending-erases.json").read_text()          # ids only, never the words
    _restart(kernel)
    assert turn_forget.unerased(MEMBER) == [] and turn_forget.previous_turn(MEMBER) is None
    kernel.forget_mode, st.svc.before_erase = "ok", None
    add()                                                                           # any job: not a forget, not a request to finish
    assert await _until(lambda: not (kernel.state / "pending-erases.json").exists())
    assert st.svc.rows == {} and (MEMBER, "t1") not in st.backend.rows
    assert not (kernel.state / "pending-forgets.json").exists()
    assert await turn_forget.finish_parked(MEMBER, {"rows": ["r1"], "turns": ["t1"]}) == 0     # replayed twice is harmless


@pytest.mark.asyncio
async def test_a_parked_erase_whose_finish_fails_stays_parked_and_is_never_run_before_the_kernel_confirms(kernel, callers):
    st = callers
    await st.said()
    st.svc.before_erase = lambda: setattr(kernel, "forget_mode", "refuse_all")
    assert not (await st.run("last"))[0]
    _restart(kernel)
    st.svc.before_erase = None
    add()                                                                           # the kernel still refuses: nothing may be erased
    settle(1)
    assert await os_shadow._finish_erases() == 0
    assert "r1" in st.svc.rows and (MEMBER, "t1") in st.backend.rows and _parked_rows(kernel) == {MEMBER: {"rows": ["r1"]}}
    kernel.forget_mode, st.svc.ordinary_failure = "ok", True                        # the kernel confirms, the row erase itself fails
    add()
    assert await _until(lambda: not (kernel.state / "pending-forgets.json").exists())
    assert await _until(lambda: not os_shadow._finishing) and await os_shadow._finish_erases() == 0
    assert "r1" in st.svc.rows and _parked_rows(kernel)[MEMBER]["rows"] == ["r1"]   # kept, never dropped
    st.svc.ordinary_failure = False
    add()
    assert await _until(lambda: not (kernel.state / "pending-erases.json").exists()) and st.svc.rows == {}


@pytest.mark.asyncio
async def test_a_parked_erase_never_touches_an_approved_row_and_a_member_delete_drops_the_entry(kernel, callers, env, monkeypatch):  # noqa: F811
    import turn_forget

    monkeypatch.setattr(os_shadow, "_kick_erases", lambda: None)     # only the delete's own drop is under test (not the replay's finish)
    st = callers
    await st.said()
    assert await turn_forget.finish_parked(MEMBER, {"rows": ["r1", "no-such-row"]}) == 0      # r1 is approved: a parked id alone erases nothing
    assert st.svc.rows["r1"].metadata["status"] == "approved" and (MEMBER, "t1") in st.backend.rows
    os_shadow.park_erase(MEMBER, rows=["r1"], turns=["t1"])
    os_shadow.park_erase("member-b", rows=["r9"])
    svc, _col = env
    await svc.delete_user(MEMBER, actor=MEMBER)                                     # the real MemoryService.delete_user
    assert _parked_rows(kernel) == {"member-b": {"rows": ["r9"]}}


def test_an_unreadable_erase_park_is_never_overwritten(kernel):
    (kernel.state / "pending-erases.json").write_text("not json")
    with pytest.raises(os_shadow.PendingUnreadable):
        os_shadow.park_erase(MEMBER, rows=["r1"])
    assert (kernel.state / "pending-erases.json").read_text() == "not json"


@pytest.mark.asyncio
@pytest.mark.parametrize("caller", ["last", "last-no-rows", "explained", "screen", "guardian"])
async def test_a_words_erase_the_shadow_refused_is_parked_by_turn_id_and_finished_by_the_replay(kernel, callers, caller):
    """The kernel stops AFTER the rows went, before the member's words (or, ``last-no-rows``, a "forget that" of a turn that wrote no
    row at all): not reported, the words are still there, and the word eraser itself parked the index turn's ID (never the words).
    Restart, the kernel recovers, any job runs the replay: the words are gone with no further request."""
    st = callers
    await st.said()
    if caller == "last-no-rows":
        st.svc.rows.clear()
        kernel.forget_mode = "refuse_all"
    else:
        st.svc.after_erase = lambda: setattr(kernel, "forget_mode", "refuse_all")
    forgotten, out = await st.run(caller.split("-")[0])
    assert not forgotten, out
    assert st.svc.rows == {} and (MEMBER, "t1") in st.backend.rows
    assert _parked_rows(kernel) == {MEMBER: {"turns": ["t1"]}}
    assert SAID not in (kernel.state / "pending-erases.json").read_text()
    _restart(kernel)
    kernel.forget_mode, st.svc.after_erase = "ok", None
    add()
    assert await _until(lambda: not (kernel.state / "pending-erases.json").exists())
    assert (MEMBER, "t1") not in st.backend.rows and not (kernel.state / "pending-forgets.json").exists()


@pytest.mark.asyncio
async def test_a_refused_unwrite_hid_nothing_so_the_replay_erases_nothing_and_the_retry_completes(kernel, callers):
    """The day-review unwrite hard-erases approved rows with no hiding step: when the shadow refuses, the row is exactly as it was
    (its caller keeps the candidates and retries). The parked id is dropped by the replay WITHOUT erasing an approved row."""
    st = callers
    await st.said()
    st.svc.before_erase = lambda: setattr(kernel, "forget_mode", "refuse_all")
    assert not (await st.run("unwrite"))[0]
    assert _parked_rows(kernel) == {MEMBER: {"rows": ["r1"]}} and st.svc.rows["r1"].metadata["status"] == "approved"
    _restart(kernel)
    kernel.forget_mode, st.svc.before_erase = "ok", None
    add()
    assert await _until(lambda: not (kernel.state / "pending-erases.json").exists())
    assert st.svc.rows["r1"].metadata["status"] == "approved" and (MEMBER, "t1") in st.backend.rows
    assert (await st.run("unwrite"))[0] and st.svc.rows == {}


@pytest.mark.asyncio
async def test_the_forget_retry_pass_triggers_the_replay_with_no_write_and_no_forget(kernel, callers):
    """With the flag off there may be no list add for days: the forget retry pass (``retry_pending``: every night per member) nudges
    the worker, so a parked erase is finished without any write or any further forget."""
    st = callers
    await st.said()
    st.svc.before_erase = lambda: setattr(kernel, "forget_mode", "refuse_all")
    assert not (await st.run("last"))[0]
    _restart(kernel)
    kernel.forget_mode, st.svc.before_erase = "ok", None
    n = len(kernel.calls)
    await st.retry_pending("member-b")                                # another member's nightly pass is enough
    assert await _until(lambda: not (kernel.state / "pending-erases.json").exists())
    assert st.svc.rows == {} and (MEMBER, "t1") not in st.backend.rows
    assert all(op == "forget" for _, op, _ in kernel.calls[n:])


@pytest.mark.asyncio
async def test_the_real_row_eraser_parks_its_ids_when_the_shadow_does_not_confirm(kernel, env):  # noqa: F811
    import memory_service

    svc, _col = env
    kernel.forget_mode = "refuse_all"
    with pytest.raises(memory_service.MemoryServiceError, match="Zoe OS shadow erasure failed"):
        await svc.erase_rows(MEMBER, ["r1", "r2"], actor=MEMBER)
    assert _parked_rows(kernel) == {MEMBER: {"rows": ["r1", "r2"]}}


# ---- structure: the parking is at the erasers' own shadow step, and a caller that hides rows reaches the row eraser ---------------------
def _fn(source, name):
    return next(n for n in ast.walk(ast.parse(source)) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)


def _parks(source, name, called, keyword):
    """Does ``name`` call ``called`` with ``keyword=...`` (the ids it parks when the shadow does not confirm)? Syntax tree only."""
    return any(isinstance(c, ast.Call) and _called(c) == called and keyword in {k.arg for k in c.keywords}
               for c in ast.walk(_fn(source, name)))


def _without_keyword(source, name, called, keyword):
    """The mutation: the same source with ``keyword=...`` cut out of that call (the negative control's input)."""
    lines = source.splitlines(keepends=True)
    for c in ast.walk(_fn(source, name)):
        if isinstance(c, ast.Call) and _called(c) == called:
            for k in c.keywords:
                if k.arg == keyword:
                    start = sum(map(len, lines[:k.value.lineno - 1])) + k.value.col_offset
                    end = sum(map(len, lines[:k.value.end_lineno - 1])) + k.value.end_col_offset
                    head = source[:start].rstrip()
                    assert head.endswith(keyword + "=")
                    return head[:-len(keyword) - 1].rstrip().rstrip(",") + source[end:]
    raise AssertionError("no such keyword")


@pytest.mark.parametrize("path,name,called,keyword", [
    ("memory_service.py", "erase_rows", "park_unconfirmed", "rows"),
    ("exact_words.py", "erase_turn", "_shadow_first", "turns"),
    ("exact_words.py", "erase_text", "_shadow_first", "turns"),
])
def test_every_shared_eraser_hands_its_ids_to_the_shadow_step_that_parks_them(path, name, called, keyword):
    source = (SVC / path).read_text("utf-8")
    assert _parks(source, name, called, keyword)
    mutated = _without_keyword(source, name, called, keyword)
    ast.parse(mutated)
    assert not _parks(mutated, name, called, keyword)


def _hides_without_erasing(source, name):
    """A forget entry point that changes a row's lifecycle (``.review(...)``) must also reach ``erase_rows`` - the eraser that parks
    the hidden ids when the shadow does not confirm - or the rows it hid could be left behind for good."""
    calls = {_called(c) for c in ast.walk(_fn(source, name)) if isinstance(c, ast.Call)}
    return "review" in calls and "erase_rows" not in calls


@pytest.mark.parametrize("site", sorted(FORGET_ENTRY_POINTS))
def test_no_forget_entry_point_hides_rows_without_reaching_the_row_eraser(site):
    path, name = site.split("::")
    assert not _hides_without_erasing((SVC / path).read_text("utf-8"), name), f"{site} hides rows it never erases or parks"


def test_the_hide_without_erase_check_catches_a_planted_caller():
    bad = "async def forget(svc, rid):\n    await svc.review(rid, decision='reject')\n    return 'forgotten'\n"
    good = bad.replace("    return", "    await svc.erase_rows('m', [rid], actor='m')\n    return")
    assert _hides_without_erasing(bad, "forget") and not _hides_without_erasing(good, "forget")
    assert not _hides_without_erasing("async def forget(svc):\n    # await svc.review(rid)\n    return 'review'\n", "forget")
