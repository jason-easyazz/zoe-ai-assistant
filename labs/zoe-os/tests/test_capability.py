"""Capability and consent: manifests are ceilings, grants are per person, default is no."""
from __future__ import annotations

import pytest

from zoe_os.capability import ADULT, CHILD, GUEST, ConsentError, Enforcer, GrantStore, Manifest, Person
from zoe_os.events import Event, Kind, Visibility
from zoe_os.spine import Refused, Spine

PEOPLE = [Person("adult-a"), Person("adult-b"), Person("kid", CHILD, guardians=("adult-a",)),
          Person("visitor", GUEST)]
VOICE = {"skill": "voice", "emits": ["utterance", "presence"], "origin": True}
HELPER = {"skill": "helper", "reads": {"utterance": "private", "presence": "household"},
          "emits": ["offer"], "acts": ["list.add"]}
AUDIT = {"skill": "audit", "reads": {"refusal": "kernel", "grant_change": "kernel"}}


@pytest.fixture
def world(tmp_path):
    store = GrantStore(PEOPLE)
    for m in (VOICE, HELPER, AUDIT):
        store.install(m)
    store.grant("adult-a", "audit")
    spine = Spine(str(tmp_path / "s.db"), Enforcer(store))
    store.on_change = lambda change, g: spine._kernel_append(Event(
        Kind.GRANT_CHANGE, "kernel", visibility=Visibility.KERNEL,
        payload={"change": change, "skill": g.skill, "person": g.person}))
    yield store, spine
    spine.close()


def say(person, text="hello", visibility=Visibility.PRIVATE):
    return Event(Kind.UTTERANCE, "voice", subject=person, visibility=visibility, payload={"text": text})


def seen(spine, reader):
    return [(e.subject, e.payload.get("text")) for e in spine.replay(reader)
            if e.kind is Kind.UTTERANCE]


# ---- manifests ------------------------------------------------------------------------
@pytest.mark.parametrize("bad", [
    {"skill": "x", "reads": {"telepathy": "private"}},          # not a kind
    {"skill": "x", "reads": {"utterance": "everyone"}},          # not a level
    {"skill": "x", "emits": ["forget"]},                         # kernel-only
    {"skill": "x", "wants": ["everything"]},                     # unknown key
    {"reads": {}},                                               # no name
])
def test_a_malformed_manifest_is_refused(bad):
    with pytest.raises(ConsentError):
        Manifest.from_dict(bad)


# ---- default deny ---------------------------------------------------------------------
def test_installed_but_not_granted_reads_nothing(world):
    store, spine = world
    spine.publish(say("adult-a"))
    spine.publish(say("adult-a", "for the house", Visibility.HOUSEHOLD))
    assert seen(spine, "helper") == []


def test_a_grant_opens_exactly_that_persons_private_events(world):
    store, spine = world
    store.grant("adult-a", "helper")
    spine.publish(say("adult-a", "mine"))
    spine.publish(say("adult-b", "not yours"))
    spine.publish(say("adult-b", "for the house", Visibility.HOUSEHOLD))
    assert seen(spine, "helper") == [("adult-a", "mine"), ("adult-b", "for the house")]


def test_a_narrower_grant_is_honoured(world):
    store, spine = world
    store.grant("adult-a", "helper", reads={"utterance": "household"}, acts=[])
    spine.publish(say("adult-a", "private"))
    spine.publish(say("adult-a", "shared", Visibility.HOUSEHOLD))
    assert seen(spine, "helper") == [("adult-a", "shared")]
    assert not Enforcer(store).may_act("helper", "list.add", "adult-a").allowed


def test_a_grant_cannot_exceed_the_manifest(world):
    store, _ = world
    with pytest.raises(ConsentError, match="exceed"):
        store.grant("adult-a", "helper", reads={"calendar_change": "private"})
    with pytest.raises(ConsentError, match="exceed"):
        store.grant("adult-a", "helper", acts=["door.unlock"])


