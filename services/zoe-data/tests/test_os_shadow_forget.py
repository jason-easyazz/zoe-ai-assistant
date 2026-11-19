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
    """The forgets the kernel was sent. Lists are off in this file, so NO forget carries an erase stamp (checked on every call):
    the forget goes exactly as it did before the lists door existed. The stamp itself is pinned in ``test_lists_kernel_door.py``."""
    out = []
    for s, op, a in k.calls:
        if op == "forget":
            assert "stamp" not in a, a.keys()
            out.append((s, dict(a)))
    return out


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
            self.stuck: set = set()         # ids the eraser quietly does not take (it reports success all the same)
            self.read_down = None           # "before": every strict read fails; "after": only the read-back after the erase
            self.ordinary_failure = False   # the eraser fails for a reason that is not the shadow

        async def get(self, rid, *, strict=False):   # as MemoryService.get: a failed read is None, or RAISES when strict
            if self.read_down in ("before", "after"):
                if strict:
                    raise _StoreError("the store could not be read")
                return None
            return self.rows.get(rid)

        async def list_by_status(self, *, user_id, status, limit=1000, offset=0, strict=False):
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
            n = sum(self.rows.pop(rid, None) is not None for rid in ids if rid not in self.stuck)
            if self.read_down == "arm-after":
                self.read_down = "after"
            if self.after_erase:
                self.after_erase()
            return {"rows_removed": n}

    import night_store
    monkeypatch.setattr(night_store, "_backend", night_store.MemoryBackend())
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
                      (memory_value_registry, "erase_topic"), (memory_value_registry, "erase_for_rows"), (night_mind, "erase_words"), (pa, "_speculation_barrier"),
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
    os_shadow._planning.clear()                 # (the hold a forget of this process puts on the replay's finish has run out)
    await os_shadow._finish_erases()            # an id parked for a row the repeat has since erased is dropped by the next replay
    assert not (kernel.state / "pending-forgets.json").exists() and not (kernel.state / "pending-erases.json").exists()


def _spy_on_the_loop(monkeypatch, under):
    """Record every executor hop and every look at the disk made from the calling (event-loop) thread: ``asyncio.to_thread``, the
    loop's ``run_in_executor``, the shadow's own probes, pathlib's stat / exists / is_dir / is_file / iterdir, and ``os.stat`` /
    ``os.listdir`` / ``os.scandir`` / ``os.path.exists`` / ``isdir`` / ``lexists`` of anything at or below ``under``."""
    import asyncio
    import threading

    seen, me = [], threading.get_ident()

    def spy(owner, name, only_paths=False):
        real = getattr(owner, name)

        def wrapped(*args, **kwargs):
            if threading.get_ident() == me and (not only_paths or str(args[0] if args else "").startswith(str(under))):
                seen.append(name)
            return real(*args, **kwargs)

        monkeypatch.setattr(owner, name, wrapped)

    spy(asyncio, "to_thread")
    spy(type(asyncio.get_running_loop()), "run_in_executor")
    for name in ("shadow_state_exists", "_dir_exists", "_state_probe", "has_data", "read_marker"):
        if hasattr(os_shadow, name):
            spy(os_shadow, name)
    for name in ("stat", "exists", "is_dir", "is_file", "iterdir"):
        spy(Path, name)
    import os
    for name in ("stat", "listdir", "scandir"):
        spy(os, name, only_paths=True)
    for name in ("exists", "isdir", "lexists"):
        spy(os.path, name, only_paths=True)
    return seen


@pytest.mark.asyncio
@pytest.mark.parametrize("caller", ["direct", *CALLERS])
async def test_with_no_shadow_state_a_forget_makes_no_executor_hop_and_no_look_at_the_disk(kernel, callers, monkeypatch, tmp_path, caller):
    """Flag off, no state directory, the answer established at start-up (``_state_probe``, what the import does): a forget through
    every caller - and the four shadow entry points, the delete's drop and the retry pass's nudge directly - records NOTHING: no
    executor hop, no stat, no listing. The spy's own control is the next test (a forget that is not certain IS seen probing)."""
    monkeypatch.setenv("ZOE_OS_LIST_SHADOW", "off")
    monkeypatch.setenv("ZOE_OS_SHADOW_DIR", str(tmp_path / "absent"))
    getattr(os_shadow, "_state_probe", lambda: None)()               # start-up, off the turn
    st = callers
    await st.said()
    seen = _spy_on_the_loop(monkeypatch, tmp_path)
    if caller == "direct":
        assert [await os_shadow.forget_for_erase(MEMBER), await os_shadow.forget_user(MEMBER), await os_shadow.erase_entity(MEMBER, "Dana"),
                await os_shadow.erase_topic(MEMBER, "the garden"), await os_shadow.drop_parked_erases(MEMBER),
                await os_shadow.nudge(), await st.retry_pending(MEMBER)] == [0, 0, 0, 0, None, None, None]
    else:
        forgotten, out = await st.run(caller)
        assert forgotten, out
        assert st.svc.rows == {} and (caller not in WORDS or (MEMBER, "t1") not in st.backend.rows)
    assert seen == []
    assert kernel.calls == [] and not (tmp_path / "absent").exists()


