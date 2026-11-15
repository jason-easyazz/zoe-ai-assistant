"""Forget by ENTITY and by TOPIC, the two scopes production's forget has and the lab lacked.

Every guarantee the person-forget gives (derived events, bytes on disk, queues, no
re-publication) is asserted again for both scopes with a planted sentinel, and each has a
negative control that removes one mechanism and gets the sentinel back. The last tests pin
what still gets through, so the limit is a fact and not a footnote.
"""
from __future__ import annotations

import glob

import pytest
from spine_helpers import AllowList

from zoe_os.events import Event, Kind
from zoe_os.spine import MAX_TOPIC_EVENTS, Refused, Spine, Subscription

A, B = "person-a", "person-b"
NAME = "Zorvane Quillfeather"                      # an invented name: the entity sentinel
SENTINEL = "zx-sentinel-opal-ferry-5528"
ENTITY_TEXT = f"okay so zorvane-QUILLFEATHER rang, the code is {SENTINEL}"
TOPIC = "my mum's heart"
TOPIC_TEXT = f"mother saw the doctor about her heart, ref {SENTINEL}"
SCOPES = pytest.mark.parametrize("scope, text", [({"entity": NAME}, ENTITY_TEXT),
                                                 ({"topic": TOPIC}, TOPIC_TEXT)], ids=["entity", "topic"])


def say(person, text, **kw):
    return Event(Kind.UTTERANCE, "voice", subject=person, payload={"text": text}, **kw)


def offer(person, text, cause, **extra):
    return Event(Kind.OFFER, "skill", subject=person, caused_by=(cause,),
                 payload={"offer_id": f"o{cause}", "text": text, **extra})


@pytest.fixture
def spine(tmp_path):
    auth = AllowList(origins={"voice"}, skills={"skill"},
                     readers={"all": {"*"}, "skill": {"*"}, "late": {"*"}, "audit": {"kernel"}})
    s = Spine(str(tmp_path / "spine.db"), auth, queue_size=8)
    s.path = str(tmp_path / "spine.db")
    yield s
    s.close()


def texts(events) -> str:
    return " | ".join(str(e.payload) for e in events)


def drain(sub):
    out = []
    while (ev := sub.poll()) is not None:
        out.append(ev)
    return out


def on_disk(spine) -> bytes:
    return b"".join(open(p, "rb").read() for p in glob.glob(spine.path + "*"))


def reasons(spine):
    return [e.payload["reason"] for e in spine.replay("audit") if e.kind is Kind.REFUSAL]


# ---- what a scoped forget takes, and what it leaves ----------------------------------------------
@SCOPES
def test_only_the_persons_matching_events_go(spine, scope, text):
    hit = spine.publish(say(A, text))
    kept = [spine.publish(say(A, "a's unrelated words")), spine.publish(say(B, text)),
            spine.publish(say(A, "quillfeathers are a kind of pen; my heart is fine"))]
    assert spine.forget(A, actor=A, **scope).count == 1
    live = [e.seq for e in spine.replay("all") if e.kind is Kind.UTTERANCE]
    assert live == [e.seq for e in kept] and hit.seq not in live
    (notice,) = [e for e in spine.replay("all") if e.kind is Kind.FORGET]
    assert notice.payload == {"subject": A, "upto": kept[-1].seq, "count": 1,
                              "scope": next(iter(scope)), "seqs": [hit.seq]}
    assert "zorvane" not in str(notice.payload).lower() and "heart" not in str(notice.payload)


@SCOPES
def test_the_scoped_forget_cascades_through_everything_derived(spine, scope, text):
    src = spine.publish(say(A, text))
    first = spine.publish(offer(B, "derived, first hand", src.seq))
    second = spine.publish(offer(B, "derived, second hand", first.seq))
    assert spine.forget(A, actor=A, **scope).count == 3
    assert {src.seq, first.seq, second.seq}.isdisjoint(e.seq for e in spine.replay("all"))


@SCOPES
def test_negative_control_without_the_cascade_a_derived_copy_survives(spine, scope, text, monkeypatch):
    monkeypatch.setattr(Spine, "_closure", lambda self, seed: set(seed))
    src = spine.publish(say(A, text))
    spine.publish(offer(B, "a summary", src.seq, copied=SENTINEL))
    spine.forget(A, actor=A, **scope)
    assert SENTINEL in texts(spine.replay("all"))


