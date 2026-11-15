"""Regression tests for the independent review of the stacked zoe-os PRs (#2505, #2506, #2546,
#2547, #2551). Each test failed on the code before its fix (the negative control is the old code
itself); the comment above each says which finding it pins. Invented people and words only.
"""
from __future__ import annotations

import pytest
from spine_helpers import AllowList
from world import ALEX, SAM, answer, emit, household

from zoe_os.capability import CHILD, Enforcer, GrantStore, Person
from zoe_os.events import Event, Kind, Visibility
from zoe_os.explain import Explainer
from zoe_os.receipts import ClaimAuditor
from zoe_os.spine import Refused, Spine

A, B = "person-a", "person-b"
DERIVED = "zx-derived-only-cobalt-quill-3307"
SENTINEL = "zx-sentinel-tin-orchard-8150"
NAME = "Zorvane Quillfeather"


@pytest.fixture
def spine(tmp_path):
    auth = AllowList(origins={"voice"}, skills={"skill"},
                     readers={"all": {"*"}, "skill": {"*"}, "audit": {"kernel"}})
    s = Spine(str(tmp_path / "spine.db"), auth)
    yield s
    s.close()


def say(person, text):
    return Event(Kind.UTTERANCE, "voice", subject=person, payload={"text": text})


def offer(person, cause, text, **extra):
    return Event(Kind.OFFER, "skill", subject=person, caused_by=(cause,),
                 payload={"offer_id": f"o{cause}", "text": text, **extra})


# ---- #2505 finding 1: text that only ever lived in a DERIVED event is shielded too ---------------
def test_forgotten_text_unique_to_a_derived_event_cannot_be_republished(spine):
    origin = spine.publish(say(A, "plain origin words"))
    spine.publish(offer(A, origin.seq, DERIVED))
    live = spine.publish(say(B, "an unrelated live event"))
    assert spine.forget(A, actor=A).count == 2
    for target in (A,):
        with pytest.raises(Refused, match="carries forgotten content"):
            spine.publish(offer(target, live.seq, DERIVED))
    assert spine.publish(offer(B, live.seq, DERIVED)).seq


# ---- #2546: a tuple is a JSON array on disk, so the shields must look inside one ------------------
def test_a_tuple_cannot_smuggle_forgotten_words_past_the_word_shield(spine):
    spine.publish(say(A, SENTINEL))
    live = spine.publish(say(B, "a live event to cite"))
    spine.forget(A, actor=A)
    with pytest.raises(Refused, match="carries forgotten content"):
        spine.publish(offer(A, live.seq, "a reminder", extra=(SENTINEL,)))


def test_a_tuple_cannot_smuggle_a_forgotten_name_past_the_entity_shield(spine):
    spine.publish(say(A, f"{NAME} rang"))
    live = spine.publish(say(B, "a live event to cite"))
    spine.forget(A, actor=A, entity=NAME)
    with pytest.raises(Refused, match="names a forgotten entity"):
        spine.publish(offer(A, live.seq, "a reminder", extra=(NAME,)))
    with pytest.raises(Refused, match="names a forgotten entity"):
        spine.publish(offer(A, live.seq, "a reminder", extra=({"who": (NAME,)},)))


# ---- #2505 finding 2: HOUSEHOLD means "an adult granted it", not "any member did" ----------------
@pytest.fixture
def consent(tmp_path):
    store = GrantStore([Person("adult-a"), Person("adult-b"),
                        Person("kid", CHILD, guardians=("adult-a",))])
    store.install({"skill": "voice", "emits": ["utterance"], "origin": True})
    store.install({"skill": "helper", "reads": {"utterance": "private"}})
    spine = Spine(str(tmp_path / "c.db"), Enforcer(store))
    yield store, spine
    spine.close()


def test_a_childs_grant_does_not_open_other_peoples_household_events(consent):
    store, spine = consent
    store.grant("kid", "helper", guardian="adult-a")
    for person in ("adult-b", "kid"):
        spine.publish(Event(Kind.UTTERANCE, "voice", subject=person, visibility=Visibility.HOUSEHOLD,
                            payload={"text": f"household words of {person}"}))
    assert [e.subject for e in spine.replay("helper")] == ["kid"]    # the child's own, nobody else's
    store.grant("adult-b", "helper")
    assert [e.subject for e in spine.replay("helper")] == ["adult-b", "kid"]   # an adult's grant does


