"""The shadow client against the REAL Zoe OS lab kernel: the wire cannot drift silently.

Runs ``labs/zoe-os/shadow_daemon.py`` as a subprocess (the production entry point, on a temp state dir) and drives ``os_shadow`` against
its socket: a list add matches, a forget removes the kernel's record, a member outside the roster is never proposed. Also pins the
client's constants and every op / argument it sends against ``zoe_os.wire``.

SKIPPED, with a named reason, when ``labs/zoe-os`` is not in this tree (the lab stack is unmerged draft PRs): the zoe-data side can land
without it, and this test starts proving the contract the day both are present. Test code may read ``labs/``; zoe-data never imports it.
Lab only: hand-run, one short kernel process, SQLite in a temp dir.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

import os_shadow

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[3]
LAB = REPO / "labs" / "zoe-os"
DAEMON = LAB / "shadow_daemon.py"
pytestmark = [pytestmark, pytest.mark.skipif(
    not DAEMON.is_file(),
    reason="the lab Zoe OS kernel is not in this tree (labs/zoe-os/shadow_daemon.py): the shadow wire contract is unproven here")]

MEMBER = "member-a"


@pytest.fixture
def real_kernel(monkeypatch):
    state = Path(tempfile.mkdtemp(prefix="osc"))
    (state / "people.json").write_text(json.dumps({"people": [MEMBER, "member-b"]}))
    proc = subprocess.Popen([sys.executable, "-B", str(DAEMON), "--dir", str(state)], stdout=subprocess.PIPE, text=True)
    try:
        assert proc.stdout.readline().startswith("zoe-os shadow kernel up")
        monkeypatch.setenv("ZOE_OS_SHADOW_DIR", str(state))
        monkeypatch.setenv("ZOE_OS_LIST_SHADOW", "shadow")
        os_shadow._reset_for_tests()
        yield state
    finally:
        os_shadow._reset_for_tests()
        proc.terminate()
        proc.wait(timeout=10)


def settle(total, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if sum(os_shadow.counters().values()) >= total:
            return
        time.sleep(0.02)
    raise AssertionError(f"counters never reached {total}: {os_shadow.counters()}")


def test_the_client_constants_and_ops_agree_with_the_kernels_wire_module():
    sys.path.insert(0, str(LAB))
    try:
        from zoe_os import wire
    finally:
        sys.path.remove(str(LAB))
    assert os_shadow.WIRE_VERSION == wire.VERSION
    assert os_shadow.MAX_REPLY <= wire.MAX_REPLY
    sent = {"publish": {"kind", "subject", "payload"}, "act": {"action", "on_behalf_of", "args", "caused_by"},
            "forget": {"subject", "actor", "entity", "topic"}}
    for op, names in sent.items():
        assert op in wire.OPS and names <= set(wire.OPS[op]), op
        assert wire.check_args(op, {k: v for k, v in {"kind": "utterance", "subject": "s", "payload": {}, "action": "a", "on_behalf_of": "p",
                                                      "args": {}, "caused_by": [1], "actor": "p"}.items() if k in wire.OPS[op]}) == ""


def test_a_real_list_add_matches_and_the_forget_removes_the_kernels_record(real_kernel):
    os_shadow.note_add(MEMBER, source="spoken", list_type="shopping", list_name="Shopping", item="oat milk")
    settle(1)
    assert os_shadow.counters() == {"match": 1}                       # event + verified receipt + read-back, over the real socket
    import asyncio
    assert asyncio.run(os_shadow.forget_user(MEMBER)) >= 2           # the utterance and its receipt (and what derives from them)
    os_shadow.note_add(MEMBER, source="spoken", list_type="shopping", list_name="Shopping", item="rice")
    settle(2)
    assert os_shadow.counters() == {"match": 2}                       # the kernel is still serving after a forget


def test_the_real_kernel_refuses_a_member_it_has_no_grant_from_and_the_client_names_it(real_kernel):
    # a roster member the daemon granted works; one NOT in people.json is stopped by the client before the socket
    os_shadow.note_add("member-z", source="spoken", list_type="shopping", list_name="Shopping", item="x")
    settle(1)
    assert os_shadow.counters() == {"member_not_in_roster": 1}


def test_an_oversized_item_is_a_named_refusal_not_a_crash(real_kernel):
    os_shadow.note_add(MEMBER, source="spoken", list_type="shopping", list_name="Shopping", item="x" * 600)
    settle(1)
    assert os_shadow.counters() == {"kernel_refused_live_did": 1}       # the kernel's own argument check (wrong argument type)
