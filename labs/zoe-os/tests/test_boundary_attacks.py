"""Attacks the process boundary makes possible, each with a negative control.

A forged identity, a replayed credential, oversized and malformed frames, a slow client, a
client that dies mid-request, and opening the database file directly. The last one is the
honest one: the SOCKET does not stop it. File permissions and a second OS user do, and that
can only be shown where the test may become a second user (it skips otherwise).
"""
from __future__ import annotations

import os
import socket
import struct
import sys

import pytest
from procs import HELPER, Box, ask, frame, req, until
from test_boundary import VICTIM_SECRET, say, set_scene
from world import ALEX, SAM

from zoe_os import wire
from zoe_os.client import Client

PUBLISH = req("publish", kind="offer", subject=SAM, payload={"offer_id": "o", "text": "hi"})


@pytest.fixture
def box():
    b = Box()
    yield b
    b.close()


def alive(box) -> bool:
    """The kernel still answers an honest client on a fresh connection."""
    c = Client(box.socket, "audit", bytes.fromhex(box.creds["audit"]))
    try:
        return c.explain()["skill"] == "audit"
    finally:
        c.close()


# ---- forged identity ----------------------------------------------------------------------------
def test_a_skill_cannot_claim_to_be_another_skill(box):
    set_scene(box)
    mine = bytes.fromhex(box.creds[HELPER])
    for claimed in ("voice", "panel", "audit", "kernel", "no-such-skill", "../etc"):
        s = box.raw()
        reply = ask(s, {"v": 1, "skill": claimed, "proof": wire.proof(mine, box.nonce)})
        assert reply["error"]["code"] == "unauthenticated"
        assert ask(s, PUBLISH) is None                       # and the connection is gone
    helper = box.raw(HELPER)                                 # its own name: accepted, and then...
    for extra in ({"source": "voice"}, {"skill": "voice"}, {"reader": "audit"}):
        assert ask(helper, req("publish", **PUBLISH["args"], **extra))["error"]["code"] == "bad_request"
        assert ask(helper, {**PUBLISH, **extra})["error"]["code"] == "bad_request"
    assert box.reasons("unverified-peer") == ["bad credential"] * 6
    assert box.reasons(HELPER) == ["unknown argument", "unknown field"] * 3
    assert "etc" not in str(box.audit())                     # a claimed name is not echoed raw


def test_a_skill_cannot_speak_for_a_person(box):
    set_scene(box)
    helper = box.raw(HELPER)
    for op, args in (("grant", {"person": ALEX, "skill": HELPER}),
                     ("revoke", {"person": SAM, "skill": "cards"}),
                     ("forget", {"subject": ALEX, "actor": ALEX})):
        assert ask(helper, req(op, **args))["error"]["code"] == "refused"
    assert box.reasons(HELPER) == ["only a person, through a trusted surface"] * 3
    assert VICTIM_SECRET.encode() in box.on_disk()           # ALEX's words were not forgotten


def test_control_without_the_credential_check_a_skill_becomes_the_panel_and_reads_everything(box):
    set_scene(box)
    box.control("no_verify")
    s = box.raw()
    assert ask(s, {"v": 1, "skill": "panel", "proof": "00"})["ok"]
    assert ask(s, req("grant", person=ALEX, skill=HELPER))["ok"]
    helper = box.client(HELPER)
    assert VICTIM_SECRET in str(helper.drain(helper.subscribe(from_seq=0)))


# ---- replayed credential -----------------------------------------------------------------------
def captured_handshake(box) -> dict:
    """What an eavesdropper would hold after watching the helper connect once."""
    box.raw(HELPER).close()
    return {"v": 1, "skill": HELPER, "proof": wire.proof(bytes.fromhex(box.creds[HELPER]), box.nonce)}


def test_a_replayed_handshake_and_a_withdrawn_credential_are_both_refused(box):
    set_scene(box)
    old = captured_handshake(box)
    assert ask(box.raw(), old)["error"]["code"] == "unauthenticated"     # a new nonce each time
    stale = box.creds[HELPER]
    fresh = box.sup("reissue", skill=HELPER)["cred"]                     # the kernel rotates it
    s = box.raw()
    reply = ask(s, {"v": 1, "skill": HELPER, "proof": wire.proof(bytes.fromhex(stale), box.nonce)})
    assert reply["error"]["code"] == "unauthenticated"
    box.raw(HELPER, fresh).close()                                       # the new one works
    assert box.reasons("unverified-peer") == ["bad credential"] * 2