# ---- children and guests ----------------------------------------------------------------
def test_a_childs_grant_needs_their_own_guardian(world):
    store, spine = world
    for guardian in (None, "adult-b", "kid", "visitor"):
        with pytest.raises(ConsentError, match="guardian"):
            store.grant("kid", "helper", guardian=guardian)
    spine.publish(say("kid", "a child's words"))
    assert seen(spine, "helper") == []
    assert store.grant("kid", "helper", guardian="adult-a").guardian == "adult-a"
    assert seen(spine, "helper") == [("kid", "a child's words")]


def test_a_guest_cannot_grant(world):
    store, _ = world
    with pytest.raises(ConsentError, match="guest"):
        store.grant("visitor", "helper")


# ---- revocation ------------------------------------------------------------------------
def test_revoking_stops_the_next_delivery_even_from_the_queue(world):
    store, spine = world
    store.grant("adult-a", "helper")
    sub = spine.subscribe("helper")
    spine.publish(say("adult-a", "one"))
    spine.publish(say("adult-a", "two"))
    assert sub.poll().payload["text"] == "one"
    assert store.revoke("adult-a", "helper")
    assert sub.poll() is None and seen(spine, "helper") == []


# ---- the enforcement point --------------------------------------------------------------
def test_asking_for_more_than_the_manifest_is_refused_and_recorded(world):
    store, spine = world
    store.grant("adult-a", "helper")
    cause = spine.publish(say("adult-a")).seq
    with pytest.raises(Refused, match="more than its manifest"):
        spine.subscribe("helper", kinds={Kind.CALENDAR_CHANGE})
    with pytest.raises(Refused, match="more than its manifest"):
        spine.publish(Event(Kind.UTTERANCE, "helper", subject="adult-a", payload={"text": "fake"},
                            caused_by=(cause,)))
    with pytest.raises(Refused, match="no grant from that person"):
        spine.subscribe("helper", subject="adult-b")
    with pytest.raises(Refused, match="no grant from that person"):
        spine.publish(Event(Kind.OFFER, "helper", subject="adult-b", caused_by=(cause,),
                            payload={"offer_id": "o", "text": "unasked"}))
    with pytest.raises(Refused, match="no manifest"):
        spine.subscribe("never-installed")
    refusals = [e.payload for e in spine.replay("audit") if e.kind is Kind.REFUSAL]
    assert [r["who"] for r in refusals] == ["helper"] * 4 + ["never-installed"]
    assert all("fake" not in str(r) and "unasked" not in str(r) for r in refusals)


def test_actions_need_the_manifest_and_that_persons_grant(world):
    store, _ = world
    pep = Enforcer(store)
    store.grant("adult-a", "helper")
    assert pep.may_act("helper", "list.add", "adult-a").grant_id
    assert pep.may_act("helper", "list.add", "adult-b").reason == "no grant from that person"
    assert pep.may_act("helper", "door.unlock", "adult-a").reason == "asks for more than its manifest"


def test_grant_changes_are_events_only_an_auditor_can_read(world):
    store, spine = world
    store.grant("adult-b", "helper")
    store.revoke("adult-b", "helper")
    changes = [(e.payload["change"], e.payload["person"]) for e in spine.replay("audit")
               if e.kind is Kind.GRANT_CHANGE]
    assert changes == [("granted", "adult-b"), ("revoked", "adult-b")]
    store.grant("adult-a", "helper")
    assert [e for e in spine.replay("helper") if e.visibility is Visibility.KERNEL] == []


def test_negative_control_without_the_grant_check_private_events_leak(world, monkeypatch):
    store, spine = world
    spine.publish(say("adult-b", "b's private words"))
    assert seen(spine, "helper") == []
    from zoe_os.spine import Decision
    monkeypatch.setattr(Enforcer, "may_read", lambda self, reader, ev: Decision(True))
    assert seen(spine, "helper") == [("adult-b", "b's private words")]
