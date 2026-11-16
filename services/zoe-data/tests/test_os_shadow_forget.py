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
                assert any(k.arg == "blocking" and getattr(k.value, "value", True) is False for k in c.keywords), (name, "blocking acquire")


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