def test_control_with_a_predictable_nonce_the_captured_handshake_logs_in(box):
    set_scene(box)
    box.control("fixed_nonce")
    s = box.raw()
    assert ask(s, captured_handshake(box))["ok"]
    assert ask(s, req("explain"))["result"]["skill"] == HELPER


# ---- oversized and malformed frames ------------------------------------------------------------
BIG = "x" * 200_000


def test_an_oversized_frame_is_refused_before_its_body_is_read(box):
    helper = box.raw(HELPER)
    helper.sendall(struct.pack(">I", 1 << 30))               # "a gigabyte follows"; nothing does
    assert wire.read_frame(helper, idle=5)["error"]["code"] == "too_large"
    voice = box.raw("voice")
    assert ask(voice, req("publish", kind="utterance", subject=SAM,
                          payload={"text": BIG}))["error"]["code"] == "too_large"
    assert box.reasons() == ["frame too large"] * 2 and len(box.on_disk()) < len(BIG)
    assert alive(box)


def test_control_without_the_size_cap_the_big_frame_is_stored(box):
    box.control("no_size_cap")
    assert ask(box.raw("voice"), req("publish", kind="utterance", subject=SAM,
                                     payload={"text": BIG}))["ok"]
    assert len(box.on_disk()) > len(BIG)


MALFORMED = [
    b"this is not json", b"[1, 2, 3]", b'"just a string"', b"\xff\xfe\x00", b"[" * 5000,
    {**PUBLISH, "v": 2}, {"v": 1, "id": 1}, {**PUBLISH, "op": "shutdown"}, {**PUBLISH, "op": 7}, {**PUBLISH, "op": ["publish"]},
    {**PUBLISH, "args": "kind=offer"}, req("publish", kind="offer"),
    req("publish", kind="no_such_kind", payload={}), req("publish", kind="offer", payload="text"),
    req("publish", kind="offer", payload={}, caused_by=["one"]),
    req("publish", kind="offer", payload={}, visibility="public"),
    req("publish", kind="offer", payload={}, mentions=[{"a": 1}]),
    req("poll", sub=999), req("poll", sub=True), req("subscribe", kinds=["no_such_kind"]),
    req("act", action="light.toggle", on_behalf_of=SAM, args={}, caused_by="1"),
    req("explain", seq="1"), req("forget", subject=SAM, actor=SAM, everything=True),
]


@pytest.mark.parametrize("bad", MALFORMED, ids=lambda b: str(b)[:40])
def test_a_malformed_frame_is_refused_recorded_and_harms_nothing(box, bad):
    set_scene(box)
    before = len(box.audit())
    s = box.raw(HELPER)
    reply = ask(s, bad)
    assert reply["ok"] is False and reply["error"]["code"] in ("bad_request", "unsupported_version")
    (refusal,) = box.audit()[before:]
    assert refusal["who"] == HELPER and "no_such_kind" not in str(refusal)   # fixed strings only
    assert ask(s, req("explain")) is None if isinstance(bad, bytes) else ask(s, req("explain"))["ok"]
    assert alive(box)


def test_control_without_the_validator_the_closed_protocol_is_open(box):
    set_scene(box)
    box.control("no_validate")
    s = box.raw(HELPER)
    assert ask(s, req("publish", **PUBLISH["args"], caused_by=[box.cause], source="voice"))["ok"]
    assert ask(s, {**PUBLISH, "op": "shutdown"})["error"]["code"] == "internal"
    assert box.audit() == []                                 # and nothing was recorded


# ---- a slow client ------------------------------------------------------------------------------
def stall(box) -> socket.socket:
    s = box.raw(HELPER)
    s.sendall(frame(PUBLISH)[:7])                            # starts a frame, never finishes it
    return s


def test_slow_clients_are_cut_off_and_cannot_hold_every_connection():
    box = Box(max_conns=2, frame_deadline=0.4)
    try:
        slow = [stall(box), stall(box)]                      # both slots are taken
        assert alive(box)                                    # served once the deadline frees one
        assert box.reasons(HELPER) == ["frame deadline"] * 2
        assert all(wire.read_frame(s, idle=2)["error"]["reason"] == "frame deadline" for s in slow)
    finally:
        box.close()


