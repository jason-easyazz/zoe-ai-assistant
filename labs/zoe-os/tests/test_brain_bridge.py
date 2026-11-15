"""The brain proposes, the kernel disposes: the bridge, end to end, over the real socket.

Every test runs a KERNEL PROCESS (``brain_proc.py``: database, drivers, stores) and the brain's
adapter in THIS process holding only a socket path and a credential. The people are invented.
The driver here is a scripted or recorded stand-in for the model: nothing in this file proves
anything about what a real model chooses to call.
"""
from __future__ import annotations

import glob
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from brain_rig import HERE, BrainBox
from world import ALEX, KID, SAM

from zoe_os.brain.bridge import Turn
from zoe_os.brain.drive import ScriptedAgent, parse_stream, replay_ndjson
from zoe_os.spine import Refused
from zoe_os.wire import WireError

SENTINEL = "zx-sentinel-amber-orchard-5512"


@pytest.fixture
def box():
    b = BrainBox()
    b.household()
    yield b
    b.close()


@pytest.fixture
def bridge(box):
    br = box.bridge()
    yield br
    br.close()


def refusals(box) -> list[str]:
    c = box.client("audit")
    return [e.payload["reason"] for e in c.drain(c.subscribe(kinds=["refusal"], from_seq=0))]


def on_disk(box) -> bytes:
    return b"".join(open(p, "rb").read() for p in glob.glob(str(box.base / "kernel" / "zoe-os.db*")))


# ---- the tool list is derived, not written ---------------------------------------------------------
def test_tools_come_from_the_kernels_registry_and_hide_what_the_turn_supplies(bridge):
    tools = {t["function"]["name"]: t["function"] for t in bridge.tools()}
    assert sorted(tools) == ["alarm_set", "list_add", "remind_set"]
    # `alarm.move` and `light.toggle` are registered in the kernel but not in the brain's manifest
    # (and declared no params): they are not tools.
    assert tools["list_add"]["parameters"] == {
        "type": "object", "additionalProperties": False, "required": ["item"],
        "properties": {"item": {"type": "string", "description": "what to add"},
                       "list": {"type": "string", "description": "which list; the shopping list if not said"}}}
    for fn in tools.values():
        assert "asked" not in fn["parameters"]["properties"] and "member" not in fn["parameters"]["properties"]


# ---- the whole honest loop, for three skills ---------------------------------------------------------
CASES = [("list_add", {"item": SENTINEL + " milk"}, "list.add", "lists"),
         ("remind_set", {"note": SENTINEL + " call back", "in_minutes": 5}, "remind.set", "reminders"),
         ("alarm_set", {"time": "06:30"}, "alarm.set", "alarms")]


@pytest.mark.parametrize("tool,args,action,store", CASES)
def test_propose_act_receipt_explain_undo(box, bridge, tool, args, action, store):
    turn = box.say(ALEX, f"please {tool} {SENTINEL}")
    out = bridge.propose(turn, tool, args)
    assert out.status == "done" and out.receipt and f"Receipt r{out.receipt}" in out.say
    assert box.sup("state")[store].get(ALEX), "the kernel performed it"
    panel = box.client("panel")
    why = panel.explain(person=ALEX, last="action")
    assert why["seq"] == out.receipt and why["what"]["payload"]["verified"] is True
    assert why["sentence"].startswith(f"brain did {action} for alex with no yes needed")
    assert "the read-back confirmed it" in why["sentence"] and f"utterance #{turn.cause}" in why["sentence"]
    undone = panel.undo(ALEX, actor=ALEX)
    assert undone.verified and not box.sup("state")[store].get(ALEX)
    with pytest.raises(Refused):
        panel.undo(ALEX, actor=ALEX)                          # once only


@pytest.mark.parametrize("tool,args,action,store", CASES)
def test_forgetting_the_member_removes_the_event_the_receipt_and_the_effect(box, bridge, tool, args, action, store):
    mine, hers = box.say(ALEX, SENTINEL), box.say(SAM, "sam's own words")
    out = bridge.propose(mine, tool, args)
    bridge.propose(hers, "list_add", {"item": "sam's eggs"})
    assert out.status == "done" and SENTINEL.encode() in on_disk(box) and box.sup("state")[store].get(ALEX)
    box.client("panel").forget(ALEX, actor=ALEX)
    box.sup("pump")                                           # the kernel's keeper erases the stores
    state = box.sup("state")
    assert ALEX not in state[store] and ALEX not in state["lists"]
    assert SAM in state["lists"], "another person's rows stay"
    assert SENTINEL.encode() not in on_disk(box)
    with pytest.raises(Refused):
        box.client("panel").explain(out.receipt, person=ALEX)  # the receipt is gone too