@SCOPES
def test_the_bytes_are_gone_from_disk_and_the_ledger_does_not_hold_the_name(spine, scope, text):
    spine.publish(say(A, text))
    assert SENTINEL.encode() in on_disk(spine)                  # positive control
    spine.forget(A, actor=A, **scope)
    raw = on_disk(spine).lower()
    assert SENTINEL.encode() not in raw and b"zorvane" not in raw and b"quillfeather" not in raw


@SCOPES
def test_a_queued_event_is_withdrawn_and_an_offline_subscriber_never_sees_it(spine, scope, text):
    live = spine.subscribe("all", kinds={Kind.UTTERANCE})
    spine.publish(say(A, text))
    assert SENTINEL in texts(live._queue)
    spine.forget(A, actor=A, **scope)
    assert SENTINEL not in texts(drain(live))
    assert SENTINEL not in texts(drain(spine.subscribe("late", from_seq=0)))


@SCOPES
def test_negative_control_without_the_queue_purge_the_queued_event_is_delivered(spine, scope, text,
                                                                               monkeypatch):
    monkeypatch.setattr(Subscription, "_purge_forgotten", lambda self: None)
    live = spine.subscribe("all", kinds={Kind.UTTERANCE})
    spine.publish(say(A, text))
    spine.forget(A, actor=A, **scope)
    assert SENTINEL in texts(drain(live))


@SCOPES
def test_negative_control_without_the_tombstone_the_offline_subscriber_gets_it(spine, scope, text,
                                                                              monkeypatch):
    monkeypatch.setattr(Spine, "_tombstone", lambda self, seqs: None)
    spine.publish(say(A, text))
    spine.forget(A, actor=A, **scope)
    assert SENTINEL in texts(drain(spine.subscribe("late", from_seq=0)))
    assert SENTINEL.encode() in on_disk(spine)


@SCOPES
def test_a_skill_cannot_publish_the_forgotten_words_again(spine, scope, text):
    spine.publish(say(A, text))
    live = spine.publish(say(B, "a live event to cite"))
    spine.forget(A, actor=A, **scope)
    for again in (text, text.upper() + "  "):
        with pytest.raises(Refused, match="forgotten content"):
            spine.publish(offer(A, again, live.seq))
    assert SENTINEL not in texts(spine.replay("all"))


@SCOPES
def test_negative_control_without_the_shield_the_words_come_back(spine, scope, text, monkeypatch):
    monkeypatch.setattr(Spine, "_shielded", lambda self, payload: False)
    monkeypatch.setattr(Spine, "_names_forgotten", lambda self, ev: False)
    spine.publish(say(A, text))
    live = spine.publish(say(B, "a live event to cite"))
    spine.forget(A, actor=A, **scope)
    spine.publish(offer(A, text, live.seq))
    assert SENTINEL in texts(spine.replay("all"))


# ---- the entity shield: the NAME stays forgotten, not only the old sentences ---------------------
def forgotten_name(spine) -> int:
    spine.publish(say(A, ENTITY_TEXT))
    live = spine.publish(say(B, "a live event to cite"))
    spine.forget(A, actor=A, entity=NAME)
    return live.seq


def test_a_skill_cannot_name_the_forgotten_entity_in_new_words(spine):
    live = forgotten_name(spine)
    smuggled = (("call Zorvane Quillfeather back?", {}), ("ZORVANE   quillfeather!", {}),
                ("a reminder", {"extra": [{"who": "zorvane_quillfeather"}]}),
                ("a reminder", {"zorvane quillfeather": 1}))                  # as a dict key
    for text, extra in smuggled:
        with pytest.raises(Refused, match="names a forgotten entity"):
            spine.publish(offer(A, text, live, **extra))
    assert spine.publish(offer(B, "Zorvane Quillfeather rang for you", live)).seq   # B never forgot
    assert reasons(spine) == ["names a forgotten entity"] * 4
    assert "zorvane" not in str([e.payload for e in spine.replay("audit")]).lower()


def test_negative_control_without_the_entity_shield_the_name_is_written_again(spine, monkeypatch):
    monkeypatch.setattr(Spine, "_names_forgotten", lambda self, ev: False)
    live = forgotten_name(spine)
    spine.publish(offer(A, "call Zorvane Quillfeather back?", live))
    assert "Zorvane" in texts(spine.replay("all"))