def test_control_without_the_deadline_two_slow_clients_starve_everyone():
    box = Box(max_conns=2, frame_deadline=0.4)
    try:
        box.control("no_deadline")
        slow = [stall(box), stall(box)]
        with pytest.raises(ConnectionError):
            Client(box.socket, "audit", bytes.fromhex(box.creds["audit"]), timeout=1.5)
        assert len(slow) == 2
    finally:
        box.close()


# ---- a client that dies mid-request -------------------------------------------------------------
def test_a_client_that_dies_mid_frame_changes_nothing(box):
    set_scene(box)
    s = box.raw("voice")
    s.sendall(frame(req("publish", kind="utterance", subject=SAM, payload={"text": "half"}))[:-5])
    s.close()
    assert alive(box) and b"half" not in box.on_disk() and box.audit() == []


def test_a_request_that_arrived_whole_runs_once_even_if_the_client_never_reads_the_answer(box):
    set_scene(box)
    s = box.raw(HELPER)
    s.sendall(frame(req("act", action="light.toggle", on_behalf_of=SAM, args={"room": "hall"},
                        caused_by=[box.cause])))
    s.close()                                                # gone before the receipt comes back
    assert until(lambda: box.sup("state")["lights"] == ["hall"])
    helper = box.client(HELPER)                              # the receipt is on the spine anyway
    assert alive(box) and box.sup("state")["lights"] == ["hall"]
    assert helper.explain()["refusals"] == []


def test_a_killed_skill_process_leaves_no_subscription_behind(box):
    bad = set_scene(box)
    idle = box.sup("state")["subs"]
    bad.do("read_private_of", ALEX)                          # opens more subscriptions
    assert box.sup("state")["subs"] > idle
    bad.proc.kill()
    assert until(lambda: box.sup("state")["subs"] == 0) and alive(box)


def test_control_without_the_release_a_dead_clients_subscriptions_pile_up(box):
    bad = set_scene(box)
    box.control("no_release")
    held = box.sup("state")["subs"]
    bad.proc.kill()
    bad.proc.wait()
    assert held > 0 and not until(lambda: box.sup("state")["subs"] < held, timeout=1.0)


# ---- opening the database file directly ---------------------------------------------------------
linux = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="needs /proc")


@linux
def test_HONEST_LIMIT_a_skill_running_as_the_same_user_reads_the_database_around_the_kernel(box):
    """The socket does NOT stop this. The skill was never told where the database is; it asks
    the socket for the kernel's pid, reads /proc, and opens the file. Same user, so it may."""
    bad = set_scene(box)
    found = bad.do("hunt")["out"]
    assert box.db in found["paths"]
    assert VICTIM_SECRET.encode() in bytes.fromhex(found["hex"])


other_user = pytest.mark.skipif(
    not sys.platform.startswith("linux") or os.geteuid() != 0,
    reason="needs root to start the skill as a second OS user; a one-user sandbox cannot prove this")
NOBODY = 65534


@other_user
def test_as_a_second_os_user_the_skill_still_reaches_the_socket_but_not_the_file():
    box = Box(world_readable=True, socket_mode=0o666)
    try:
        say(box, ALEX, VICTIM_SECRET)
        box.client("panel").grant(SAM, HELPER)
        bad = box.skill(user=NOBODY)
        assert bad.do("pump")["out"] == 0                    # the protocol works for it
        assert bad.do("hunt")["out"] == {"error": "PermissionError"}
        assert bad.do("open", box.db)["out"] == {"error": "PermissionError"}
        assert bad.do("open", box.db + "-wal")["out"] == {"error": "PermissionError"}
    finally:
        box.close()


@other_user
def test_control_with_loose_file_permissions_the_second_user_reads_the_database():
    box = Box(world_readable=True, socket_mode=0o666)
    try:
        say(box, ALEX, VICTIM_SECRET)
        bad = box.skill(user=NOBODY)
        os.chmod(os.path.dirname(box.db), 0o755)
        for name in (box.db, box.db + "-wal"):
            os.chmod(name, 0o644)
        got = bad.do("open", box.db, box.db + "-wal")["out"]
        assert VICTIM_SECRET.encode() in bytes.fromhex(got["hex"])
    finally:
        box.close()
