"""The process boundary: the kernel in one process, the skill in another, a socket between.

Part 1 re-runs the malicious skill of ``test_attack.py`` FROM ANOTHER PROCESS: the same class,
the same ten attempts, the same ten recorded refusals, and the same four negative controls
(applied inside the kernel process through the supervisor's pipe).
Part 2 is the honest path. ``test_boundary_attacks.py`` holds the attacks the boundary adds.
"""
from __future__ import annotations

import pytest
from procs import HELPER, Box
from world import ALEX, SAM

from zoe_os.skills.leaving_catch import LeavingCatch
from zoe_os.spine import Refused

VICTIM_SECRET = "zx-victim-secret-amber-lantern-4410"
FORGOTTEN = "zx-forgotten-words-teal-meadow-9056"


@pytest.fixture
def box():
    b = Box()
    yield b
    b.close()


def say(box, person, text):
    return box.client("voice").publish("utterance", subject=person, payload={"text": text})


def set_scene(box):
    """ALEX never enabled the helper. SAM did, said something, and the helper cached it."""
    box.client("panel").grant(SAM, HELPER)
    say(box, ALEX, VICTIM_SECRET)
    box.client("presence").publish("presence", subject=ALEX, visibility="household",
                                   payload={"state": "home", "confidence": 0.95})
    box.cause = say(box, SAM, FORGOTTEN).seq
    bad = box.skill()
    assert FORGOTTEN in str(bad.do("pump")["cache"])
    return bad


@pytest.fixture
def scene(box):
    return box, set_scene(box)


def forget_sam(box) -> int:
    box.client("panel").forget(SAM, actor=SAM)
    return say(box, SAM, "something new and harmless").seq


def test_the_skill_process_is_handed_a_socket_and_a_credential_and_nothing_else(scene):
    box, bad = scene
    assert set(bad.handed) == {"socket", "skill", "cred"}
    assert box.db not in str(bad.handed) and "kernel" not in bad.handed["socket"]
    assert bad.proc.pid != box.pid and bad.handed["cred"] == box.creds[HELPER]
    assert len(set(box.creds.values())) == len(box.creds)          # one credential per skill


def test_attack_1_reading_another_persons_private_events(scene):
    box, bad = scene
    r = bad.do("read_private_of", ALEX)
    assert [e["kind"] for e in r["out"]] == ["presence"]            # household: it may see that
    assert VICTIM_SECRET not in str(r)
    assert box.reasons() == ["no grant from that person"] * 2 + ["asks for more than its manifest"]


def test_attack_2_acting_without_a_grant(scene):
    box, bad = scene
    assert bad.do("act_without_grant", ALEX, box.cause)["out"] == []
    assert box.sup("state")["lights"] == [] and box.sup("state")["alarms"] == {}
    assert box.reasons() == ["no grant from that person", "asks for more than its manifest"]


def test_attack_3_resurrecting_a_forgotten_subject(scene):
    box, bad = scene
    r = bad.do("resurrect", SAM, forget_sam(box))
    assert r["out"] == []
    assert box.reasons() == [
        "derives from a forgotten event", "carries forgotten content", "carries forgotten content",
        "asks for more than its manifest", "kernel-only kind"]
    assert FORGOTTEN.encode() not in box.on_disk()
    assert FORGOTTEN in str(r["cache"])       # honest limit: the copy in its own process


def test_all_ten_attempts_are_refused_and_recorded_with_no_content(scene):
    box, bad = scene
    bad.do("read_private_of", ALEX)
    bad.do("act_without_grant", ALEX, box.cause)
    r = bad.do("resurrect", SAM, forget_sam(box))
    recorded = box.audit()
    assert len(recorded) == len(r["refused"]) == 10
    assert {e["who"] for e in recorded} == {HELPER}
    assert VICTIM_SECRET not in str(recorded) and FORGOTTEN not in str(recorded)


