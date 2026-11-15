"""Durable receipts: what survives when the kernel process is KILLED, and what it then admits.

The kernel runs in its own process (``procs.Box``); the "device" it writes to is a file, so the
test can see what really reached the outside world. A driver hook kills the kernel with
SIGKILL at one exact point: before the write, or after the write and before the receipt.

* a receipt survives a restart; so does the fact that a yes was used;
* a claim with no verified receipt is still found after a restart, once, not twice;
* a kill between "write sent" and "receipt stored" is reported as UNCERTAIN: never as done,
  and never retried.
"""
from __future__ import annotations

import pytest
from procs import HELPER, Box, until
from world import SAM

from zoe_os.spine import Refused

ROOM = {"room": "hall"}


@pytest.fixture
def box():
    b = Box()
    yield b
    b.close()


def grants(box):
    """Grants live in memory: after a restart everything is denied until a person grants again."""
    for skill in (HELPER, "mover", "cards"):
        box.client("panel").grant(SAM, skill)
    box.client("panel").grant("alex", "audit")


def said(box) -> int:
    """A live first-hand event the mover skill may read and so may cite."""
    return box.client("calendar").publish("calendar_change", subject=SAM, payload={
        "event_id": "e0", "change": "created"}).seq


def a_yes(box) -> int:
    """SAM's accepted offer from the mover skill: the cause a consequential action needs."""
    cal = box.client("calendar").publish("calendar_change", subject=SAM,
                                         payload={"event_id": "e1", "change": "created"})
    offer = box.client("mover").publish("offer", subject=SAM, caused_by=[cal.seq],
                                        payload={"offer_id": "o1", "text": "move the alarm?"})
    return box.client("panel").publish("offer_response", subject=SAM, caused_by=[offer.seq],
                                       payload={"offer_id": "o1", "response": "accepted"}).seq


def receipts(box) -> list[dict]:
    c = box.client("mover")
    return [e.payload | {"seq": e.seq} for e in c.drain(c.subscribe(kinds=["action_receipt"], from_seq=0))
            if e.kind.value == "action_receipt"]


def killed_during(box, die: str, act) -> None:
    """Arm the driver, run ``act``, and watch the kernel process die inside it."""
    box.sup("arm", die=die)
    with pytest.raises((ConnectionError, OSError)):
        act()
    assert until(lambda: box.proc.poll() is not None) and box.proc.returncode == -9


# ---- a receipt, a used yes and the auditor's place all survive a restart --------------------------
def test_a_verified_receipt_and_the_used_yes_survive_a_kill(box):
    grants(box)
    yes = a_yes(box)
    receipt = box.client("mover").act("alarm.move", SAM, {"to": "05:45"}, caused_by=[yes])
    assert receipt.verified and receipt.outcome == "verified"
    box.restart()                                            # SIGKILL, then a new process
    grants(box)
    assert box.recovered == []
    (stored,) = receipts(box)
    assert stored["seq"] == receipt.seq and stored["verified"] and stored["grant_id"] == receipt.grant_id
    with pytest.raises(Refused, match="already used"):
        box.client("mover").act("alarm.move", SAM, {"to": "03:00"}, caused_by=[yes])
    assert box.written() == [[SAM, {"to": "05:45"}]]
    assert box.client("panel").grant(SAM, "leaving-catch")["grant_id"] not in (
        receipt.grant_id, "g1")                              # grant ids do not start over


def test_control_if_the_used_yes_were_only_in_memory_one_yes_buys_two_actions(box):
    grants(box)
    yes = a_yes(box)
    box.client("mover").act("alarm.move", SAM, {"to": "05:45"}, caused_by=[yes])
    box.restart("yes_not_durable")
    grants(box)
    assert box.client("mover").act("alarm.move", SAM, {"to": "03:00"}, caused_by=[yes]).verified
    assert len(box.written()) == 2


