"""The protocol itself, spoken in raw frames to a kernel in another process.

Every op of the closed table is exercised once here with nothing but a socket, so the wire
format is pinned independently of the Python client that later tests use.
"""
from __future__ import annotations

import pytest
from procs import HELPER, Box, ask, req
from world import ALEX, SAM

from zoe_os import wire


@pytest.fixture
def box():
    b = Box()
    yield b
    b.close()


def ok(sock, op, **args):
    reply = ask(sock, req(op, **args))
    assert reply["ok"] and reply["v"] == 1 and reply["id"] == 1, reply
    return reply["result"]


def test_the_op_table_is_the_whole_surface():
    assert sorted(wire.OPS) == ["act", "explain", "forget", "grant", "poll", "publish", "revoke",
                                "subscribe", "undo"]
    assert wire.VERSION == 1 and wire.MAX_FRAME == 64 * 1024


def test_every_op_round_trips_over_the_socket(box):
    panel, voice, helper = box.raw("panel"), box.raw("voice"), box.raw(HELPER)
    assert ok(panel, "grant", person=SAM, skill=HELPER)["grant_id"].startswith("g")
    sub = ok(helper, "subscribe", kinds=["utterance"])["sub"]
    said = ok(voice, "publish", kind="utterance", subject=SAM, payload={"text": "synthetic words"})
    assert said["source"] == "voice" and said["seq"] > 0          # the kernel names the source
    (got,) = ok(helper, "poll", sub=sub, wait_ms=500, ack=said["seq"])["events"]
    assert got == said
    receipt = ok(helper, "act", action="light.toggle", on_behalf_of=SAM, args={"room": "hall"},
                 caused_by=[said["seq"]])
    assert receipt["verified"] and receipt["skill"] == HELPER
    assert box.sup("state")["lights"] == ["hall"]
    why = ok(helper, "explain", seq=said["seq"])
    assert why["event"] == said and why["causes"] == []
    assert [g["person"] for g in ok(helper, "explain")["grants"]] == [SAM]
    assert ok(panel, "forget", subject=SAM, actor=SAM) == {"count": 2, "upto": receipt["seq"]}
    assert [e["kind"] for e in ok(helper, "poll", sub=sub, max=8)["events"]] == ["forget"]
    assert ok(panel, "revoke", person=SAM, skill=HELPER) == {"revoked": True}
    assert box.audit() == []


def test_a_refusal_crosses_the_socket_as_an_error_and_is_an_event(box):
    helper = box.raw(HELPER)
    reply = ask(helper, req("subscribe", kinds=["utterance"], subject=ALEX))
    assert reply == {"v": 1, "id": 1, "ok": False, "error": {
        "code": "refused",
        "reason": "friendly-helper: read forget,utterance of one person: no grant from that person"}}
    assert box.audit() == [{"who": HELPER, "attempted": "read forget,utterance of one person",
                            "reason": "no grant from that person"}]


def test_nothing_is_served_before_the_handshake_and_the_version_is_checked(box):
    s = box.raw()
    assert ask(s, req("explain"))["error"]["code"] == "unauthenticated"   # a request is not a hello
    assert ask(s, req("explain")) is None
    s = box.raw()
    hello = {"v": 2, "skill": HELPER, "proof": wire.proof(bytes.fromhex(box.creds[HELPER]), box.nonce)}
    assert ask(s, hello)["error"]["code"] == "unsupported_version"
    assert ask(box.raw(HELPER), {**req("explain"), "v": 2})["error"]["code"] == "unsupported_version"
    assert box.reasons() == ["bad credential"] + ["unsupported protocol version"] * 2