# ---- the same negative controls, applied inside the kernel process ----------------------------
def test_control_without_the_capability_check_private_events_cross_the_socket(scene):
    box, bad = scene
    box.control("no_read_check")
    assert VICTIM_SECRET in str(bad.do("read_private_of", ALEX)["out"])


def test_control_without_the_capability_check_the_action_runs(scene):
    box, bad = scene
    box.control("no_authorize")
    done = bad.do("act_without_grant", ALEX, box.cause)["out"]
    assert [r["action"] for r in done] == ["light.toggle"] and box.sup("state")["lights"] == ["study"]


def test_control_without_the_tombstone_the_forgotten_words_replay(scene):
    box, bad = scene
    box.control("no_tombstone")
    assert FORGOTTEN in str(bad.do("resurrect", SAM, forget_sam(box))["out"])


def test_control_without_the_shield_the_forgotten_words_are_republished(scene):
    box, bad = scene
    box.control("no_shield")
    back = bad.do("resurrect", SAM, forget_sam(box))["out"]
    assert len(back) == 2 and FORGOTTEN in str(back)


# ---- the honest path ---------------------------------------------------------------------------
def test_an_honest_skill_runs_unchanged_on_the_far_side_of_the_socket(box):
    panel = box.client("panel")
    panel.grant(ALEX, "leaving-catch")
    panel.grant(ALEX, "cards")
    box.client("routines").publish("routine_step", subject=ALEX, payload={
        "routine": "take-with-you", "item": "gym bag", "done": False})
    left = box.client("presence").publish("presence", subject=ALEX,
                                          payload={"state": "left", "confidence": 0.95})
    assert box.client("leaving-catch").run(LeavingCatch(), from_seq=0) == 2
    cards = box.client("cards")
    (offer,) = cards.drain(cards.subscribe(from_seq=0))
    assert offer.source == "leaving-catch" and offer.payload["items"] == ["gym bag"]
    assert left.seq in offer.caused_by and box.audit() == []


def test_a_revoked_grant_stops_the_next_delivery_across_the_socket(box):
    panel, helper = box.client("panel"), box.client(HELPER)
    panel.grant(SAM, HELPER)
    sub = helper.subscribe(kinds=["utterance"])
    say(box, SAM, "first")
    say(box, SAM, "second")
    assert [e.payload["text"] for e in helper.poll(sub)] == ["first"]
    assert panel.revoke(SAM, HELPER) == {"revoked": True}
    assert helper.poll(sub, max=8) == []                    # "second" was already queued
    assert [g["change"] for g in box.audit("grant_change")][-2:] == ["granted", "revoked"]


def test_a_forget_through_the_socket_reaches_a_subscriber_in_another_process(box):
    panel, helper = box.client("panel"), box.client(HELPER)
    panel.grant(SAM, HELPER)
    sub = helper.subscribe(kinds=["utterance"])
    say(box, SAM, FORGOTTEN)
    assert panel.forget(SAM, actor=SAM)["count"] == 1
    (notice,) = helper.poll(sub, max=8)
    assert notice.kind.value == "forget" and FORGOTTEN not in str(notice.payload)
    with pytest.raises(Refused, match="not theirs to forget"):
        panel.forget(SAM, actor=ALEX)                       # the surface relays; the kernel decides


def test_explain_tells_a_skill_its_own_standing_and_nothing_about_anyone_else(scene):
    box, bad = scene
    helper = box.client(HELPER)
    with pytest.raises(Refused, match="not an event it may read"):
        helper.explain(seq=1)                               # ALEX's private words
    standing = helper.explain()
    assert standing["protocol"] == 1 and standing["manifest"]["acts"] == ["light.toggle"]
    assert [g["person"] for g in standing["grants"]] == [SAM]
    assert [r["reason"] for r in standing["refusals"]] == ["not an event it may read"]
    mine = helper.explain(seq=box.cause)
    assert mine["event"]["payload"] == {"text": FORGOTTEN} and mine["hidden_causes"] == 0
    assert VICTIM_SECRET not in str(standing) + str(mine)
