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

os_shadow = pytest.importorskip("os_shadow", reason="the zoe-data shadow client (os_shadow.py) is not in this tree: nothing to hold to the wire")

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
        getattr(os_shadow, "revive", lambda: True)()                  # what zoe-data's start-up does (a turn never starts the worker)
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


# ---- the widened verbs, against the real kernel (os_shadow.note_act and the daemon's four acts) -------------------------------------
needs_verbs = pytest.mark.skipif(not hasattr(os_shadow, "note_act"),
                                 reason="this os_shadow predates the widened verbs (note_act): only list add is in the contract here")


@needs_verbs
def test_a_reminder_an_alarm_and_a_remove_match_against_the_real_kernel(real_kernel, monkeypatch):
    import proactive.triggers.reminder_scan as scan
    from datetime import timedelta

    monkeypatch.setenv("ZOE_OS_SHADOW_VERBS", "list_add,list_remove,reminder_create,alarm_create")
    when = scan.zoe_now() + timedelta(minutes=45)
    os_shadow.note_add(MEMBER, source="spoken", list_type="shopping", list_name="Shopping", item="oat milk")
    os_shadow.note_list_remove(MEMBER, source="skybridge", list_type="shopping", list_name="Shopping", item="oat milk")
    os_shadow.note_reminder_create(MEMBER, source="service", note="call the plumber", due_date=when.date().isoformat(),
                                   due_time=when.strftime("%H:%M"), recurring=False)
    os_shadow.note_alarm_create(MEMBER, source="service", time_hhmm="06:45", repeat="once")
    settle(4)
    assert os_shadow.counters_by_verb() == {v: {"match": 1} for v in ("list_add", "list_remove", "reminder_create", "alarm_create")}
    import asyncio
    assert asyncio.run(os_shadow.forget_user(MEMBER)) >= 8            # four utterances and four receipts, all the member's


@needs_verbs
def test_the_real_kernel_refuses_what_its_schema_cannot_take_and_the_client_names_it(real_kernel, monkeypatch):
    monkeypatch.setenv("ZOE_OS_SHADOW_VERBS", "alarm_create")
    os_shadow.note_alarm_create(MEMBER, source="service", time_hhmm="7:5", repeat="once")      # never sent: the client stops it
    os_shadow.note_alarm_create(MEMBER, source="service", time_hhmm="06:45", repeat="weekdays")
    settle(2)
    assert os_shadow.counters_by_verb() == {"alarm_create": {"not_expressible": 1, "match": 1}}


def test_a_member_taken_off_the_roster_is_still_forgotten_by_the_real_kernel(monkeypatch):
    """The daemon keeps every id it was ever started with, so zoe-data's forget-by-id reaches a member removed from people.json."""
    import asyncio

    state = Path(tempfile.mkdtemp(prefix="osc"))
    monkeypatch.setenv("ZOE_OS_SHADOW_DIR", str(state))
    monkeypatch.setenv("ZOE_OS_LIST_SHADOW", "shadow")

    def start(people):
        (state / "people.json").write_text(json.dumps({"people": people}))
        proc = subprocess.Popen([sys.executable, "-B", str(DAEMON), "--dir", str(state)], stdout=subprocess.PIPE, text=True)
        assert proc.stdout.readline().startswith("zoe-os shadow kernel up")
        os_shadow._reset_for_tests()
        getattr(os_shadow, "revive", lambda: True)()
        return proc

    proc = start([MEMBER, "member-b"])
    try:
        os_shadow.note_add(MEMBER, source="spoken", list_type="shopping", list_name="Shopping", item="oat milk")
        settle(1)
        assert os_shadow.counters() == {"match": 1}
    finally:
        proc.terminate()
        proc.wait(timeout=10)
    proc = start(["member-b"])                                         # MEMBER was taken off the roster
    try:
        assert json.loads((state / "known-people.json").read_text())["people"] == sorted([MEMBER, "member-b"])
        assert asyncio.run(os_shadow.forget_user(MEMBER)) >= 2         # their old record is reached and erased
        assert asyncio.run(os_shadow.forget_user("member-never")) == 0    # a stranger: the kernel never knew them
    finally:
        os_shadow._reset_for_tests()
        proc.terminate()
        proc.wait(timeout=10)