# ---- three ways to drive it (no model: scripted, recorded) ---------------------------------------------
def test_a_scripted_agent_runs_the_whole_loop(box, bridge):
    agent = ScriptedAgent(bridge)
    for who, text, store in [(ALEX, "add oat milk to the shopping list", "lists"),
                             (SAM, "remind me to stretch in 20 minutes", "reminders"),
                             (ALEX, "set my alarm for 06:45", "alarms")]:
        outcomes, reply = agent.turn(box.say(who, text), text)
        assert [o.status for o in outcomes] == ["done"] and reply.startswith("Done:")
        assert box.sup("state")[store][who]
    outcomes, reply = agent.turn(box.say(ALEX, "what a lovely day"), "what a lovely day")
    assert outcomes == [] and reply == "I did not do anything."


def test_a_recorded_turn_replays_into_the_same_kernel_actions(box, bridge):
    frames = [json.dumps("Adding that. "),
              json.dumps('__TOOL__:{"phase": "start", "id": "c1", "name": "list_add"}'),
              json.dumps('__TOOL__:{"phase": "args", "id": "c1", "name": "list_add", "args": {"item": "bread"}}'),
              json.dumps('__TOOL__:{"phase": "result", "id": "c1", "result": "ok (old tool wrote a store)"}'),
              json.dumps("__THINKING__:hm")]
    assert parse_stream(frames) == [("list_add", {"item": "bread"})]
    (out,) = replay_ndjson(bridge, box.say(SAM, "add bread"), frames)
    assert out.status == "done" and box.sup("state")["lists"][SAM][0]["item"] == "bread"
    assert "ok (old tool" not in out.say, "the agent sees the kernel's summary, not the old result"


# ---- refusals: what the agent is told, and that nothing changed ---------------------------------------
def test_a_member_may_not_set_alarms_the_kernel_refuses_and_the_agent_can_say_so(box, bridge):
    out = bridge.propose(box.say(KID, "set my alarm for 05:00"), "alarm_set", {"time": "05:00"})
    assert (out.status, out.say) == ("refused", "I am not permitted to do that for you. Nothing changed.")
    assert box.sup("state")["alarms"] == {} and refusals(box) == ["no grant from that person"]
    assert bridge.propose(box.say(KID, "add apples"), "list_add", {"item": "apples"}).status == "done"


def test_a_prompt_injected_call_for_another_member_changes_nothing(box):
    turn = box.say(ALEX, "add eggs to the shopping list for sam")
    # 1. the model's call carries a `member`: the adapter's schema has none
    strict = box.bridge()
    out = ScriptedAgent(strict).turn(turn, "add eggs to the shopping list for sam")[0][0]
    assert out.status == "invalid" and refusals(box) == [] and box.sup("state")["lists"] == {}
    # 2. the same call with the adapter's check off: the KERNEL refuses the argument
    loose = box.bridge(prevalidate=False)
    assert ScriptedAgent(loose).turn(turn, "add eggs to the shopping list for sam")[0][0].status == "refused"
    assert refusals(box) == ["unknown argument"] and box.sup("state")["lists"] == {}
    # 3. a compromised adapter that forges on_behalf_of: both people granted the brain, but the cause
    #    is alex's utterance, so the kernel refuses to let it speak for sam
    forged = box.client("brain")
    with pytest.raises(Refused, match="not about that person"):
        forged.act("list.add", SAM, {"item": "eggs", "asked": turn.cause}, caused_by=[turn.cause])
    assert box.sup("state")["lists"] == {}
    # 4. and it cannot cite sam's utterance either: alex's grant... the cause is not alex's to cite
    sams = box.say(SAM, "sam talking")
    with pytest.raises(Refused):
        forged.act("list.add", ALEX, {"item": "eggs", "asked": sams.cause}, caused_by=[sams.cause])
    assert box.sup("state")["lists"] == {}
    strict.close()
    loose.close()


@pytest.mark.parametrize("tool,args", [("list_add", {}), ("list_add", {"item": 5}),
                                       ("remind_set", {"note": "x", "in_minutes": "soon"}),
                                       ("alarm_set", {"time": "0630abc"}), ("list_add", {"item": "x", "asked": 1}),
                                       ("light_toggle", {"room": "hall"})])
def test_a_malformed_proposal_changes_nothing_with_or_without_the_adapters_check(box, tool, args):
    for prevalidate in (True, False):
        br = box.bridge(prevalidate=prevalidate)
        out = br.propose(box.say(ALEX, "x"), tool, args)
        assert out.status in ("invalid", "refused") and "Nothing changed" in out.say or out.status == "invalid"
        br.close()
    assert box.sup("state") == {"lists": {}, "reminders": {}, "alarms": {}}