@pytest.mark.asyncio
async def test_an_answer_that_is_not_certain_always_probes_off_the_loop_and_the_directory_is_what_ends_certainty(kernel, monkeypatch, tmp_path):
    """Every way the cached "no directory" answer stops being usable, each failing toward CHECKING (in a thread, never the loop):
    (1) nothing established; (2) another process makes the directory - EMPTY, as the rule demands - and the poll sees it: forgets
    probe from then on, and with no data yet there is provably nothing to forget; data arriving later is never skipped; (3) this
    process makes the directory: the very next forget probes, with no poll; (4) the poller has stopped: the answer expires."""
    import asyncio
    import threading

    monkeypatch.setenv("ZOE_OS_LIST_SHADOW", "off")
    monkeypatch.setattr(os_shadow, "POLL_S", 0.05)
    monkeypatch.setattr(os_shadow, "TRUST_S", 0.15)
    looks = []
    real = os_shadow._dir_exists
    monkeypatch.setattr(os_shadow, "_dir_exists", lambda: looks.append(threading.get_ident()) or real())
    loop_thread = threading.get_ident()

    def fresh(name):
        d = tmp_path / name
        monkeypatch.setenv("ZOE_OS_SHADOW_DIR", str(d))
        return d

    async def forget_probes():
        del looks[:]
        got = await os_shadow.forget_for_erase(MEMBER)
        mine = [t for t in looks if t != getattr(os_shadow._state_poller, "ident", None)]
        return got, bool(mine) and loop_thread not in mine

    # (1) nothing established for this directory: the forget itself looks, off the loop, and is then answered from memory
    d = fresh("one")
    assert await forget_probes() == (0, True) and os_shadow._no_state() and not d.exists()
    # (2) another process makes the directory EMPTY: the poll notices, and the forget stops trusting memory
    d.mkdir()
    for _ in range(200):
        if not os_shadow._no_state():
            break
        await asyncio.sleep(0.01)
    assert not os_shadow._no_state()
    assert await forget_probes() == (0, True)                           # looked, found no data: nothing to forget
    (d / "kernel").mkdir()                                              # the data arrives (after the settle time, by the rule)
    with pytest.raises(os_shadow.ShadowForgetUnavailable):
        await os_shadow.forget_for_erase(MEMBER)                        # state, and no kernel answers: fails closed, parked
    # (3) THIS process makes the directory: the next forget does not answer from memory, without waiting for any poll
    d = fresh("three")
    assert await forget_probes() == (0, True) and os_shadow._no_state()
    monkeypatch.setattr(os_shadow, "POLL_S", 30.0)                      # no poll can help within this test
    monkeypatch.setattr(os_shadow, "SETTLE_S", 0.0)
    await asyncio.to_thread(os_shadow.write_roster, [MEMBER])
    assert not os_shadow._no_state()
    with pytest.raises(os_shadow.ShadowForgetUnavailable):
        await os_shadow.forget_for_erase(MEMBER)
    # (4) the poller is gone: the answer is not trusted past TRUST_S, and the forget looks at the disk again
    monkeypatch.setattr(os_shadow, "POLL_S", 0.05)
    d = fresh("four")
    assert await forget_probes() == (0, True) and os_shadow._no_state()
    os_shadow._state_stop.set()
    await asyncio.sleep(0.4)
    assert not os_shadow._no_state()
    assert await forget_probes() == (0, True)



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

    async def list_by_status(self, *, user_id, status, limit=1000, offset=0, strict=False):
        return [r for r in self.rows.values() if r.metadata["status"] == status][offset:offset + limit]

    async def review(self, rid, *, decision, **kwargs):
        self.rows[rid].metadata["status"] = "archived"
        return self.rows[rid]

    async def get(self, rid, *, strict=False):
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
    assert _parked_rows(kernel)[MEMBER]["rows"] == ["r1"]


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
    assert _parked_rows(kernel)[MEMBER]["rows"] == ["r1"] and _pending(kernel) == [MEMBER]
    assert SAID not in (kernel.state / "pending-erases.json").read_text()          # ids only, never the words
    _restart(kernel)
    assert turn_forget.unerased(MEMBER) == [] and turn_forget.previous_turn(MEMBER) is None
    kernel.forget_mode, st.svc.before_erase = "ok", None
    add()                                                                           # any job: not a forget, not a request to finish
    assert await _until(lambda: not (kernel.state / "pending-erases.json").exists())
    assert st.svc.rows == {} and (MEMBER, "t1") not in st.backend.rows
    assert not (kernel.state / "pending-forgets.json").exists()
    assert await turn_forget.finish_parked(MEMBER, {"rows": ["r1"], "turns": ["t1"]}) == {}    # replayed twice is harmless


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
    assert "r1" in st.svc.rows and (MEMBER, "t1") in st.backend.rows and _parked_rows(kernel)[MEMBER]["rows"] == ["r1"]
    kernel.forget_mode, st.svc.ordinary_failure = "ok", True                        # the kernel confirms, the row erase itself fails
    add()
    assert await _until(lambda: not (kernel.state / "pending-forgets.json").exists())
    assert await _until(lambda: not os_shadow._finishing) and await os_shadow._finish_erases() == 0
    assert "r1" in st.svc.rows and _parked_rows(kernel)[MEMBER]["rows"] == ["r1"]   # kept, never dropped
    st.svc.ordinary_failure = False
    add()
    assert await _until(lambda: not (kernel.state / "pending-erases.json").exists()) and st.svc.rows == {}


@pytest.mark.asyncio
async def test_a_parked_id_is_erased_whatever_its_status_and_a_missing_row_is_the_only_thing_done_without_an_erase(kernel, callers):
    import turn_forget

    st = callers
    await st.said()
    # a parked id was chosen by a forget: the replay erases it whatever its status (r1 is still APPROVED here), with the words behind it
    assert await turn_forget.finish_parked(MEMBER, {"rows": ["r1", "no-such-row"]}) == {}
    assert st.svc.rows == {} and (MEMBER, "t1") not in st.backend.rows


@pytest.mark.asyncio
async def test_a_member_delete_that_fails_part_way_keeps_the_parked_plan_and_the_retried_delete_drops_it(kernel, env, monkeypatch):  # noqa: F811
    """RUNS the real ``MemoryService.delete_user`` with an earlier forget's plan parked. A LATER eraser fails (the day review, the
    first one after the shadow): the delete raises and the plan is STILL parked - it is what would finish that earlier forget.
    The retried delete succeeds, and only then is the member's entry dropped (another member's stays)."""
    import memory_service
    import omi_day_review

    monkeypatch.setattr(os_shadow, "_kick_erases", lambda: None)     # only the delete's own drop is under test (not the replay's finish)
    os_shadow.park_erase(MEMBER, rows=["r1"], turns=["t1"])
    os_shadow.park_erase("member-b", rows=["r9"])
    svc, _col = env
    real = omi_day_review.forget_user

    async def fails(*args, **kwargs):
        raise OSError("the day-review store is down")

    monkeypatch.setattr(omi_day_review, "forget_user", fails)
    with pytest.raises(memory_service.MemoryServiceError, match="day-review erasure failed"):
        await svc.delete_user(MEMBER, actor=MEMBER)
    assert _parked_rows(kernel) == {MEMBER: {"rows": ["r1"], "turns": ["t1"]}, "member-b": {"rows": ["r9"]}}     # kept
    monkeypatch.setattr(omi_day_review, "forget_user", real)
    await svc.delete_user(MEMBER, actor=MEMBER)                                     # the retry: every eraser confirms
    assert _parked_rows(kernel) == {"member-b": {"rows": ["r9"]}}


