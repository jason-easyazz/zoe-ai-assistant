"""Receipts: no action without a grant and the person's yes; no claim without proof."""
from __future__ import annotations

import pytest

from zoe_os.capability import Enforcer, GrantStore, Person
from zoe_os.events import Event, Kind
from zoe_os.receipts import ClaimAuditor, Dispatcher
from zoe_os.spine import Decision, Refused, Spine

P, Q = "person-p", "person-q"
PANEL = {"skill": "panel", "emits": ["calendar_change", "offer_response"], "origin": True}
SKILL = {"skill": "mover", "reads": {"calendar_change": "private", "offer_response": "private",
                                     "action_receipt": "private"},
         "emits": ["offer", "claim"], "acts": ["alarm.move", "light.toggle"]}
AUDIT = {"skill": "audit", "reads": {"refusal": "kernel", "audit_finding": "kernel"}}


class Alarms:
    """A stand-in driver. ``broken`` models a write that reports success and did nothing."""

    def __init__(self):
        self.at: dict[str, str] = {}
        self.broken = False

    def do(self, person, args):
        if not self.broken:
            self.at[person] = args["to"]
        return {"ok": True}                      # what the driver SAYS

    def verify(self, person, args, result):
        return {"alarm": self.at[person]} if self.at.get(person) == args["to"] else None


@pytest.fixture
def world(tmp_path):
    store = GrantStore([Person(P), Person(Q)])
    for m in (PANEL, SKILL, AUDIT):
        store.install(m)
    store.grant(P, "mover")
    store.grant(P, "audit")
    pep = Enforcer(store)
    spine = Spine(str(tmp_path / "s.db"), pep)
    alarms, actions = Alarms(), Dispatcher(spine, pep)
    actions.register("alarm.move", alarms.do, alarms.verify)
    actions.register("light.toggle", lambda p, a: True, lambda p, a, r: {"on": True},
                     needs_accept=False)
    yield spine, actions, alarms
    spine.close()


def offer_and_answer(spine, person=P, response="accepted", by="mover"):
    cal = spine.publish(Event(Kind.CALENDAR_CHANGE, "panel", subject=person,
                              payload={"event_id": "e1", "change": "created"}))
    offer = spine.publish(Event(Kind.OFFER, by, subject=person, caused_by=(cal.seq,),
                                payload={"offer_id": "o1", "text": "move the alarm?"}))
    return spine.publish(Event(Kind.OFFER_RESPONSE, "panel", subject=person,
                               caused_by=(offer.seq,),
                               payload={"offer_id": "o1", "response": response}))


def refusals(spine):
    return [e.payload["reason"] for e in spine.replay("audit") if e.kind is Kind.REFUSAL]


def test_an_accepted_offer_becomes_an_action_with_a_verified_receipt(world):
    spine, actions, alarms = world
    yes = offer_and_answer(spine)
    receipt = actions.act("mover", "alarm.move", P, {"to": "05:45"}, caused_by=(yes.seq,))
    assert alarms.at[P] == "05:45"
    assert receipt.verified and receipt.proof == {"alarm": "05:45"}
    assert receipt.skill == "mover" and receipt.grant_id.startswith("g")
    stored = spine._get(receipt.seq)
    assert stored.kind is Kind.ACTION_RECEIPT and stored.subject == P
    assert stored.caused_by == (yes.seq,) and "05:45" not in str(stored.payload["args_digest"])


def test_no_yes_no_action(world):
    spine, actions, alarms = world
    no = offer_and_answer(spine, response="declined")
    cal = spine.publish(Event(Kind.CALENDAR_CHANGE, "panel", subject=P,
                              payload={"event_id": "e2", "change": "created"}))
    for cause in ((no.seq,), (cal.seq,), ()):
        with pytest.raises(Refused):
            actions.act("mover", "alarm.move", P, {"to": "05:45"}, caused_by=cause)
    assert alarms.at == {}
    assert refusals(spine) == ["the person has not accepted an offer for this"] * 2 + [
        "an action must cite the event that asked for it"]