def test_alarm_time_longer_than_declared_is_refused_by_the_kernel(box):
    br = box.bridge(prevalidate=False)
    out = br.propose(box.say(ALEX, "x"), "alarm_set", {"time": "06:30:00"})
    assert out.status == "refused" and box.sup("state")["alarms"] == {}
    br.close()


def test_the_brains_manifest_is_a_ceiling_it_cannot_raise(box):
    turn = box.say(ALEX, "move my alarm")
    with pytest.raises(Refused, match="asks for more than its manifest"):
        box.client("brain").act("alarm.move", ALEX, {"to": "05:00"}, caused_by=[turn.cause])


# ---- the kernel is not there --------------------------------------------------------------------------
def test_kernel_down_before_the_call_the_brain_says_so_and_nothing_is_half_written(box):
    turn = box.say(ALEX, "add milk")
    box.kill()
    br = box.bridge()
    out = br.propose(turn, "list_add", {"item": "milk"})
    assert out.status == "unavailable" and "Nothing changed" in out.say
    box.restart()
    assert box.sup("state") == {"lists": {}, "reminders": {}, "alarms": {}} and box.recovered == []


def test_kernel_lost_on_an_open_connection_is_unknown_not_done(box, bridge):
    assert bridge.propose(box.say(ALEX, "one"), "list_add", {"item": "a"}).status == "done"
    turn = box.say(ALEX, "two")
    box.kill()
    out = bridge.propose(turn, "list_add", {"item": "b"})
    assert out.status == "unknown" and "could not confirm" in out.say and "done" not in out.say.lower()


def test_a_kernel_killed_between_the_write_and_the_receipt_is_uncertain_never_done(box, bridge):
    turn = box.say(ALEX, "add milk")
    bridge.tools()
    box.sup("arm_die") if False else None
    box.proc.stdin.write(json.dumps({"cmd": "arm_die"}) + "\n")
    box.proc.stdin.flush()
    box.proc.stdout.readline()
    out = bridge.propose(turn, "list_add", {"item": "milk"})
    assert out.status == "unknown"
    box.proc.wait()
    box.start()
    assert len(box.recovered) == 1                             # one intent left open: now `uncertain`
    box.household()
    why = box.client("panel").explain(person=ALEX, last="action")
    assert "the outcome is uncertain" in why["sentence"] and why["what"]["payload"]["verified"] is False
    with pytest.raises(Refused, match="never confirmed"):
        box.client("panel").undo(ALEX, actor=ALEX)


# ---- the brain process holds no store handle and no credential beyond its own ---------------------------
def test_the_brain_process_is_handed_a_socket_and_a_credential_and_holds_nothing_of_the_kernel(box):
    turn = box.say(ALEX, "add milk")
    with tempfile.TemporaryDirectory(prefix="zbc-") as cwd:
        child = subprocess.run(
            [sys.executable, "-I", "-B", str(HERE / "brain_child.py")], cwd=cwd, text=True, capture_output=True,
            timeout=30, env={"PATH": "/usr/bin:/bin"},
            input=json.dumps({"socket": box.socket, "cred": box.creds["brain"], "member": ALEX,
                              "cause": turn.cause, "item": "milk"}) + "\n")
    seen = json.loads(child.stdout)
    assert seen["status"] == "done" and seen["tools"] == ["alarm_set", "list_add", "remind_set"]
    assert seen["handed"] == ["cause", "cred", "item", "member", "socket"]
    kernel_side = {"zoe_os.kernel", "zoe_os.server", "zoe_os.capability", "zoe_os.brain.drivers", "world"}
    assert not kernel_side & set(seen["modules"]), "no kernel, no server, no enforcer, no store code"
    assert seen["file_fds"] == [] and seen["kernel_db_visible_by_handed_path"] == []
    assert not (box.base / "kernel").as_posix() in json.dumps(seen)


def test_HONEST_LIMIT_a_brain_running_as_the_kernels_user_can_still_find_the_database(box):
    """Same limit as the skill boundary (README): the socket is not the whole boundary. A brain
    process under the SAME OS user can read the kernel's /proc entries. Only a second OS user (and
    file modes) stops that: shown in a root sandbox elsewhere, not here."""
    links = [Path(p).resolve() for p in glob.glob(f"/proc/{box.proc.pid}/fd/*")]
    assert any(str(p).endswith("zoe-os.db") for p in links)