@pytest.mark.asyncio
async def test_a_member_delete_drops_the_members_parked_entry(kernel, callers, env, monkeypatch):  # noqa: F811
    monkeypatch.setattr(os_shadow, "_kick_erases", lambda: None)     # only the delete's own drop is under test (not the replay's finish)
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
    assert _parked_rows(kernel)[MEMBER]["turns"] == ["t1"]
    assert SAID not in (kernel.state / "pending-erases.json").read_text()
    _restart(kernel)
    kernel.forget_mode, st.svc.after_erase = "ok", None
    add()
    assert await _until(lambda: not (kernel.state / "pending-erases.json").exists())
    assert (MEMBER, "t1") not in st.backend.rows and not (kernel.state / "pending-forgets.json").exists()


@pytest.mark.asyncio
async def test_a_refused_unwrite_is_finished_by_the_replay_though_its_row_was_never_hidden(kernel, callers):
    """The day-review unwrite hard-erases approved rows with no hiding step. When the shadow refuses, the eraser parks the id; the
    replay erases it - a parked id is erased whatever its status - and the caller's retry then finds nothing left to do."""
    st = callers
    await st.said()
    st.svc.before_erase = lambda: setattr(kernel, "forget_mode", "refuse_all")
    assert not (await st.run("unwrite"))[0]
    assert _parked_rows(kernel) == {MEMBER: {"rows": ["r1"]}} and st.svc.rows["r1"].metadata["status"] == "approved"
    _restart(kernel)
    kernel.forget_mode, st.svc.before_erase = "ok", None
    add()
    assert await _until(lambda: not (kernel.state / "pending-erases.json").exists())
    assert st.svc.rows == {}
    import omi_review_apply
    assert await omi_review_apply.unwrite(MEMBER, [{"kind": "person_fact", "written_ref": "memory:r1"}]) == 0      # the retry: a no-op


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


# ---- PARK FIRST: the whole plan is durable before the first destructive step ------------------------------------------------------------
exact_words_mod = __import__("exact_words")


class _SqlIndex(exact_words_mod.SqlBackend):
    """The production backend's TYPE (``forget_transcript`` acts only with it) over an in-memory index, for the transcript half."""

    def __init__(self):
        self.mem = exact_words_mod.MemoryBackend()

    async def rows_matching(self, user_id, needle):
        return await self.mem.rows_matching(user_id, needle)

    async def get(self, user_id, turn_ids):
        return await self.mem.get(user_id, turn_ids)

    async def delete(self, user_id, turn_ids):
        return await self.mem.delete(user_id, turn_ids)


@pytest.fixture
def transcript(kernel, monkeypatch):
    """One indexed verbatim turn (``t1``) and its saved transcript row (``m1``), with the transcript seams in memory."""
    from types import SimpleNamespace

    import night_mind

    import night_store
    monkeypatch.setattr(night_store, "_backend", night_store.MemoryBackend())
    index, meta = _SqlIndex(), {"m1": "{}"}

    async def candidates(user_id, needle):
        return [("m1", SAID, meta["m1"])] if needle in SAID.lower() else []

    async def by_id(user_id, ids):
        return [(i, SAID, meta[i]) for i in ids if i in meta]

    async def update(row_id, value):
        meta[row_id] = value

    async def noop(*args, **kwargs):
        return 0

    exact_words_mod.set_backend(index)
    monkeypatch.setattr(exact_words_mod, "_transcript_candidates", candidates)
    monkeypatch.setattr(exact_words_mod, "_transcript_rows", by_id)
    monkeypatch.setattr(exact_words_mod, "_set_transcript_metadata", update)
    monkeypatch.setattr(night_mind, "erase_words", noop)
    yield SimpleNamespace(index=index, meta=meta)
    exact_words_mod.set_backend(None)


@pytest.mark.asyncio
async def test_the_kernel_confirming_the_index_erase_and_failing_at_the_next_step_still_ends_with_the_transcript_suppressed(kernel, transcript, monkeypatch):
    """The reviewer's case, on the real ``provenance_answers._erase_words``: the index turn is erased BY ID (confirmed), then the
    kernel stops before the next eraser. The index row - the only lead to the saved transcript turn - is gone. Because the whole
    plan was parked FIRST, the transcript row's id is durable: after a restart the replay suppresses it and clears the park."""
    import memory_provenance as mp
    import provenance_answers as pa

    await transcript.index.mem.add(MEMBER, "t1", 1, SAID, " lavender ", "test")
    exp = mp.Explained(seq=0, row_id="", ts=0.0, text=SAID, quote=SAID, turn_id="t1")
    real_erase_turn, order = exact_words_mod.erase_turn, []

    async def erase_turn_then_the_kernel_stops(user_id, turn_id):
        order.append(json.loads((kernel.state / "pending-erases.json").read_text()))     # what was durable BEFORE the first step
        n = await real_erase_turn(user_id, turn_id)
        kernel.forget_mode = "refuse_all"                                                # confirmed step 1; step 2 will be refused
        return n

    monkeypatch.setattr(exact_words_mod, "erase_turn", erase_turn_then_the_kernel_stops)
    with pytest.raises(os_shadow.ShadowForgetUnavailable):
        await pa._erase_words(MEMBER, exp)
    assert order == [{MEMBER: {"turns": ["t1"], "messages": ["m1"]}}]                    # PARK FIRST: the whole plan, ids only
    assert (MEMBER, "t1") not in transcript.index.mem.rows                              # step 1 really happened ...
    assert transcript.meta == {"m1": "{}"}                                              # ... and the transcript is NOT suppressed yet
    assert _parked_rows(kernel)[MEMBER]["messages"] == ["m1"] and SAID not in (kernel.state / "pending-erases.json").read_text()
    monkeypatch.setattr(exact_words_mod, "erase_turn", real_erase_turn)
    _restart(kernel)
    kernel.forget_mode = "ok"
    add()                                                                               # any job: nobody asks again
    assert await _until(lambda: not (kernel.state / "pending-erases.json").exists())
    assert json.loads(transcript.meta["m1"]) == {"off_record": True}
    assert not (kernel.state / "pending-forgets.json").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("caller", ["last", "explained", "topic", "screen", "guardian", "curation"])
async def test_every_multi_step_forget_parks_its_whole_plan_before_its_first_step_and_clears_it_when_all_confirm(kernel, callers, monkeypatch, caller):
    """ORDER, for every caller that hides rows: at the first lifecycle change (the reject / archive) the park already holds the row
    id - and the index turn id for the callers that erase words. A forget that completes leaves no park behind."""
    st, seen = callers, []
    await st.said()
    real_review = st.svc.review

    async def review(rid, **kwargs):
        seen.append(json.loads((kernel.state / "pending-erases.json").read_text()) if (kernel.state / "pending-erases.json").exists() else {})
        return await real_review(rid, **kwargs)

    monkeypatch.setattr(st.svc, "review", review)
    forgotten, out = await st.run(caller)
    assert forgotten, out
    assert seen and seen[0][MEMBER]["rows"] == ["r1"]
    assert seen[0][MEMBER].get("turns", []) == (["t1"] if caller in WORDS else [])
    assert st.svc.rows == {} and not (kernel.state / "pending-erases.json").exists()    # all confirmed: the plan left the park


