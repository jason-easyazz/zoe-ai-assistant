"""The authority client against the REAL Zoe OS lab kernel (``labs/zoe-os/authority.py`` as a subprocess): the wire cannot drift.

SKIPPED, with a named reason, when the lab authority daemon is not in this tree (it lands on its own branch): the zoe-data side
can land without it, and this test starts proving the contract the day both are present. Test code may read ``labs/``; zoe-data
never imports it. One short kernel process, SQLite in a temp dir, synthetic members and items.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

import os_authority
import os_shadow
import user_erase_gate

REPO = Path(__file__).resolve().parents[3]
DAEMON = REPO / "labs" / "zoe-os" / "authority.py"
pytestmark = [pytest.mark.ci_safe, pytest.mark.skipif(
    not DAEMON.is_file(), reason="the lab authority kernel (labs/zoe-os/authority.py) is not in this tree: the contract is unproven here")]

A, B = "member-a", "member-b"


@pytest.fixture
def real(monkeypatch):
    state = Path(tempfile.mkdtemp(prefix="oac"))
    (state / "people.json").write_text(json.dumps({"people": [A, B]}))
    proc = subprocess.Popen([sys.executable, "-B", str(DAEMON), "--dir", str(state)], stdout=subprocess.PIPE, text=True)
    try:
        assert proc.stdout.readline().startswith("zoe-os authority kernel up")
        monkeypatch.setenv("ZOE_OS_SHADOW_DIR", str(state))
        monkeypatch.setenv("ZOE_OS_LIST_ADD_AUTHORITY", "kernel")
        monkeypatch.setenv("ZOE_OS_LIST_SHADOW", "off")
        os_shadow._reset_for_tests()
        os_authority._reset_for_tests()
        user_erase_gate.reset()
        yield proc
    finally:
        os_authority._reset_for_tests()
        proc.kill()
        proc.wait(timeout=10)


def panel(op, **args):
    path, creds = os_shadow._load_creds()
    conn = os_shadow._Conn(path, "panel", creds["panel"])
    try:
        return conn.call(op, **args)
    finally:
        conn.close()


async def propose(user, item):
    return await os_authority.propose_add(user, list_type="shopping", list_name="Shopping", item=item)


@pytest.mark.asyncio
async def test_the_real_kernel_decides_refuses_for_a_revoke_and_falls_back_on_its_own_limits(real):
    assert await propose(A, "oat milk") == ("kernel", "verified")
    assert await propose(A, "x" * 600) == ("fallback", "refused_params")       # its argument cap: not consent, so the old path adds it
    panel("revoke", person=B, skill="brain")
    assert await propose(B, "rice") == ("refused", "no_grant")                  # the person's own decision: honoured
    assert await propose("member-z", "rice") == ("fallback", "not_in_roster")
    assert os_authority.kernel_digests() == [os_authority.digest("oat milk")]


@pytest.mark.asyncio
async def test_the_spoken_add_end_to_end_lands_in_both_and_the_erases_reach_the_kernel(real):
    from test_os_shadow_hook import _DB
    import list_service
    out = await list_service.add_items_to_list(_DB(), text="oat milk and rice", user_id=A, list_type="shopping", list_name="Shopping",
                                               new_list_visibility="family")
    assert out["added"] == ["oat milk", "rice"] and all(o["kernel"] for o in out["outcomes"])
    assert sorted(os_authority.kernel_digests()) == sorted(os_authority.digest(t) for t in ("oat milk", "rice"))
    assert await os_authority.erase_item("rice") >= 2
    assert os_authority.kernel_digests() == [os_authority.digest("oat milk")]
    assert await os_authority.erase_member(A) >= 2
    assert os_authority.kernel_digests() == []
    assert await os_authority.erase_member(A) == 0                              # idempotent, still verified


@pytest.mark.asyncio
async def test_a_killed_kernel_is_a_fast_fallback_and_a_failed_erase(real):
    real.kill()
    real.wait(timeout=10)
    assert await propose(A, "oat milk") == ("fallback", "unavailable")
    with pytest.raises(os_authority.AuthorityEraseFailed):
        await os_authority.erase_member(A)