def test_a_claim_with_no_verified_receipt_is_found_after_a_restart_exactly_once(box):
    grants(box)
    mover = box.client("mover")
    good = mover.act("light.toggle", SAM, ROOM, caused_by=[said(box)])
    backed = mover.publish("claim", subject=SAM, caused_by=[good.seq],
                           payload={"action": "light.toggle", "receipt_seq": good.seq})
    bare = mover.publish("claim", subject=SAM, caused_by=[good.seq], payload={"action": "alarm.move"})
    box.restart()                                            # killed before any scan ran
    assert box.sup("scan")["unbacked"] == [bare.seq]
    assert backed.seq not in box.sup("scan")["unbacked"]
    box.restart()
    assert box.sup("scan")["unbacked"] == []                 # judged once: the cursor is durable
    grants(box)
    assert [f["claim_seq"] for f in box.audit("audit_finding")] == [bare.seq]


def test_control_a_trusting_auditor_finds_nothing_after_the_restart(box):
    grants(box)
    box.client("mover").publish("claim", subject=SAM, caused_by=[said(box)],
                                payload={"action": "alarm.move"})
    box.restart("trusting_auditor")
    assert box.sup("scan")["unbacked"] == []


# ---- the crash window: write sent, receipt not stored ---------------------------------------------
@pytest.mark.parametrize("die, landed", [("after_write", [[SAM, ROOM]]), ("before_write", [])])
def test_a_kill_inside_the_action_is_reported_as_uncertain_never_done_never_retried(box, die, landed):
    grants(box)
    cause = said(box)
    killed_during(box, die, lambda: box.client("mover").act("light.toggle", SAM, ROOM, caused_by=[cause]))
    assert box.written() == landed                           # what really happened, from the file
    box.restart()
    grants(box)
    (receipt,) = receipts(box)                               # written by recovery, at startup
    assert box.recovered == [receipt["seq"]]
    assert receipt["outcome"] == "uncertain" and receipt["verified"] is False and receipt["proof"] is None
    assert box.written() == landed                           # NOT retried: the same, not one more
    (finding,) = box.audit("audit_finding")
    assert finding == {"finding": "action_outcome_uncertain", "who": "mover",
                       "action": "light.toggle", "receipt_seq": receipt["seq"]}
    claim = box.client("mover").publish("claim", subject=SAM, caused_by=[receipt["seq"]], payload={
        "action": "light.toggle", "receipt_seq": receipt["seq"]})
    assert box.sup("scan")["unbacked"] == [claim.seq]        # "I did it" on an uncertain receipt
    box.restart()
    assert box.recovered == []                               # recovered once, not at every start


def test_the_yes_spent_on_an_uncertain_action_is_not_silently_reusable(box):
    grants(box)
    yes = a_yes(box)
    killed_during(box, "after_write", lambda: box.client("mover").act(
        "alarm.move", SAM, {"to": "05:45"}, caused_by=[yes]))
    box.restart()
    grants(box)
    with pytest.raises(Refused, match="already used"):       # ask the person again; do not guess
        box.client("mover").act("alarm.move", SAM, {"to": "05:45"}, caused_by=[yes])
    assert box.written() == [[SAM, {"to": "05:45"}]] and receipts(box)[0]["outcome"] == "uncertain"


def test_control_without_the_write_ahead_intent_the_write_leaves_no_trace(box):
    grants(box)
    box.control("no_intent")
    cause = said(box)
    killed_during(box, "after_write",
                  lambda: box.client("mover").act("light.toggle", SAM, ROOM, caused_by=[cause]))
    box.restart()
    grants(box)
    assert box.written() == [[SAM, ROOM]]                    # the light WAS toggled...
    assert receipts(box) == [] and box.recovered == [] and box.audit("audit_finding") == []


def test_control_a_recovery_that_calls_it_done_lets_a_false_claim_through(box):
    grants(box)
    cause = said(box)
    killed_during(box, "before_write",
                  lambda: box.client("mover").act("light.toggle", SAM, ROOM, caused_by=[cause]))
    box.restart("optimistic_recovery")
    grants(box)
    (receipt,) = receipts(box)
    box.client("mover").publish("claim", subject=SAM, caused_by=[receipt["seq"]], payload={
        "action": "light.toggle", "receipt_seq": receipt["seq"]})
    assert box.written() == [] and box.sup("scan")["unbacked"] == []      # nothing happened; "done"