def test_the_entity_shield_survives_a_restart_and_only_the_person_lifts_it(spine):
    live = forgotten_name(spine)
    spine.close()
    again = Spine(spine.path, spine.authorizer)
    with pytest.raises(Refused, match="names a forgotten entity"):
        again.publish(offer(A, "Zorvane Quillfeather", live))
    heard = again.publish(say(A, "zorvane quillfeather is visiting"))     # first hand: recorded...
    with pytest.raises(Refused, match="names a forgotten entity"):       # ...but it lifts nothing
        again.publish(offer(A, "Zorvane Quillfeather is visiting", heard.seq))
    with pytest.raises(Refused, match="release"):
        again.release(A, NAME, actor=B)
    assert again.release(A, NAME, actor=A) and not again.release(A, NAME, actor=A)
    assert again.publish(offer(A, "Zorvane Quillfeather is visiting", heard.seq)).seq
    again.close()


def test_an_event_about_nobody_in_particular_cannot_carry_the_name_either(spine):
    live = forgotten_name(spine)
    with pytest.raises(Refused, match="names a forgotten entity"):
        spine.publish(Event(Kind.OFFER, "skill", visibility="household", caused_by=(live,),
                            payload={"offer_id": "h", "text": "Zorvane Quillfeather for dinner?"}))


# ---- a scope that matches nothing, or too much, changes nothing -----------------------------------
@pytest.mark.parametrize("scope", [{"entity": ""}, {"entity": " ?! "}, {"topic": ""},
                                   {"topic": "everything I told you about that"},
                                   {"entity": NAME, "topic": TOPIC}])
def test_an_empty_scope_is_refused_and_never_widens_to_everything(spine, scope):
    spine.publish(say(A, ENTITY_TEXT))
    with pytest.raises(Refused, match="nothing to match|one scope"):
        spine.forget(A, actor=A, **scope)
    assert SENTINEL in texts(spine.replay("all"))


def test_a_topic_that_matches_too_many_events_is_refused_and_nothing_changes(spine):
    for i in range(MAX_TOPIC_EVENTS + 1):
        spine.publish(say(A, f"heart rate was {60 + i}, {SENTINEL}"))
    with pytest.raises(Refused, match="say which part"):
        spine.forget(A, actor=A, topic="heart")
    assert texts(spine.replay("all")).count(SENTINEL) == MAX_TOPIC_EVENTS + 1
    assert spine.forget(A, actor=A, topic="heart rate 60").count == 1     # narrower: fine


def test_only_the_person_may_forget_by_entity_or_topic(spine):
    spine.publish(say(A, ENTITY_TEXT))
    for scope in ({"entity": NAME}, {"topic": "code"}):
        with pytest.raises(Refused, match="forget"):
            spine.forget(A, actor=B, **scope)
    assert SENTINEL in texts(spine.replay("all"))


# ---- HONEST LIMITS: what a scoped forget does not catch (these assert the leak) -------------------
def test_HONEST_LIMIT_reworded_content_and_near_spellings_get_through(spine):
    live = forgotten_name(spine)
    spine.forget(A, actor=A, topic="code")
    for reworded in ("that friend with the feather surname rang again",    # no name, new words
                     "Zorvan Quilfeather rang again",                      # a near spelling
                     "Z. Quillfeather rang again",                         # part of the name
                     "Z o r v a n e Quillfeather rang again"):             # the name, spaced out
        assert spine.publish(offer(A, reworded, live)).seq


# ---- through the socket: a surface relays the scoped forget, a skill process meets the shield ----
def test_an_entity_forget_through_the_socket_shields_the_name_from_a_skill_process():
    from procs import HELPER, Box
    from world import SAM
    box = Box()
    try:
        panel, voice, helper = box.client("panel"), box.client("voice"), box.client(HELPER)
        panel.grant(SAM, HELPER)
        voice.publish("utterance", subject=SAM, payload={"text": ENTITY_TEXT})
        live = voice.publish("utterance", subject=SAM, payload={"text": "something else"})
        assert panel.forget(SAM, actor=SAM, entity=NAME)["count"] == 1
        with pytest.raises(Refused, match="names a forgotten entity"):
            helper.publish("offer", subject=SAM, caused_by=[live.seq],
                           payload={"offer_id": "o", "text": "Call Zorvane Quillfeather?"})
        with pytest.raises(Refused, match="trusted surface"):
            helper.forget(SAM, actor=SAM, topic="something")
        assert SENTINEL.encode() not in box.on_disk()
    finally:
        box.close()