def test_one_yes_is_one_action(world):
    spine, actions, alarms = world
    yes = offer_and_answer(spine)
    actions.act("mover", "alarm.move", P, {"to": "05:45"}, caused_by=(yes.seq,))
    with pytest.raises(Refused, match="already used"):
        actions.act("mover", "alarm.move", P, {"to": "03:00"}, caused_by=(yes.seq,))
    assert alarms.at[P] == "05:45"


def test_a_skill_cannot_forge_the_persons_yes(world):
    spine, actions, alarms = world
    with pytest.raises(Refused, match="more than its manifest"):     # it cannot emit a response
        offer_and_answer(spine, by="mover") and spine.publish(Event(
            Kind.OFFER_RESPONSE, "mover", subject=P, caused_by=(1,),
            payload={"offer_id": "o1", "response": "accepted"}))


def test_someone_elses_yes_does_not_count(world):
    spine, actions, alarms = world
    spine.authorizer.store.grant(Q, "mover")
    yes_q = offer_and_answer(spine, person=Q)
    with pytest.raises(Refused):
        actions.act("mover", "alarm.move", P, {"to": "05:45"}, caused_by=(yes_q.seq,))
    assert P not in alarms.at


def test_no_grant_no_action_and_the_refusal_is_recorded(world):
    spine, actions, alarms = world
    yes = offer_and_answer(spine)
    with pytest.raises(Refused, match="no grant from that person"):
        actions.act("mover", "light.toggle", Q, {}, caused_by=(yes.seq,))
    with pytest.raises(Refused, match="more than its manifest"):
        actions.act("mover", "door.unlock", P, {}, caused_by=(yes.seq,))
    assert len(refusals(spine)) == 2


def test_negative_control_without_the_capability_check_the_action_runs(world, monkeypatch):
    spine, actions, _ = world
    yes = offer_and_answer(spine)
    monkeypatch.setattr(Dispatcher, "_authorize", lambda self, s, a, p: Decision(True, "", "none"))
    assert actions.act("mover", "light.toggle", Q, {}, caused_by=(yes.seq,)).verified


def test_a_write_that_did_not_land_gets_an_unverified_receipt(world):
    spine, actions, alarms = world
    alarms.broken = True
    receipt = actions.act("mover", "alarm.move", P, {"to": "05:45"},
                          caused_by=(offer_and_answer(spine).seq,))
    assert not receipt.verified and receipt.proof is None      # the driver said ok; the read-back did not


def test_the_claim_detector(world):
    spine, actions, alarms = world
    auditor = ClaimAuditor(spine, spine.authorizer)
    good = actions.act("mover", "alarm.move", P, {"to": "05:45"},
                       caused_by=(offer_and_answer(spine).seq,))
    alarms.broken = True
    bad = actions.act("mover", "alarm.move", P, {"to": "04:00"},
                      caused_by=(offer_and_answer(spine).seq,))

    def claim(action, receipt_seq, cause):
        payload = {"action": action} | ({"receipt_seq": receipt_seq} if receipt_seq else {})
        return spine.publish(Event(Kind.CLAIM, "mover", subject=P, payload=payload,
                                   caused_by=(cause,)))

    backed = claim("alarm.move", good.seq, good.seq)
    no_receipt = claim("alarm.move", None, good.seq)
    unverified = claim("alarm.move", bad.seq, bad.seq)
    wrong_action = claim("light.toggle", good.seq, good.seq)
    found = auditor.scan()
    assert [c.seq for c in found] == [no_receipt.seq, unverified.seq, wrong_action.seq]
    assert backed.seq not in [c.seq for c in found]
    findings = [e.payload for e in spine.replay("audit") if e.kind is Kind.AUDIT_FINDING]
    assert [f["claim_seq"] for f in findings] == [c.seq for c in found]
    assert auditor.scan() == []                                  # each claim is judged once


def test_negative_control_a_detector_that_trusts_claims_finds_nothing(world, monkeypatch):
    spine, actions, _ = world
    cause = offer_and_answer(spine).seq
    spine.publish(Event(Kind.CLAIM, "mover", subject=P, payload={"action": "alarm.move"},
                        caused_by=(cause,)))
    monkeypatch.setattr(ClaimAuditor, "backed", lambda self, claim: True)
    assert ClaimAuditor(spine, spine.authorizer).scan() == []