@pytest.mark.asyncio
@pytest.mark.parametrize("caller", ["last", "explained", "topic", "screen", "guardian"])
async def test_a_forget_that_dies_between_two_steps_is_finished_by_the_replay_from_its_parked_plan(kernel, callers, monkeypatch, caller):
    """The crash shape: the rows are erased (confirmed), then the forget stops for a reason no eraser can park (the request is
    cancelled between two steps, as a dying process would). Nothing was refused, so no eraser parked anything - only the plan,
    parked first, remembers the words. Restart, any job: the words are gone and the park is cleared."""
    import asyncio

    import forget_topic

    st = callers
    await st.said()

    def die():
        raise asyncio.CancelledError()

    if caller == "topic":            # a topic forget erases the words before the rows: it dies before the rows instead
        async def boom(*args, **kwargs):
            die()
        monkeypatch.setattr(forget_topic, "_erase_words", boom)
    else:
        st.svc.after_erase = die
    with pytest.raises(asyncio.CancelledError):
        await st.run(caller)
    parked = _parked_rows(kernel)[MEMBER]
    assert parked.get("turns") == ["t1"] and (MEMBER, "t1") in st.backend.rows          # the words are still there, and remembered
    _restart(kernel)
    st.svc.after_erase = None
    os_shadow._planning.clear()                                                         # a restart: nothing of this process is in flight
    add()
    assert await _until(lambda: not (kernel.state / "pending-erases.json").exists())
    assert (MEMBER, "t1") not in st.backend.rows


# ---- the replay erases EVERY parked id, whatever its status; one it cannot erase stays parked ---------------------------------------------
@pytest.mark.asyncio
@pytest.mark.parametrize("stuck", [(), ("r0b",)], ids=["all-erasable", "one-id-cannot-be-erased"])
async def test_a_curation_forget_the_kernel_refused_is_replayed_for_the_row_and_every_superseded_revision(kernel, callers, monkeypatch, stuck):
    """The reviewer's case. A curator's forget erases the current row AND its superseded revision chain (the earlier wording). The
    kernel fails at that erase: all three ids are parked. Restart, the kernel recovers, any job: the replay erases ALL three - a
    superseded row is not "still hidden", it was chosen by the forget - and clears the park. The variant: the eraser quietly does
    not take one id; the two it took leave the park, the third STAYS parked and is counted, and is finished once it can be."""
    from types import SimpleNamespace

    import memory_curation as mc

    st = callers
    await st.said()
    for rid, wording in (("r0a", "The synthetic garden grew thyme"), ("r0b", "The synthetic garden grew sage")):
        st.svc.rows[rid] = SimpleNamespace(id=rid, text=wording, metadata={"user_id": MEMBER, "status": "superseded"})

    async def chain(*args):
        return ["r0a", "r0b"]

    monkeypatch.setattr(mc, "_earlier_revisions", chain)
    st.svc.before_erase = lambda: setattr(kernel, "forget_mode", "refuse_all")
    assert not (await st.run("curation"))[0]
    assert _parked_rows(kernel) == {MEMBER: {"rows": ["r1", "r0a", "r0b"]}} and len(st.svc.rows) == 3
    assert st.svc.rows["r0a"].metadata["status"] == "superseded"                    # never hidden: exactly what the old replay skipped
    _restart(kernel)
    kernel.forget_mode, st.svc.before_erase, st.svc.stuck = "ok", None, set(stuck)
    add()                                                                           # any job: nobody asks again
    if not stuck:
        assert await _until(lambda: not (kernel.state / "pending-erases.json").exists())
        assert st.svc.rows == {} and os_shadow.parked_unfinished() == 0
        return
    assert await _until(lambda: sorted(st.svc.rows) == ["r0b"] and os_shadow.parked_unfinished() == 1)
    assert await _until(lambda: _parked_rows(kernel) == {MEMBER: {"rows": ["r0b"]}})  # kept: never cleared as done, and counted
    assert st.svc.rows["r0b"].text == "The synthetic garden grew sage"
    st.svc.stuck = set()
    add()
    assert await _until(lambda: not (kernel.state / "pending-erases.json").exists())
    assert st.svc.rows == {} and os_shadow.parked_unfinished() == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("when", ["before", "arm-after"], ids=["the-look-before-the-erase", "the-read-back-after-it"])
async def test_a_read_that_fails_during_the_replay_is_never_read_as_gone(kernel, callers, when):
    """``MemoryService.get`` answers None for a read that FAILED, so "None means gone" could clear a park after a hiccup. The replay
    uses the strict read: when the look before the erase, or the read-back after it, raises, nothing is cleared, the ids stay parked
    and are counted; the next job with the store back finishes them."""
    st = callers
    await st.said()
    st.svc.before_erase = lambda: setattr(kernel, "forget_mode", "refuse_all")
    assert not (await st.run("curation"))[0]
    parked = _parked_rows(kernel)
    assert parked == {MEMBER: {"rows": ["r1"]}}
    _restart(kernel)
    kernel.forget_mode, st.svc.before_erase, st.svc.read_down = "ok", None, when
    add()
    assert await _until(lambda: os_shadow.parked_unfinished() == 1)
    assert await _until(lambda: not os_shadow._finishing)
    assert _parked_rows(kernel) == parked                                           # nothing cleared: "could not look" is not "gone"
    if when == "before":
        assert "r1" in st.svc.rows                                                  # not even erased: the replay stopped at the look
    st.svc.read_down = None
    add()                                                                           # the store answers again
    assert await _until(lambda: not (kernel.state / "pending-erases.json").exists())
    assert st.svc.rows == {} and os_shadow.parked_unfinished() == 0