def test_a_childs_grant_alone_cannot_publish_for_the_whole_household(consent):
    store, spine = consent                       # same class, the publish side: about nobody
    store.install({"skill": "teller", "reads": {"utterance": "private"}, "emits": ["offer"]})
    store.grant("kid", "teller", guardian="adult-a")
    cause = spine.publish(Event(Kind.UTTERANCE, "voice", subject="kid", payload={"text": "hi"}))
    house = Event(Kind.OFFER, "teller", visibility=Visibility.HOUSEHOLD, caused_by=(cause.seq,),
                  payload={"offer_id": "h1", "text": "pizza tonight?"})
    with pytest.raises(Refused, match="no grant"):
        spine.publish(house)
    store.grant("adult-a", "teller")
    assert spine.publish(house).seq


# ---- #2506 finding 2: one verified receipt backs ONE claim, durably -------------------------------
MOVER = {"skill": "mover", "reads": {"calendar_change": "private", "offer_response": "private",
                                     "action_receipt": "private"},
         "emits": ["offer", "claim"], "acts": ["alarm.move"]}


@pytest.fixture
def k(tmp_path):
    kernel = household(tmp_path)
    kernel.grants.install(MOVER)
    kernel.grants.grant(SAM, "mover")
    yield kernel
    kernel.close()


def move(k, person=SAM, to="05:45"):
    cal = emit(k, Kind.CALENDAR_CHANGE, "calendar", person, event_id="e", change="created")
    off = emit(k, Kind.OFFER, "mover", person, cause=(cal.seq,), offer_id=f"o{cal.seq}", text="move?")
    yes = answer(k, off)
    return k.actions.act("mover", "alarm.move", person, {"to": to}, caused_by=(yes.seq,)), yes


def claim(k, receipt):
    return emit(k, Kind.CLAIM, "mover", SAM, cause=(receipt.seq,), action="alarm.move",
                receipt_seq=receipt.seq)


def test_one_receipt_cannot_back_a_second_claim_even_after_a_restart(k):
    receipt, _ = move(k)
    first = claim(k, receipt)
    assert ClaimAuditor(k.spine, k.enforcer).scan() == []
    again = claim(k, receipt)
    assert [c.seq for c in ClaimAuditor(k.spine, k.enforcer).scan()] == [again.seq]   # a fresh auditor
    assert first.seq < again.seq


# ---- #2547: forget reaches the action bookkeeping (intents), and a yes stays spent ---------------
def intents(k):
    return k.spine._db.execute(
        "SELECT person, skill, action, grant_id, caused_by, accept_seq, undoes FROM intents").fetchall()


def test_a_forget_leaves_no_intent_row_naming_the_person_or_their_events(k):
    receipt, yes = move(k)
    k.actions.undo(SAM, actor=SAM)
    assert any(row[0] == SAM for row in intents(k))
    k.spine.forget(SAM, actor=SAM)
    assert intents(k) == []


def test_forgetting_only_the_receipt_does_not_make_the_yes_usable_again(k):
    receipt, yes = move(k)
    k.spine.forget(SAM, actor=SAM, seqs=[receipt.seq])
    assert k.spine._get(yes.seq) is not None                  # the yes itself was not forgotten
    assert all(row[0] != SAM for row in intents(k))           # but the row no longer names SAM
    with pytest.raises(Refused, match="that yes was already used"):
        k.actions.act("mover", "alarm.move", SAM, {"to": "04:00"}, caused_by=(yes.seq,))


# ---- #2551: "the last action" is the newest one, however many came before -------------------------
def capped_forward_reads(monkeypatch, cap=2):
    """Model a store that holds more rows than a forward read's cap."""
    real = Spine._fetch
    monkeypatch.setattr(Spine, "_fetch", lambda self, after, kinds, subject, limit:
                        real(self, after, kinds, subject, min(limit, cap)))


def test_last_action_is_the_newest_receipt_not_the_last_one_a_capped_read_reached(k, monkeypatch):
    done = [move(k, to=t)[0] for t in ("05:00", "05:15", "05:30")]
    capped_forward_reads(monkeypatch)
    assert k.actions.last_action(SAM).seq == done[-1].seq
    assert Explainer(k.spine, k.grants.people).last(SAM) == done[-1].seq


def test_undo_reverses_the_newest_action_when_there_are_many(k, monkeypatch):
    for t in ("05:00", "05:15", "05:30"):
        move(k, to=t)
    capped_forward_reads(monkeypatch)
    k.actions.undo(SAM, actor=SAM)
    assert k.alarms.at[SAM] == "05:15"
    assert ALEX not in k.alarms.at
