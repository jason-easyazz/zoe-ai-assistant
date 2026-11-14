"""Explain: "why did you do that?" for an action, a refusal and an offer, and what it must
never show: another person's private event, not even its number.
"""
from __future__ import annotations

import pytest
from world import ALEX, KID, SAM, TOMORROW_0630, answer, emit, household, of_kind, presence, step

from zoe_os.capability import GUEST, Person
from zoe_os.events import Event, Kind, Visibility
from zoe_os.explain import Explainer
from zoe_os.skills.calendar_headsup import CalendarHeadsUp
from zoe_os.skills.leaving_catch import LeavingCatch
from zoe_os.spine import Refused

SENTINEL = "zx-sentinel-jade-quarry-3307"
DIGEST = {"skill": "digest", "reads": {"utterance": "private", "offer": "private"}, "emits": ["offer"]}
NO = "not an event you may ask about"


@pytest.fixture
def k(tmp_path):
    kernel = household(tmp_path)
    kernel.grants.people["visitor"] = Person("visitor", GUEST)
    for skill in (LeavingCatch(), CalendarHeadsUp()):
        kernel.install(skill)
    kernel.grants.install(DIGEST)
    for person in (ALEX, SAM):
        for skill in ("leaving-catch", "calendar-heads-up", "digest"):
            kernel.grants.grant(person, skill)
    yield kernel
    kernel.close()


def moved_alarm(k) -> Event:
    """The whole honest path for SAM: calendar change, offer, yes, action, receipt, claim."""
    emit(k, Kind.CALENDAR_CHANGE, "calendar", SAM, event_id="e1", change="created",
         starts_at=TOMORROW_0630)
    k.pump()
    answer(k, of_kind(k, Kind.OFFER)[-1])
    k.pump()
    return of_kind(k, Kind.ACTION_RECEIPT)[-1]


def test_why_an_action_the_chain_the_grant_and_one_sentence(k):
    receipt = moved_alarm(k)
    why = k.explainer.why(receipt.seq, person=SAM)
    cal, offer, yes = (n["seq"] for n in why["chain"])
    assert [n["kind"] for n in why["chain"]] == ["calendar_change", "offer", "offer_response"]
    assert why["grant"]["person"] == SAM and why["grant"]["skill"] == "calendar-heads-up"
    assert [n["kind"] for n in why["then"]] == ["claim"] and why["hidden"] == 0
    assert why["sentence"] == (
        f"calendar-heads-up did alarm.move for sam after sam said yes (#{yes}), under the grant sam "
        f"gave it (#{why['grant']['seq']}), and the read-back confirmed it. It followed from: "
        f"calendar change #{cal}, offer #{offer}, offer response #{yes}.")
    assert k.explainer.last(SAM) == receipt.seq and k.explainer.last(ALEX) is None
    assert k.explainer.why(receipt.seq, person=SAM) == why          # the same question, the same answer


def test_why_a_refusal(k):
    with pytest.raises(Refused):
        k.actions.act("leaving-catch", "door.unlock", SAM, {}, caused_by=(1,))
    refusal = of_kind(k, Kind.REFUSAL)[-1]
    why = k.explainer.why(refusal.seq, person=ALEX)
    assert why["sentence"] == ("leaving-catch was refused when it tried to act door.unlock: "
                               "asks for more than its manifest. Nothing led to it.")
    assert k.explainer.last(ALEX, "refusal") == refusal.seq


def test_why_an_offer_how_it_was_routed_and_what_was_answered(k):
    step(k, SAM, "gym bag")
    left = presence(k, SAM, "left")
    k.pump()
    offer = of_kind(k, Kind.OFFER)[-1]
    assert "has not been answered" in k.explainer.why(offer.seq, person=SAM)["sentence"]
    k.attention.route(offer, hour=12, day=1)
    answer(k, offer, "declined")
    why = k.explainer.why(k.explainer.last(SAM, "offer"), person=SAM)
    assert why["sentence"] == (
        "leaving-catch made this offer to sam. It was routed as card because unprompted speech is off "
        f"for this person. The answer was: declined. It followed from: routine step #{left.seq - 1}, "
        f"presence #{left.seq}.")
    assert [n["kind"] for n in why["then"]] == ["attention", "offer_response"]


# ---- the privacy rule ----------------------------------------------------------------------------
def crossing(k):
    """A skill both people enabled derives an offer for SAM from ALEX's private words."""
    theirs = emit(k, Kind.UTTERANCE, "voice", ALEX, text=f"alex's private words {SENTINEL}")
    mine = emit(k, Kind.UTTERANCE, "voice", SAM, text="sam's own words")
    offer = emit(k, Kind.OFFER, "digest", SAM, cause=(theirs.seq, mine.seq), offer_id="d1",
                 text="a suggestion")
    return theirs, mine, offer