@pytest.mark.asyncio
async def test_the_real_strict_reads_raise_on_a_store_failure_where_the_plain_ones_answer_none_or_empty(env, monkeypatch):  # noqa: F811
    """The real ``MemoryService``: ``get`` and ``list_by_status`` swallow a read failure into None / [] (unchanged for every other
    caller); with ``strict=True`` they raise it, and None / [] mean only that the store really holds nothing."""
    import memory_service

    svc, _col = env
    assert await svc.get("no-such-row", strict=True) is None and await svc.get("no-such-row") is None
    assert await svc.list_by_status(user_id=MEMBER, status="approved", strict=True) == []

    def broken(*args):
        raise OSError("the store could not be read")

    monkeypatch.setattr(svc, "_get_sync", broken)
    monkeypatch.setattr(svc, "_list_by_status_sync", broken)
    assert await svc.get("r1") is None and await svc.list_by_status(user_id=MEMBER, status="approved") == []    # the swallows
    with pytest.raises(memory_service.MemoryServiceError, match="get failed"):
        await svc.get("r1", strict=True)
    with pytest.raises(memory_service.MemoryServiceError, match="list_by_status failed"):
        await svc.list_by_status(user_id=MEMBER, status="approved", strict=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("replay_down", [False, True], ids=["replayed", "night-store-down"])
async def test_night_observation_survives_index_loss_only_until_its_parked_erase_confirms(kernel, transcript, monkeypatch, replay_down):
    import asyncio
    import memory_provenance as mp
    import night_store
    import provenance_answers as pa

    store = night_store.MemoryBackend()
    monkeypatch.setattr(night_store, "_backend", store)
    await store.put_thread(night_store.thread_row(id="thread-quartz", user_id=MEMBER, title="Imaginary fennel patch"))
    await store.put_observation(night_store.obs_row(id="obs-quartz", user_id=MEMBER, thread_id="thread-quartz",
                                                  turn_id="turn-quartz", quote="Imaginary fennel grows on Saturn"))
    await transcript.index.mem.add(MEMBER, "turn-quartz", 1, "Imaginary fennel grows on Saturn", " fennel ", "test")
    exp = mp.Explained(seq=0, row_id="", ts=0.0, text="Imaginary fennel grows on Saturn", quote="", turn_id="turn-quartz")
    erase_turn = exact_words_mod.erase_turn

    async def die_after_index(user_id, tid):
        await erase_turn(user_id, tid)
        raise asyncio.CancelledError()

    monkeypatch.setattr(exact_words_mod, "erase_turn", die_after_index)
    with pytest.raises(asyncio.CancelledError):
        await pa._erase_words(MEMBER, exp)
    assert not transcript.index.mem.rows and await store.observations(MEMBER)
    assert _parked_rows(kernel)[MEMBER]["observations"] == ["obs-quartz"]
    assert "Imaginary fennel" not in (kernel.state / "pending-erases.json").read_text()
    monkeypatch.setattr(exact_words_mod, "erase_turn", erase_turn)
    erase_ids = store.erase_ids

    async def finish(user_id, observations, threads):
        assert (kernel.state / "pending-erases.json").exists()  # never clear first
        if replay_down:
            raise RuntimeError("synthetic night store unavailable")
        return await erase_ids(user_id, observations, threads)

    monkeypatch.setattr(store, "erase_ids", finish)
    _restart(kernel)
    assert await os_shadow._finish_erases() == (0 if replay_down else 1)
    if replay_down:
        assert await store.observations(MEMBER)
        assert _parked_rows(kernel)[MEMBER]["observations"] == ["obs-quartz"]
        assert os_shadow.parked_unfinished() == sum(len(ids) for ids in _parked_rows(kernel)[MEMBER].values())
        replay_down = False
        assert await os_shadow._finish_erases() == 1
    assert not await store.observations(MEMBER) and not await store.threads(MEMBER)
    assert not (kernel.state / "pending-erases.json").exists()
    assert os_shadow.parked_unfinished() == 0


# Eraser contract: each durable source eraser maps to the actual replay entry point.
PLANNED_ERASERS = {
    "exact_words.erase_turn": "exact_words.finish_parked",
    "exact_words.erase_text": "exact_words.finish_parked",
    "exact_words.forget_transcript": "exact_words.finish_parked",
    "exact_words.delete": "exact_words.finish_parked",
    "memory_service.erase_rows": "memory_service.erase_rows",
    "memory_service._delete_ids": "memory_service.erase_rows",
    "memory_service._delete_audit_for_rows_sync": "memory_service.erase_rows",
    "memory_service._physical_erase": "memory_service.erase_rows",
    "night_mind.erase_words": "night_mind.erase_ids",
    "conversation_notes.erase_for_text": "conversation_notes.erase_ids",
    "conversation_notes.erase_topic": "conversation_notes.erase_ids",
    "memory_value_registry.erase_for_rows": "memory_value_registry.erase_ids",
    "memory_value_registry.erase_for_turn": "memory_value_registry.erase_ids",
    "memory_value_registry.erase_topic": "memory_value_registry.erase_ids",
    "pending_suggestions.withdraw_matching": "pending_suggestions.erase_ids",
}
# These erase no durable words: the kernel is already confirmed before replay; the rest is process-local.
PLAN_PROCESS_STEPS = {"os_shadow.forget_for_erase", "os_shadow.erase_topic", "turn_forget.retract",
                      "turn_forget.mark_forgotten", "turn_forget.clear_unerased", "value_correction.forget_turns",
                      "value_correction.forget_matching", "value_correction.forget_in_texts"}
PLAN_SEQUENCES = {
    "provenance_answers": ("_erase_words", "forget_it", "_forget_turn"),
    "intent_router": ("_execute_forget_last",),
    "forget_topic": ("_forget", "_erase_words", "_withdraw_offers"),
    "memory_space": ("forget_row", "guardian_forget"),
    "memory_curation": ("_forget_locked",),
    "memory_service": ("erase_rows",),
}


def _sequence_erasers(source, names, owner):
    tree = ast.parse(source)
    aliases = {a.asname or a.name: a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    aliases.update(svc="memory_service", self="memory_service", backend="exact_words", pa="provenance_answers")
    found = set()
    for name in names:
        for call in ast.walk(_fn(source, name)):
            if not isinstance(call, ast.Call):
                continue
            target = call.func
            if isinstance(target, ast.Name):
                if target.id in {*names, "_delete_tombstone_id", "_delete_tombstone_body", "_forget_preview"}:  # wrappers / content-free receipts
                    continue
                if target.id.lstrip("_").startswith(("erase_", "forget_", "withdraw_", "delete_")):
                    found.add(f"{owner}.{target.id}")
                continue
            if not isinstance(target, ast.Attribute):
                continue
            if target.attr == "_run_sync" and call.args and isinstance(call.args[0], ast.Attribute):
                target = call.args[0]
            receiver, verb = target.value, target.attr
            if not isinstance(receiver, ast.Name):
                continue
            module = aliases.get(receiver.id, receiver.id)
            # Wrappers are scanned in PLAN_SEQUENCES, not treated as terminal erasers.
            if (module, verb) in {("provenance_answers", "_erase_words"), ("forget_topic", "_erase_words")}:
                continue
            if verb.lstrip("_").startswith(("erase_", "forget_", "withdraw_", "delete_", "physical_erase")) or verb in {"delete", "retract", "mark_forgotten", "clear_unerased"}:
                found.add(f"{module}.{verb}")
    return found


def _assert_plan_replay(sources):
    source = set().union(*(_sequence_erasers(sources[module], names, module) for module, names in PLAN_SEQUENCES.items()))
    assert source - PLAN_PROCESS_STEPS == set(PLANNED_ERASERS)
    replay = _sequence_erasers(sources["turn_forget"], ("finish_parked",), "turn_forget")
    # finish_parked is the shared index + transcript eraser, so include it explicitly.
    if any(
        isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute) and ast.unparse(c.func) == "exact_words.finish_parked"
        for c in ast.walk(_fn(sources["turn_forget"], "finish_parked"))):
        replay.add("exact_words.finish_parked")
    assert replay == set(PLANNED_ERASERS.values())


def test_every_planned_eraser_has_a_replay_and_a_new_step_cannot_silently_disappear():
    sources = {module: (SVC / f"{module}.py").read_text() for module in (*PLAN_SEQUENCES, "turn_forget")}
    _assert_plan_replay(sources)
    mutated = dict(sources)
    mutated["provenance_answers"] = sources["provenance_answers"].replace(
        "    n += await night_mind.erase_words", "    await night_mind.erase_quartz(user_id)\n    n += await night_mind.erase_words")
    with pytest.raises(AssertionError):
        _assert_plan_replay(mutated)
    mutated = dict(sources)
    mutated["turn_forget"] = sources["turn_forget"].replace("await night_mind.erase_ids", "await night_mind.read_ids")
    with pytest.raises(AssertionError):
        _assert_plan_replay(mutated)


@pytest.mark.asyncio
async def test_night_words_plan_carries_observations_and_threads_without_erasing_or_crossing_members(monkeypatch):
    import night_mind
    import night_store

    store = night_store.MemoryBackend()
    monkeypatch.setattr(night_store, "_backend", store)
    for uid, suffix in (("member-quartz", "quartz"), ("member-topaz", "topaz")):
        await store.put_thread(night_store.thread_row(id=f"thread-{suffix}", user_id=uid, title="Imaginary saffron"))
        await store.put_observation(night_store.obs_row(id=f"obs-{suffix}", user_id=uid, thread_id=f"thread-{suffix}",
                                                      turn_id=f"turn-{suffix}", quote="Imaginary saffron grows on Venus"))
    plan = await night_mind.words_plan("member-quartz", "turn-quartz")
    assert plan == {"observations": ["obs-quartz"], "threads": ["thread-quartz"]}
    assert len(store.obs_rows) == 2 and len(store.thread_rows) == 2  # planning only reads
    assert await night_mind.erase_ids("member-quartz", **plan) == 2
    assert not await store.observations("member-quartz") and not await store.threads("member-quartz")
    assert await night_mind.erase_ids("member-quartz", **plan) == 0  # idempotent
    assert len(await store.observations("member-topaz")) == len(await store.threads("member-topaz")) == 1


@pytest.mark.asyncio
async def test_a_row_already_deleted_still_gets_its_owned_audit_and_residue_cleanup(tmp_path, monkeypatch):
    import memory_service

    svc = memory_service.MemoryService(data_dir=str(tmp_path))
    audit = {"audit-quartz": {"user_id": "member-quartz"}, "audit-topaz": {"user_id": "member-topaz"}}
    scrubbed = []

    class Audit:
        def get(self, **kwargs):
            return {"ids": list(audit), "metadatas": list(audit.values())}

        def delete(self, *, ids):
            for rid in ids:
                audit.pop(rid)

    async def scrub(needles):
        scrubbed.append(needles)
        return {"ok": True}

    async def noop(*args, **kwargs):
        return 0

    async def sync(fn, *args):
        return fn(*args)

    monkeypatch.setattr(os_shadow, "forget_for_erase", noop)
    monkeypatch.setattr(__import__("memory_value_registry"), "erase_for_rows", noop)
    monkeypatch.setattr(svc, "_run_sync", sync)
    monkeypatch.setattr(svc, "_owned_rows_sync", lambda *_: ([], []))
    monkeypatch.setattr(svc, "_audit_collection", lambda: Audit())
    monkeypatch.setattr(svc, "_physical_erase", scrub)
    result = await svc.erase_rows("member-quartz", ["row-quartz"], actor="member-quartz", finish_missing=True)
    assert result["rows_removed"] == 0 and result["audit_removed"] == 1
    assert list(audit) == ["audit-topaz"] and scrubbed == [[]]


@pytest.mark.asyncio
@pytest.mark.parametrize("caller", ["last", "explained", "topic", "screen", "guardian", "curation"])
async def test_ordinary_erase_failure_keeps_the_plan_until_replay(ordinary_callers, caller):
    kernel, st = ordinary_callers
    await st.said()
    st.svc.ordinary_failure = True
    forgotten, out = await st.run(caller)
    assert not forgotten, out
    assert _parked_rows(kernel)[MEMBER]["rows"] == ["r1"]
    assert "r1" in st.svc.rows
    st.svc.ordinary_failure = False
    os_shadow._planning.clear()        # the in-flight hold expired: the next replay can finish
    await os_shadow._finish_erases()
    assert st.svc.rows == {} and not (kernel.state / "pending-erases.json").exists()
    assert (MEMBER, "t1") not in st.backend.rows or caller not in WORDS


@pytest.fixture
def ordinary_callers(tmp_path, monkeypatch):
    """Ordinary storage failures: the kernel confirms, with no socket or worker needed."""
    from types import SimpleNamespace

    async def confirmed(*args, **kwargs):
        return 0

    async def inline(fn, *args, **kwargs):
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(fn, *args, **kwargs).result()

    # These tests exercise durable erases, not executor/socket wakeups.
    monkeypatch.setattr(os_shadow.asyncio, "to_thread", inline)
    os_shadow._reset_for_tests()
    monkeypatch.setenv("ZOE_OS_SHADOW_DIR", str(tmp_path))
    monkeypatch.setenv("ZOE_OS_LIST_SHADOW", "shadow")
    monkeypatch.setattr(os_shadow, "SETTLE_S", 0)
    for name in ("forget_for_erase", "erase_topic"):
        monkeypatch.setattr(os_shadow, name, confirmed)
    monkeypatch.setattr(os_shadow, "_kick_erases", lambda: None)
    stores = callers.__wrapped__(None, monkeypatch)
    yield SimpleNamespace(state=tmp_path), next(stores)
    stores.close()
    os_shadow._reset_for_tests()


def _assert_plan_failures_marked(sources):
    found = set()
    for filename, source in sources.items():
        tree = ast.parse(source)
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.AsyncFunctionDef):
                continue
            starts = [n.lineno for n in ast.walk(fn) if isinstance(n, ast.Assign)
                      and any(isinstance(t, ast.Name) and t.id == "plan" for t in n.targets)
                      and isinstance(n.value, ast.Await) and isinstance(n.value.value, ast.Call)
                      and ((isinstance(n.value.value.func, ast.Attribute)
                            and n.value.value.func.attr in {"plan_erase", "_plan_forget"})
                           or (isinstance(n.value.value.func, ast.Name) and n.value.value.func.id == "_plan_forget"))]
            ends = [n.lineno for n in ast.walk(fn) if isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Attribute) and isinstance(n.func.value, ast.Name)
                    and n.func.value.id == "plan" and n.func.attr == "ok"]
            if not starts or not ends:
                continue
            found.add((filename, fn.name))
            called = {n.func.id for n in ast.walk(fn) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                      and min(starts) < n.lineno < max(ends)}
            helpers = [n for n in ast.walk(fn) if isinstance(n, ast.AsyncFunctionDef) and n is not fn and n.name in called]
            for handler in ast.walk(fn):
                if not isinstance(handler, ast.ExceptHandler):
                    continue
                if not (min(starts) < handler.lineno < max(ends)
                        or any(h.lineno < handler.lineno < h.end_lineno for h in helpers)):
                    continue
                # Failure to acquire the plan precedes the planned sequence.
                if any(isinstance(n, ast.Try) and handler in n.handlers
                       and any(n.lineno <= start <= n.body[-1].end_lineno for start in starts)
                       for n in ast.walk(fn)):
                    continue
                # The one exemption: a step that only clears IN-PROCESS correction memory (``value_correction``, no await): nothing
                # durable is left behind, and its failure must never break the forget (tests/test_value_correction_forget.py).
                owner = next(n for n in ast.walk(fn) if isinstance(n, ast.Try) and handler in n.handlers)
                body = "\n".join(ast.unparse(b) for b in owner.body)
                if ("value_correction" in body or body.strip() == "clear()") and "await" not in body:
                    continue
                marked = any(isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
                             and ast.unparse(n.value.func) == "plan.failed" for n in ast.walk(handler))
                assert marked or isinstance(handler.body[-1], ast.Raise), (filename, fn.name, handler.lineno)
    return found


