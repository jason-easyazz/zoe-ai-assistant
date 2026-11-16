"""Two honest skills, end to end on synthetic data: offer first, act only on a yes."""
from __future__ import annotations

import pytest
from world import (ALEX, KID, NOW, SAM, TOMORROW_0630, TOMORROW_1100, answer, emit, household,
                   of_kind, presence, refusal_reasons, step)

from zoe_os.attention import CARD, DIGEST, SPOKEN
from zoe_os.capability import ConsentError
from zoe_os.events import Kind
from zoe_os.skills.calendar_headsup import CalendarHeadsUp
from zoe_os.skills.leaving_catch import LeavingCatch


@pytest.fixture
def k(tmp_path):
    kernel = household(tmp_path)
    kernel.install(LeavingCatch())
    kernel.install(CalendarHeadsUp())
    for person in (ALEX, SAM):
        kernel.grants.grant(person, "leaving-catch")
        kernel.grants.grant(person, "calendar-heads-up")
    yield kernel
    kernel.close()


def new_event(k, person, starts_at, title="synthetic early thing"):
    return emit(k, Kind.CALENDAR_CHANGE, "calendar", person, event_id="e1", change="created",
                title=title, starts_at=starts_at)


# ---- leaving catch ----------------------------------------------------------------------
def test_leaving_with_an_undone_step_makes_an_offer_and_never_an_action(k):
    step(k, ALEX, "gym bag")
    step(k, ALEX, "water bottle")
    step(k, ALEX, "water bottle", done=True)
    left = presence(k, ALEX, "left")
    k.pump()
    (offer,) = of_kind(k, Kind.OFFER)
    assert offer.subject == ALEX and offer.payload["items"] == ["gym bag"]
    assert left.seq in offer.caused_by and offer.source == "leaving-catch"
    assert of_kind(k, Kind.ACTION_RECEIPT) == [] and refusal_reasons(k) == []


@pytest.mark.parametrize("setup", ["nothing undone", "someone else left", "not sure it was them",
                                   "arrived"])
def test_leaving_catch_stays_quiet_when_it_should(k, setup):
    if setup != "nothing undone":
        step(k, ALEX, "gym bag")
    who = SAM if setup == "someone else left" else ALEX
    presence(k, who, "arrived" if setup == "arrived" else "left",
             confidence=0.4 if setup == "not sure it was them" else 0.95)
    k.pump()
    assert of_kind(k, Kind.OFFER) == []


def test_a_person_who_never_enabled_the_skill_gets_nothing(k):
    k.grants.revoke(SAM, "leaving-catch")
    step(k, SAM, "gym bag")
    presence(k, SAM, "left")
    k.pump()
    assert of_kind(k, Kind.OFFER) == []


def test_after_a_forget_the_skill_no_longer_knows_the_routine(k):
    step(k, ALEX, "gym bag")
    k.pump()
    k.spine.forget(ALEX, actor=ALEX)
    presence(k, ALEX, "left")
    k.pump()
    assert of_kind(k, Kind.OFFER) == []


# ---- calendar heads-up ------------------------------------------------------------------
def test_an_early_event_tomorrow_is_offered_then_done_only_on_yes_with_a_receipt(k):
    new_event(k, ALEX, TOMORROW_0630)
    k.pump()
    (offer,) = of_kind(k, Kind.OFFER)
    assert offer.payload["proposal"] == {"alarm": "05:45"}
    assert k.alarms.at == {} and of_kind(k, Kind.ACTION_RECEIPT) == []     # offered, not done
    answer(k, offer)
    k.pump()
    assert k.alarms.at == {ALEX: "05:45"}
    (receipt,) = of_kind(k, Kind.ACTION_RECEIPT)
    assert receipt.payload["verified"] and receipt.payload["skill"] == "calendar-heads-up"
    (claim,) = of_kind(k, Kind.CLAIM)
    assert claim.payload["receipt_seq"] == receipt.seq
    assert k.auditor.scan() == [] and refusal_reasons(k) == []


@pytest.mark.parametrize("starts_at, response", [(TOMORROW_0630, "declined"), (TOMORROW_0630, None),
                                                 (TOMORROW_1100, None), (NOW + 3600, None)])
def test_no_yes_or_not_early_means_no_alarm_change(k, starts_at, response):
    new_event(k, ALEX, starts_at)
    k.pump()
    offers = of_kind(k, Kind.OFFER)
    assert len(offers) == (1 if starts_at == TOMORROW_0630 else 0)
    if response:
        answer(k, offers[0], response)
        k.pump()
    assert k.alarms.at == {} and of_kind(k, Kind.CLAIM) == []


def test_a_write_that_does_not_land_is_not_claimed(k):
    k.actions.register("alarm.move", lambda person, args: None, k.alarms.verify)   # says nothing, does nothing
    new_event(k, ALEX, TOMORROW_0630)
    k.pump()
    answer(k, of_kind(k, Kind.OFFER)[0])
    k.pump()
    (receipt,) = of_kind(k, Kind.ACTION_RECEIPT)
    assert receipt.payload["verified"] is False and of_kind(k, Kind.CLAIM) == []


# ---- attention --------------------------------------------------------------------------
def offers_for(k, n):
    for i in range(n):
        step(k, ALEX, f"item {i}")
        presence(k, ALEX, "left")
        k.pump()
    return of_kind(k, Kind.OFFER)


def test_unprompted_speech_is_off_by_default_and_the_budget_holds(k):
    offers = offers_for(k, 4)
    channels = [k.attention.route(o, hour=10, day=1) for o in offers]
    assert channels == [CARD, CARD, CARD, DIGEST]                 # three a day, then the digest
    assert k.attention.route(offers[0], hour=23, day=2) == DIGEST  # quiet hours
    reasons = [e.payload["reason"] for e in of_kind(k, Kind.ATTENTION)]
    assert reasons[0] == "unprompted speech is off for this person" and len(reasons) == 5


def test_only_the_person_can_turn_speech_on_and_only_for_themselves(k):
    (offer,) = offers_for(k, 1)
    with pytest.raises(ConsentError):
        k.attention.set(ALEX, by=SAM, spoken_unprompted=True)
    with pytest.raises(ConsentError):
        k.attention.set(KID, by=KID, spoken_unprompted=True)      # a child cannot; a guardian can
    k.attention.set(KID, by=ALEX, offers_per_day=1)
    k.attention.set(ALEX, by=ALEX, spoken_unprompted=True)
    assert k.attention.route(offer, hour=10, day=1) == SPOKEN
    assert k.attention.setting(SAM).spoken_unprompted is False