def test_another_persons_private_event_in_the_chain_is_a_count_and_nothing_else(k):
    theirs, mine, offer = crossing(k)
    why = k.explainer.why(offer.seq, person=SAM)
    assert [n["seq"] for n in why["chain"]] == [mine.seq] and why["hidden"] == 1
    assert why["what"]["caused_by"] == [mine.seq]              # not even the hidden event's number
    assert SENTINEL not in str(why) and "alex" not in str(why).lower()
    assert why["sentence"].endswith(f"It followed from: utterance #{mine.seq}, 1 event you may not see.")


def test_negative_control_without_the_visibility_check_the_private_words_are_shown(k, monkeypatch):
    theirs, mine, offer = crossing(k)
    monkeypatch.setattr(Explainer, "_may_see", lambda self, asker, ev: True)
    why = k.explainer.why(offer.seq, person=SAM)
    assert SENTINEL in str(why) and theirs.seq in why["what"]["caused_by"]


def test_what_led_to_a_hidden_event_stays_hidden_too(k):
    household_note = emit(k, Kind.UTTERANCE, "voice", ALEX, Visibility.HOUSEHOLD, text="said aloud")
    assert k.explainer.why(household_note.seq, person=SAM)["kind"] == "utterance"   # SAM may see it...
    theirs = emit(k, Kind.OFFER, "digest", ALEX, cause=(household_note.seq,), offer_id="a1",
                  text=SENTINEL)
    mine = emit(k, Kind.UTTERANCE, "voice", SAM, text="sam's own words")
    offer = emit(k, Kind.OFFER, "digest", SAM, cause=(theirs.seq, mine.seq), offer_id="d2", text="x")
    why = k.explainer.why(offer.seq, person=SAM)
    assert [n["seq"] for n in why["chain"]] == [mine.seq] and why["hidden"] == 1    # ...but not via ALEX


def test_an_event_you_may_not_see_and_one_that_does_not_exist_are_refused_alike(k):
    theirs, mine, offer = crossing(k)
    refused = []
    for seq, asker in ((theirs.seq, SAM), (offer.seq, ALEX), (99_999, SAM), (mine.seq, "nobody"),
                       (offer.seq, "visitor"), (offer.seq, KID)):
        with pytest.raises(Refused, match=NO) as exc:
            k.explainer.why(seq, person=asker)
        refused.append(str(exc.value).split(": ", 1)[1])
    assert set(refused) == {f"explain: {NO}"}
    assert SENTINEL not in str([e.payload for e in of_kind(k, Kind.REFUSAL)])


def test_a_guardian_may_ask_for_their_child_and_a_child_may_not_read_the_audit_trail(k):
    kids = emit(k, Kind.UTTERANCE, "voice", KID, text="the child's words")
    assert k.explainer.why(kids.seq, person=ALEX)["what"]["subject"] == KID      # ALEX is the guardian
    with pytest.raises(Refused, match=NO):
        k.explainer.why(kids.seq, person=SAM)
    with pytest.raises(Refused):
        k.actions.act("digest", "door.unlock", SAM, {}, caused_by=(kids.seq,))
    refusal = of_kind(k, Kind.REFUSAL)[-1]
    with pytest.raises(Refused, match=NO):
        k.explainer.why(refusal.seq, person=KID)
    note = emit(k, Kind.PRESENCE, "presence", ALEX, Visibility.HOUSEHOLD, state="home", confidence=0.9)
    assert k.explainer.why(note.seq, person=KID)["kind"] == "presence"           # household: yes
    with pytest.raises(Refused, match=NO):
        k.explainer.why(note.seq, person="visitor")                              # a guest: no


def test_an_uncertain_action_is_explained_as_uncertain(k):
    receipt = moved_alarm(k)
    k.spine._db.execute("UPDATE intents SET receipt_seq = NULL")       # as if the kernel had died
    (recovered,) = k.actions.recover()
    assert "but the outcome is uncertain" in k.explainer.why(recovered.seq, person=SAM)["sentence"]
    assert "confirmed" in k.explainer.why(receipt.seq, person=SAM)["sentence"]


# ---- through the socket: a surface asks for a person; a skill cannot ----------------------------
def test_why_did_you_do_that_through_the_socket():
    from procs import HELPER, Box
    box = Box()
    try:
        panel, voice = box.client("panel"), box.client("voice")
        for person in (ALEX, SAM):
            panel.grant(person, HELPER)
        theirs = voice.publish("utterance", subject=ALEX, payload={"text": SENTINEL})
        mine = voice.publish("utterance", subject=SAM, payload={"text": "sam's own words"})
        helper = box.client(HELPER)
        helper.act("light.toggle", SAM, {"room": "hall"}, caused_by=[theirs.seq, mine.seq])
        why = panel.explain(person=SAM, last="action")
        assert why["kind"] == "action_receipt" and why["hidden"] == 1 and SENTINEL not in str(why)
        assert why["sentence"].startswith("friendly-helper did light.toggle for sam with no yes needed")
        with pytest.raises(Refused, match="trusted surface"):
            helper.explain(person=ALEX, seq=theirs.seq)        # a skill cannot ask as a person
        with pytest.raises(Refused, match=NO):
            panel.explain(person=ALEX, last="action")          # ALEX has no action to ask about
    finally:
        box.close()