def test_every_planned_sequence_marks_caught_failures_with_a_negative_control():
    sources = {}
    for path in SVC.rglob("*.py"):
        if "tests" in path.relative_to(SVC).parts:
            continue
        source = path.read_text()
        if "plan_erase(" in source or "_plan_forget(" in source:
            sources[str(path.relative_to(SVC))] = source
    assert _assert_plan_failures_marked(sources) == {
        ("intent_router.py", "_execute_forget_last"), ("intent_router.py", "execute_intent"),
        ("provenance_answers.py", "_erase_words"), ("provenance_answers.py", "forget_it"),
        ("forget_topic.py", "_forget"), ("memory_space.py", "forget_row"),
        ("memory_space.py", "guardian_forget"), ("memory_curation.py", "_forget_locked"),
    }
    with pytest.raises(AssertionError):
        _assert_plan_failures_marked({**sources, "intent_router.py": sources["intent_router.py"].replace(
            "if plan is not None:\n                    plan.failed()",
            "if plan is not None:\n                    pass")})
    for name in ("intent_router.py", "provenance_answers.py", "forget_topic.py"):
        planted = {**sources, name: sources[name].replace("plan.failed()", "pass")}
        with pytest.raises(AssertionError):
            _assert_plan_failures_marked(planted)


@pytest.mark.asyncio
async def test_failed_plan_ok_keeps_the_disk_park(ordinary_callers):
    kernel, _ = ordinary_callers
    plan = await os_shadow.plan_erase("member-azurite", rows=["row-azurite"])
    plan.failed()
    await plan.ok()
    assert _parked_rows(kernel)["member-azurite"]["rows"] == ["row-azurite"]
    assert plan.has_failed and plan.entry is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("caller", ["last", "explained", "topic"])
