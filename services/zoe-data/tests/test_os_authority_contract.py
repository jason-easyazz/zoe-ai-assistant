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
import os_speaker
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
    """A raw panel call. A person-bound op gets the assertion zoe-data would mint for ``person`` (os_speaker): the lab kernel must
    accept zoe-data's token."""
    path, creds = os_shadow._load_creds()
    conn = os_shadow._Conn(path, "panel", creds["panel"])
    try:
        if op == "revoke":
            args["speaker"] = os_speaker.mint(args["person"], "revoke")
        return conn.call(op, **args)
    finally:
        conn.close()


async def propose(user, item):
    async with os_authority.all_or_none():
        return await os_authority.propose_add(user, list_type="shopping", list_name="Shopping", item=item)


def digests():
    rows = os_authority.kernel_digests()
    return rows, sorted(rows.digests)


@pytest.mark.asyncio
async def test_the_real_kernel_decides_refuses_for_a_revoke_and_falls_back_on_its_own_limits(real):
    assert await propose(A, "oat milk") == ("kernel", "verified")
    assert await propose(A, "x" * 600) == ("fallback", "refused_params")       # its argument cap: not consent, so the old path adds it
    panel("revoke", person=B, skill="brain")
    assert await propose(B, "rice") == ("refused", "no_grant")                  # the person's own decision: honoured
    assert await propose("member-z", "rice") == ("fallback", "not_in_roster")
    rows, got = digests()
    assert got == [rows.digest(A, "Shopping", "oat milk")]                      # the kernel's keyed digest, computed by zoe-data


@pytest.mark.asyncio
async def test_the_spoken_add_end_to_end_lands_in_both_and_the_erases_reach_the_kernel(real):
    from test_os_shadow_hook import _DB
    import list_service
    out = await list_service.add_items_to_list(_DB(), text="oat milk and rice", user_id=A, list_type="shopping", list_name="Shopping",
                                               new_list_visibility="family")
    assert out["added"] == ["oat milk", "rice"] and all(o["kernel"] for o in out["outcomes"])
    rows, got = digests()
    assert got == sorted(rows.digest(A, "Shopping", t) for t in ("oat milk", "rice"))
    assert rows.digest(B, "Shopping", "rice") not in got                       # same text, another member: another digest
    assert await os_authority.erase_item(A, "rice") >= 2                       # the panel, with A's forget.scoped assertion
    rows, got = digests()
    assert got == [rows.digest(A, "Shopping", "oat milk")]
    assert await os_authority.erase_member(A) >= 2                              # as the eraser
    assert digests()[1] == []
    assert await os_authority.erase_member(A) == 0                              # idempotent, still verified


@pytest.mark.asyncio
async def test_the_real_kernel_refuses_the_old_paths_and_zoe_data_fails_closed_on_them(real):
    """What the kernel now refuses: a member erase through a surface, an item erase without its member's proof, the digest for
    anyone but the reconciler. zoe-data's own calls are the right ones; a stale identity secret is a REAL refusal."""
    assert await propose(A, "oat milk") == ("kernel", "verified")
    with pytest.raises(os_shadow.KernelRefused):
        panel("erase_member", subject=A)                                        # no assertion: speaker unproven
    with pytest.raises(os_shadow.KernelRefused):
        panel("list_digest")                                                    # only the reconciler
    state = os_shadow.state_dir()
    raw = json.loads((state / "identity-creds.json").read_text())
    raw["creds"]["speaker-verifier"] = "11" * 32                                # a secret the kernel never issued
    path = state / "identity-creds.json"
    path.write_text(json.dumps(raw))
    path.chmod(0o600)
    with pytest.raises(os_authority.AuthorityEraseFailed) as e:
        await os_authority.erase_item(A, "oat milk")
    assert e.value.refused and e.value.code == "refused_unproven"
    assert len(digests()[1]) == 1                                               # nothing was erased on an unproven ask


@pytest.mark.asyncio
async def test_a_later_refusal_leaves_the_real_kernel_holding_none_of_the_add(real):
    """The review's case on the real kernel: the second item is refused for a forgotten word; production rolls back and the item
    the kernel already verified is taken back there (list.remove by the brain)."""
    from test_os_authority import _TxDB
    assert await propose(A, "rice") == ("kernel", "verified")
    assert await os_authority.erase_item(A, "rice") >= 1                        # "forget that": the kernel now shields the words
    assert await propose(B, "tea") == ("kernel", "verified")                    # another member's add still goes through
    assert await os_authority.erase_member(B) >= 1
    db = _TxDB()
    with pytest.raises(os_authority.ListAddRefused):
        await add_many_as(db, A, "oat milk and rice")
    assert db.writes == [] and digests()[1] == [] and os_authority.pending_rollbacks() == []


@pytest.mark.asyncio
async def test_a_killed_kernel_is_a_fast_fallback_and_a_failed_erase(real):
    real.kill()
    real.wait(timeout=10)
    assert await propose(A, "oat milk") == ("fallback", "unavailable")
    with pytest.raises(os_authority.AuthorityEraseFailed):
        await os_authority.erase_member(A)


async def add_many_as(db, member, text):
    import list_service
    return await list_service.add_items_to_list(db, text=text, user_id=member, list_type="shopping", list_name="Shopping",
                                                new_list_visibility="family")
