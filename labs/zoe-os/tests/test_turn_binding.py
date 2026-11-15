"""Every proposal is bound to the turn it came from (kernel rule 6: no cross-member reach).

The lab runner moves on when a turn's stream times out, but the earlier Flue operation is left
running and can still submit tool calls. Each call carries the id the bridge server minted for
ITS turn (``X-Turn``); one that is not the current turn is refused ``stale_turn`` and never
reaches the bridge, so it cannot act under the next turn's person and cause.

A real kernel process and the real bridge; the HTTP handler is driven without binding a socket.
The people and words are invented.
"""
from __future__ import annotations

import io
import json

import pytest
from brain_rig import BrainBox
from world import ALEX, SAM

from zoe_os.brain import serve

WORD = "zx-turnbind-cobalt-heron-3307"


@pytest.fixture
def rig(monkeypatch):
    class HTTP:                       # capture the handler class; nothing binds or listens
        server_address = ("127.0.0.1", 1)

        def __init__(self, address, handler):
            self.handler = handler

        def serve_forever(self):
            pass

    monkeypatch.setattr(serve, "ThreadingHTTPServer", HTTP)
    box = BrainBox()
    box.household()
    bridge = box.bridge()
    server = serve.BridgeServer(bridge, "synthetic-runner-token")

    def post(path, body, turn=None, token=None):
        h = object.__new__(server.httpd.handler)
        h.path = path
        data = json.dumps(body).encode()
        h.headers = {"Content-Length": str(len(data)), "X-Runner": token or server.token}
        if turn is not None:
            h.headers["X-Turn"] = turn
        h.rfile, h.wfile = io.BytesIO(data), io.BytesIO()
        reply = []
        h._send = lambda code, obj: reply.append((code, obj))
        h.do_POST()
        return reply[0]

    def open_turn(person, text):
        cause = box.say(person, text).cause
        code, body = post("/turn", {"member": person, "cause": cause})
        assert code == 200 and body["ok"]
        return body["turn"], cause

    def propose(turn, item):
        return post("/propose", {"tool": "list_add", "arguments": {"item": item}}, turn=turn,
                    token=server.app_token)

    yield box, server, open_turn, propose
    bridge.close()
    box.close()


def kernel_trail(box):
    audit = box.client("audit")
    return [(e.kind.value, e.subject) for e in audit.drain(
        audit.subscribe(kinds=["action_receipt", "refusal"], from_seq=0))]


def test_a_late_call_from_an_earlier_turn_is_refused_and_writes_for_nobody(rig):
    box, server, open_turn, propose = rig
    alex_turn, alex_cause = open_turn(ALEX, f"add {WORD} to my list")
    sam_turn, _ = open_turn(SAM, "what time is it")           # the runner moved on: a different person
    before = kernel_trail(box)

    code, out = propose(alex_turn, WORD)                     # turn N's operation, still running

    assert code == 200 and out["status"] == "refused" and out["receipt"] is None
    state = box.sup("state")
    assert not state["lists"].get(ALEX) and not state["lists"].get(SAM), "nothing written for either person"
    assert kernel_trail(box) == before, "the call never reached the kernel: no receipt, no driver"
    rec = server.log[-1]
    assert rec == {"turn": alex_cause, "turn_id": alex_turn, "tool": rec["tool"], "status": "refused",
                   "receipt": None, "hop_ms": 0.0, "code": "stale_turn"}
    assert WORD not in json.dumps(server.log)
    # the current turn is untouched by the stale call and still works for its own person
    code, out = propose(sam_turn, "synthetic tea")
    assert out["status"] == "done" and box.sup("state")["lists"].get(SAM)
    assert not box.sup("state")["lists"].get(ALEX)


def test_the_same_call_inside_its_own_turn_acts_for_that_turns_person(rig):
    box, server, open_turn, propose = rig
    alex_turn, alex_cause = open_turn(ALEX, f"add {WORD} to my list")
    code, out = propose(alex_turn, WORD)
    assert code == 200 and out["status"] == "done" and out["receipt"]
    rows = box.sup("state")["lists"]
    assert [r["item"] for r in rows[ALEX]] == [WORD] and rows[ALEX][0]["asked"] == alex_cause
    assert not rows.get(SAM)
    assert server.log[-1]["turn_id"] == alex_turn and "code" not in server.log[-1]


@pytest.mark.parametrize("supplied", [None, "", "turn-1-0000000000000000", "turn-999-made-up"])
def test_a_missing_or_made_up_turn_id_is_stale_and_never_logged_as_text(rig, supplied):
    box, server, open_turn, propose = rig
    open_turn(ALEX, f"add {WORD} to my list")
    code, out = propose(supplied, WORD)
    assert out["status"] == "refused" and server.log[-1]["code"] == "stale_turn"
    assert server.log[-1]["turn_id"] is None and server.log[-1]["turn"] is None
    assert not box.sup("state")["lists"].get(ALEX)


def test_turn_ids_are_never_reused(rig):
    box, server, open_turn, propose = rig
    ids = [server.open_turn(ALEX, 1) for _ in range(50)]
    assert len(set(ids)) == 50
    reopened, _ = open_turn(ALEX, "the same person again")   # same person, new turn: the old id is still stale
    assert reopened not in ids
    assert propose(ids[-1], WORD)[1]["status"] == "refused"


def test_CONTROL_without_the_binding_check_the_late_call_acts_under_the_next_person(rig, monkeypatch):
    """The pre-change rule: whatever turn is open when the call ARRIVES. Turn N's call writes into
    turn N+1's person's list, citing turn N+1's utterance."""
    box, server, open_turn, propose = rig
    alex_turn, _ = open_turn(ALEX, f"add {WORD} to my list")
    _, sam_cause = open_turn(SAM, "what time is it")
    monkeypatch.setattr(serve.BridgeServer, "_bound", lambda self, supplied: (self.turn, self.turn_id))
    code, out = propose(alex_turn, WORD)
    assert out["status"] == "done"
    rows = box.sup("state")["lists"]
    assert [r["item"] for r in rows[SAM]] == [WORD] and rows[SAM][0]["asked"] == sam_cause