async def test_failure_marker_control_recreates_false_confirmation(ordinary_callers, monkeypatch, caller):
    _, st = ordinary_callers
    await st.said()
    st.svc.ordinary_failure = True
    monkeypatch.setattr(os_shadow.Plan, "failed", lambda self: None)
    forgotten, out = await st.run(caller)
    assert forgotten, out             # the old false confirmation: the main regression assertion would fail
    assert "r1" in st.svc.rows


@pytest.mark.asyncio
async def test_entity_ordinary_failure_keeps_its_reply_and_cascade_and_parks_for_replay(ordinary_callers, monkeypatch):
    kernel, _ = ordinary_callers

    async def confirmed(*args, **kwargs):
        return 0

    monkeypatch.setattr(os_shadow, "erase_entity", confirmed)
    forget = entity.__wrapped__(None, monkeypatch)
    store = _EntityStore(fail="ordinary")
    reply = await forget(store)
    assert reply != forget.incomplete and "forgot" in reply.lower()
    assert forget.cascades == ["Dana"] and forget.marked == []
    assert store.rows["r1"].metadata["status"] == "archived"
    assert _parked_rows(kernel)[MEMBER]["rows"] == ["r1"]
    store.fail = None
    os_shadow._planning.clear()
    await os_shadow._finish_erases()
    assert store.rows == {} and not (kernel.state / "pending-erases.json").exists()


def test_with_the_shadow_off_a_step_failure_never_marks_a_plan_or_changes_a_reply():
    """Review (PR #2631), no regression against main: flag off and no shadow state = ``plan_erase`` returns an EMPTY plan, and an
    empty plan cannot fail - so a caller's ``if plan.has_failed`` reply branch is unreachable and the pre-shadow reply stands."""
    empty = os_shadow.Plan("member-poff", None)
    empty.failed()
    assert empty.has_failed is False
    import asyncio

    asyncio.run(empty.ok())                                    # still a no-op, never raises
    real = os_shadow.Plan("member-poff", {"rows": ["r1"]})
    real.failed()
    assert real.has_failed is True                             # control: a real plan does fail


