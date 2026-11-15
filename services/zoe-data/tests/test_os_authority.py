"""The Zoe OS kernel as the AUTHORITY for list add (os_authority.py): the kernel decides, and every kernel failure is a counted
fallback to the old path that never fails or stalls the turn.

The kernel is the shadow tests' FAKE (v1 socket protocol from its spec, nothing from ``labs/``) plus the authority ops; a hanging
kernel is a fake that sleeps before every answer. The contract against the real lab kernel is ``test_os_authority_contract.py``.
Names and items are invented.
"""
from __future__ import annotations

import json
import logging
import shutil
import tempfile
import time
from pathlib import Path

import pytest

import os_authority
import os_shadow
import user_erase_gate
from test_os_shadow import MEMBER, SVC, FakeKernel
from test_os_shadow_hook import _DB

pytestmark = pytest.mark.ci_safe

ITEM = "oat milk"


class AuthorityFake(FakeKernel):
    def __init__(self):
        super().__init__()
        self.health = {"alive": True, "schema": 1, "last_write_ok": True, "recovered": 0, "rows": 0}
        self.erase_verified = True

    def _answer(self, conn, skill, req):
        op, a = req["op"], req["args"]
        if op == "health":
            return self._ok(conn, req, self.health)
        if op in ("erase_member", "erase_item"):
            return self._ok(conn, req, {"erased": 2, "receipt_seq": 9, "verified": self.erase_verified})
        return super()._answer(conn, skill, req)

    def ops(self):
        return [op for _, op, _ in self.calls]


@pytest.fixture
def ak(monkeypatch):
    k = AuthorityFake()
    state = Path(tempfile.mkdtemp(prefix="osa"))
    (state / "creds.json").write_text(json.dumps({"socket": k.path, "creds": {s: c.hex() for s, c in k.creds.items()}}))
    (state / "people.json").write_text(json.dumps({"people": [MEMBER]}))
    monkeypatch.setenv("ZOE_OS_SHADOW_DIR", str(state))
    monkeypatch.setenv("ZOE_OS_LIST_ADD_AUTHORITY", "kernel")
    monkeypatch.setenv("ZOE_OS_LIST_SHADOW", "shadow")         # both on: a kernel-decided add must not ALSO be shadowed
    os_shadow._reset_for_tests()
    os_authority._reset_for_tests()
    user_erase_gate.reset()
    yield k
    os_authority._reset_for_tests()
    os_shadow._reset_for_tests()
    user_erase_gate.reset()
    k.close()
    shutil.rmtree(state, ignore_errors=True)


async def propose(user=MEMBER, item=ITEM):
    return await os_authority.propose_add(user, list_type="shopping", list_name="Shopping", item=item)


# ---- the decision -----------------------------------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_flag_off_decides_nothing_and_touches_no_socket(ak, monkeypatch):
    monkeypatch.setenv("ZOE_OS_LIST_ADD_AUTHORITY", "off")
    assert await propose() == os_authority.OFF and ak.connections == 0
    monkeypatch.setenv("ZOE_OS_LIST_ADD_AUTHORITY", "on")              # an unlisted value is off, never a guess
    assert await propose() == os_authority.OFF and ak.connections == 0


@pytest.mark.asyncio
async def test_a_verified_receipt_decides_and_health_is_read_once_per_window(ak):
    assert await propose() == ("kernel", "verified")
    assert await propose(item="rice") == ("kernel", "verified")
    assert ak.ops() == ["health", "publish", "act", "publish", "act"]
    assert [a["args"]["item"] for _, op, a in ak.calls if op == "act"] == [ITEM, "rice"]


@pytest.mark.asyncio
async def test_a_real_refusal_is_honoured(ak):
    ak.act_mode = "refuse"                                    # "no grant from that person": the member withdrew the grant
    assert await propose() == ("refused", "no_grant")
    assert os_authority.REAL_REFUSALS == {"no_grant", "forgotten"}


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["derives from a forgotten event", "forgotten_action_arguments", "carries forgotten content"])
async def test_a_forgotten_refusal_is_honoured_and_never_falls_back(ak, reason):
    """Falling back would let the old path write the forgotten text straight back (forgotten means forever)."""
    ak.act_mode = "refuse:" + reason
    assert await propose() == ("refused", "forgotten")
    assert os_authority.refusal_reply("forgotten") == os_authority.FORGOTTEN_REPLY != os_authority.REFUSAL_REPLY
    assert os_shadow.reason_code("forgotten_action_arguments") == "forgotten"      # not "params": "argument" is in the text


@pytest.mark.asyncio
@pytest.mark.parametrize("act_mode,code", [
    ("refuse:wrong argument type", "refused_params"),         # the kernel's own limit (an item over 500 characters)
    ("refuse:asks for more than its manifest", "refused_manifest"),
    ("refuse:cites an event that does not exist", "refused_cause"),
    ("refuse:something new", "refused_other"),
    ("unverified", "unverified"), ("uncertain", "unverified"), ("item_differs", "unverified"), ("wrong_shape", "unverified"),
    ("malformed", "protocol"), ("close", "unavailable"),
])
async def test_every_answer_that_is_not_a_verified_receipt_or_a_real_refusal_falls_back(ak, act_mode, code):
    ak.act_mode = act_mode
    assert await propose() == ("fallback", code)


@pytest.mark.asyncio
@pytest.mark.parametrize("health,code", [({"last_write_ok": False}, "write_failed"), ({"schema": 2}, "unhealthy"),
                                         ({"alive": False}, "unhealthy")])
async def test_an_unhealthy_kernel_is_never_asked_to_act(ak, health, code):
    ak.health.update(health)
    assert await propose() == ("fallback", code) and "act" not in ak.ops()


