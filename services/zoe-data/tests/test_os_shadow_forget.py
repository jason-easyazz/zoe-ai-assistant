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
    assert await os_shadow.forget_user("someone-else") == 0 and forgets(kernel) == []


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
    assert os_shadow._worker is None and kernel.connections == 0


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
    add()
    settle(1)
    assert [op for _, op, _ in kernel.calls] == ["forget", "publish", "act"]        # the parked forget went first
    assert not (kernel.state / "pending-forgets.json").exists()


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