@pytest.mark.asyncio
@pytest.mark.parametrize("caller", ["last", "topic"])
async def test_ordinary_erase_failure_shadow_off_preserves_caller_result(ordinary_callers, monkeypatch, caller):
    import forget_topic
    import intent_router

    kernel, st = ordinary_callers
    monkeypatch.delenv("ZOE_OS_LIST_SHADOW", raising=False)
    monkeypatch.setenv("ZOE_OS_SHADOW_DIR", str(kernel.state / "absent"))
    await st.said()
    st.svc.ordinary_failure = True
    forgotten, result = await st.run(caller)

    assert forgotten
    assert st.svc.rows["r1"].metadata["status"] == ("rejected" if caller == "last" else "archived")
    assert (MEMBER, "t1") not in st.backend.rows
    assert not (kernel.state / "absent" / "pending-erases.json").exists()
    if caller == "last":
        assert result == f'Done — I forgot: "{SAID}".'
        assert result != intent_router.FORGET_STORE_DOWN_REPLY
    else:
        assert result == forget_topic.Result(found=1, forgotten=1, turns_retracted=1, preview=SAID, words_erased=1)
        assert not result.store_down
    # Shadow-on controls: test_ordinary_erase_failure_keeps_the_plan_until_replay[last/topic].


@pytest.mark.asyncio
@pytest.mark.parametrize("shadow", [True, False], ids=["shadow", "off"])
async def test_forget_that_unconfirmed_physical_erase(ordinary_callers, monkeypatch, shadow, tmp_path):
    import intent_router
    import memory_service

    kernel, st = ordinary_callers
    if not shadow:
        monkeypatch.setenv("ZOE_OS_LIST_SHADOW", "off")
        monkeypatch.setenv("ZOE_OS_SHADOW_DIR", str(tmp_path / "absent"))
    real = memory_service.MemoryService(data_dir=str(tmp_path))

    async def inline(fn, *args):
        return fn(*args)

    async def physical(_needles):
        return {"ok": False}

    monkeypatch.setattr(real, "_run_sync", inline)
    monkeypatch.setattr(real, "_owned_rows_sync", lambda uid, ids: (list(ids), [SAID]))
    monkeypatch.setattr(real, "_append_audit_sync", lambda *args: None)
    monkeypatch.setattr(real, "_delete_ids", lambda ids: [st.svc.rows.pop(rid, None) for rid in ids])
    monkeypatch.setattr(real, "_delete_audit_for_rows_sync", lambda ids: 0)
    monkeypatch.setattr(real, "_physical_erase", physical)
    monkeypatch.setattr(st.svc, "erase_rows", real.erase_rows)
    await st.said()
    forgotten, reply = await st.run("last")
    if shadow:
        assert not forgotten and reply == intent_router.FORGET_STORE_DOWN_REPLY
        assert _parked_rows(kernel)[MEMBER]["rows"] == ["r1"]
    else:
        assert forgotten and reply == f'Done — I forgot: "{SAID}".'
        assert not (tmp_path / "absent" / "pending-erases.json").exists()


def test_note_erase_marks_only_real_plans_on_explicit_failure():
    real = os_shadow.Plan("member-citrine", {"rows": ["row-citrine"]})
    empty = os_shadow.Plan("member-citrine", None)
    for result in (None, {}, {"physical": None}, {"physical": {"ok": True}}):
        real.note_erase(result=result)
        assert not real.has_failed
    for plan in (real, empty):
        plan.note_erase(result={"physical": {"ok": False}})
    assert real.has_failed and not empty.has_failed


def _assert_erase_results_noted(sources):
    count = 0
    for source in sources.values():
        tree = ast.parse(source)
        for node in ast.walk(tree):
            for field in ("body", "orelse", "finalbody"):
                statements = getattr(node, field, None)
                if not isinstance(statements, list):
                    continue
                for index, stmt in enumerate(statements):
                    if not (isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.Await)
                            and isinstance(stmt.value.value, ast.Call)
                            and isinstance(stmt.value.value.func, ast.Attribute)
                            and stmt.value.value.func.attr == "erase_rows"):
                        continue
                    count += 1
                    assert len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name)
                    assert index + 1 < len(statements)
                    note = statements[index + 1]
                    # the one other accepted form: on a path with NO plan yet (the repeat of an earlier forget), a physical erase
                    # that did not confirm RAISES, which keeps the carried rows
                    if (isinstance(note, ast.If) and '.get("ok") is False' in ast.unparse(note.test).replace("'", '"')
                            and "os_shadow.in_play()" in ast.unparse(note.test) and isinstance(note.body[-1], ast.Raise)):
                        continue
                    assert isinstance(note, ast.Expr) and isinstance(note.value, ast.Call)
                    assert ast.unparse(note.value.func) == "plan.note_erase"
                    assert len(note.value.keywords) == 1 and note.value.keywords[0].arg == "result"
                    assert ast.unparse(note.value.keywords[0].value) == stmt.targets[0].id
    assert count == 8


def test_planned_erase_results_are_noted_with_negative_control():
    names = ("intent_router", "provenance_answers", "memory_space", "forget_topic", "memory_curation")
    sources = {name: (SVC / f"{name}.py").read_text() for name in names}
    _assert_erase_results_noted(sources)
    for name in names:
        mutated = dict(sources)
        mutated[name] = sources[name].replace("plan.note_erase(result=erased)", "pass", 1)
        with pytest.raises(AssertionError):
            _assert_erase_results_noted(mutated)


def test_in_play_is_false_with_the_switch_off_and_no_state(monkeypatch):
    """Review (PR #2679), no regression against main: the repeat-forget path raises on a soft physical-erase failure ONLY with the
    switch on. Switch off = main's path exactly, whatever the state cache says, and without touching the disk (rounds 3 and 4)."""
    def must_not_run():
        raise AssertionError("switch off: no cache read, no disk probe")
    monkeypatch.setattr(os_shadow, "_no_state", must_not_run)
    monkeypatch.setattr(os_shadow, "_state_probe", must_not_run)
    monkeypatch.setattr(os_shadow, "mode", lambda: "off")
    assert os_shadow.in_play() is False
    monkeypatch.setattr(os_shadow, "mode", lambda: "shadow")
    assert os_shadow.in_play() is True