@pytest.mark.asyncio
async def test_a_hanging_kernel_falls_back_inside_the_budget_and_the_circuit_then_skips_it(ak):
    ak.delay = 3.0                                            # accepts, answers nothing for 3 s
    t0 = time.perf_counter()
    assert await propose() == ("fallback", "timeout")
    assert time.perf_counter() - t0 < os_authority.TIMEOUT_S + 0.1
    t1 = time.perf_counter()
    assert await propose() == ("fallback", "circuit")
    assert time.perf_counter() - t1 < 0.01


@pytest.mark.asyncio
async def test_kernel_down_falls_back(ak):
    ak.srv.close()
    Path(ak.path).unlink()
    assert await propose() == ("fallback", "unavailable")


@pytest.mark.asyncio
async def test_who_the_kernel_cannot_speak_for_falls_back_without_a_socket(ak):
    for who in ("member-z", "guest", "__household__", ""):    # a member added after the roster, a guest, the household
        assert (await propose(user=who)).verdict == "fallback"
    with user_erase_gate.erase_span(MEMBER):
        assert await propose() == ("fallback", "erasing")
    assert ak.connections == 0


@pytest.mark.asyncio
async def test_the_decision_logs_codes_only(ak, caplog):
    caplog.set_level(logging.INFO, logger="os_authority")
    await propose(item="secret recipe word")
    lines = [r.getMessage() for r in caplog.records if "OS_AUTHORITY" in r.getMessage()]
    assert len(lines) == 1 and "secret" not in lines[0] and "result=kernel code=verified" in lines[0]
    assert os_authority.counters() == {"kernel:verified": 1}


# ---- the add path (list_service.add_items_to_list) ---------------------------------------------------------------------------------
async def spoken_add():
    import list_service
    db = _DB()
    out = await list_service.add_items_to_list(db, text="oat milk and rice", user_id=MEMBER, list_type="shopping",
                                               list_name="Shopping", new_list_visibility="family")
    return db.writes, out


@pytest.mark.asyncio
async def test_a_real_refusal_writes_nothing_and_raises_for_the_caller_to_say(ak):
    import list_service
    ak.act_mode = "refuse"
    db = _DB()
    with pytest.raises(os_authority.ListAddRefused):
        await list_service.add_items_to_list(db, text="oat milk and rice", user_id=MEMBER, list_type="shopping",
                                             list_name="Shopping", new_list_visibility="family")
    assert db.writes == []


@pytest.mark.asyncio
async def test_a_hanging_kernel_never_stalls_the_add_and_the_old_path_writes_it_exactly_as_today(ak, monkeypatch):
    monkeypatch.setenv("ZOE_OS_LIST_ADD_AUTHORITY", "off")
    base, base_out = await spoken_add()
    monkeypatch.setenv("ZOE_OS_LIST_ADD_AUTHORITY", "kernel")
    ak.delay = 3.0
    t0 = time.perf_counter()
    writes, out = await spoken_add()
    assert time.perf_counter() - t0 < os_authority.TIMEOUT_S + 0.25       # one timeout, then the circuit: never 3 s
    assert [w[1][1:] for w in writes] == [w[1][1:] for w in base] and out["added"] == base_out["added"] == ["oat milk", "rice"]
    assert os_authority.counters() == {"fallback:timeout": 1, "fallback:circuit": 1}


@pytest.mark.asyncio
async def test_a_kernel_decided_add_writes_the_read_model_and_is_not_shadowed_again(ak):
    writes, out = await spoken_add()
    assert [w[0] for w in writes] == ["INSERT INTO list_items"] * 2 and all(o["kernel"] for o in out["outcomes"])
    time.sleep(0.3)                                            # the shadow worker would have proposed by now
    assert [op for op in ak.ops() if op == "act"] == ["act", "act"] and os_shadow.counters() == {}


def test_the_spoken_callers_speak_the_refusal():
    for name in ("intent_router.py", "mcp_server.py"):
        src = (SVC / name).read_text()
        assert "except os_authority.ListAddRefused" in src and "os_authority.refusal_reply(" in src, name


# ---- erase reach --------------------------------------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_erase_member_is_confirmed_by_the_kernels_read_back_or_fails(ak):
    assert await os_authority.erase_member(MEMBER) == 2
    ak.erase_verified = False
    with pytest.raises(os_authority.AuthorityEraseFailed):
        await os_authority.erase_member(MEMBER)
    ak.srv.close()
    Path(ak.path).unlink()
    os_authority._reset_for_tests()
    with pytest.raises(os_authority.AuthorityEraseFailed):
        await os_authority.erase_member(MEMBER)
    assert not isinstance(os_authority.AuthorityEraseFailed("x"), RuntimeError)


@pytest.mark.asyncio
async def test_erase_with_the_flag_off_touches_nothing(ak, monkeypatch):
    monkeypatch.setenv("ZOE_OS_LIST_ADD_AUTHORITY", "off")
    assert await os_authority.erase_member(MEMBER) == 0 and await os_authority.erase_item(ITEM) == 0 and ak.connections == 0


@pytest.mark.asyncio
async def test_erase_item_sends_a_digest_never_the_words(ak):
    assert await os_authority.erase_item("Oat Milk ") == 2
    (sent,) = [a for _, op, a in ak.calls if op == "erase_item"]
    assert sent == {"digest": os_authority.digest(ITEM)} and ITEM not in json.dumps(ak.calls)


def test_delete_user_calls_the_authority_erase_and_fails_closed_on_it():
    src = (SVC / "memory_service.py").read_text()
    body = src[src.index("    async def delete_user("):]
    body = body[:body.index("\n    async def ", 10)]
    at = body.index("await os_authority.erase_member(user_id)")
    assert "raise MemoryServiceError" in body[at:at + 300]
