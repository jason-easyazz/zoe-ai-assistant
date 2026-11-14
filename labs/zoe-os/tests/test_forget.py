"""Forgotten means forever, as a property of the spine and not of each feature.

Every guarantee has a planted sentinel and a negative control: the same scenario with one
mechanism removed must return the sentinel, or the test proves nothing.
"""
from __future__ import annotations

import glob

import pytest
from spine_helpers import AllowList

from zoe_os.events import Event, Kind, Visibility
from zoe_os.spine import Refused, Spine, Subscription

SENTINEL = "zx-sentinel-plum-harbour-7731"
A, B = "person-a", "person-b"


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


def test_forget_removes_a_persons_events_from_replay_and_export(spine):
    spine.publish(say(A, f"my secret is {SENTINEL}"))
    spine.publish(say(B, "b's ordinary words"))
    result = spine.forget(A, actor=A)
    assert result.count == 1
    assert [e.subject for e in spine.replay("all") if e.kind is Kind.UTTERANCE] == [B]
    assert SENTINEL not in str(spine.export("all"))
    after = spine.publish(say(A, "a new day"))        # the person is not banned, the past is gone
    assert after in list(spine.replay("all"))


def test_a_subscriber_that_was_offline_during_the_forget_never_sees_it(spine):
    sub = spine.subscribe("late")
    first = spine.publish(say(B, "seen before going offline"))
    assert sub.poll().seq == first.seq
    sub.ack(first.seq)
    sub.close()
    spine.publish(say(A, SENTINEL))
    spine.publish(say(B, "also missed"))
    spine.forget(A, actor=A)
    got = drain(spine.subscribe("late"))                       # resumes from its cursor
    assert SENTINEL not in texts(got) and "also missed" in texts(got)
    assert SENTINEL not in texts(drain(spine.subscribe("late", from_seq=0)))  # or from the start


def test_negative_control_without_the_tombstone_the_offline_subscriber_gets_it(spine, monkeypatch):
    monkeypatch.setattr(Spine, "_tombstone", lambda self, seqs: None)
    spine.publish(say(A, SENTINEL))
    spine.forget(A, actor=A)
    assert SENTINEL in texts(drain(spine.subscribe("late", from_seq=0)))


def test_an_event_already_queued_for_a_live_subscriber_is_withdrawn(spine):
    sub = spine.subscribe("all", kinds={Kind.UTTERANCE})
    spine.publish(say(A, SENTINEL))
    spine.publish(say(B, "kept"))
    assert SENTINEL in texts(sub._queue)                       # it is sitting in memory
    spine.forget(A, actor=A)
    got = drain(sub)
    assert SENTINEL not in texts(got)
    assert [e.kind for e in got] == [Kind.UTTERANCE, Kind.FORGET]


def test_negative_control_without_the_queue_purge_the_queued_event_is_delivered(spine, monkeypatch):
    monkeypatch.setattr(Subscription, "_purge_forgotten", lambda self: None)
    sub = spine.subscribe("all", kinds={Kind.UTTERANCE})
    spine.publish(say(A, SENTINEL))
    spine.forget(A, actor=A)
    assert SENTINEL in texts(drain(sub))


def test_every_subscriber_is_told_to_forget_and_the_notice_carries_no_content(spine):
    sub = spine.subscribe("all", kinds={Kind.PRESENCE}, subject=B)   # filters do not hide it
    spine.publish(say(A, SENTINEL))
    spine.forget(A, actor=A)
    (notice,) = drain(sub)
    assert notice.kind is Kind.FORGET and notice.payload == {"subject": A, "upto": 1, "count": 1}


def test_events_that_mention_the_person_go_too(spine):
    spine.publish(say(B, f"a said {SENTINEL}", mentions=(A,)))
    spine.forget(A, actor=A)
    assert SENTINEL not in texts(spine.replay("all"))


def test_the_forget_cascades_through_everything_derived(spine):
    src = spine.publish(say(A, SENTINEL))
    other = spine.publish(say(B, "unrelated"))
    first = spine.publish(offer(B, "derived, first hand", src.seq))
    second = spine.publish(offer(B, "derived, second hand", first.seq))
    kept = spine.publish(offer(B, "from b's own words", other.seq))
    assert spine.forget(A, actor=A).count == 3
    live = {e.seq for e in spine.replay("all")}
    assert {src.seq, first.seq, second.seq}.isdisjoint(live) and kept.seq in live


def test_negative_control_without_the_cascade_a_derived_copy_survives(spine, monkeypatch):
    monkeypatch.setattr(Spine, "_closure", lambda self, seed: set(seed))
    src = spine.publish(say(A, "the original"))
    spine.publish(Event(Kind.OFFER, "skill", subject=B, caused_by=(src.seq,),
                        payload={"offer_id": "o", "text": "copy", "copied": SENTINEL}))
    spine.forget(A, actor=A)
    assert SENTINEL in texts(spine.replay("all"))


def test_the_bytes_are_gone_from_the_files_on_disk(spine):
    spine.publish(say(A, SENTINEL))
    assert SENTINEL.encode() in on_disk(spine)                  # positive control: it was there
    spine.forget(A, actor=A)
    assert SENTINEL.encode() not in on_disk(spine)


def test_forgetting_survives_a_restart(spine):
    spine.publish(say(A, SENTINEL))
    live = spine.publish(say(B, "still here"))
    spine.forget(A, actor=A)
    spine.close()
    again = Spine(spine.path, spine.authorizer)
    assert SENTINEL not in texts(again.replay("all"))
    with pytest.raises(Refused, match="forgotten content"):
        again.publish(offer(B, SENTINEL, live.seq))
    again.close()


def test_a_skill_cannot_bring_forgotten_words_back(spine):
    old = spine.publish(say(A, SENTINEL))
    live = spine.publish(say(B, "a live event to cite"))
    spine.forget(A, actor=A)
    with pytest.raises(Refused, match="derives from a forgotten event"):
        spine.publish(offer(A, "harmless", old.seq))
    with pytest.raises(Refused, match="forgotten content"):
        spine.publish(offer(A, SENTINEL.upper() + "  ", live.seq))       # case and spacing
    with pytest.raises(Refused, match="forgotten content"):
        spine.publish(offer(A, "harmless", live.seq, extra=[{"deep": SENTINEL}]))
    assert spine.publish(offer(A, "different words", live.seq)).seq


def test_negative_control_without_the_shield_the_words_come_back(spine, monkeypatch):
    monkeypatch.setattr(Spine, "_shielded", lambda self, payload: False)
    spine.publish(say(A, SENTINEL))
    live = spine.publish(say(B, "a live event to cite"))
    spine.forget(A, actor=A)
    spine.publish(offer(A, SENTINEL, live.seq))
    assert SENTINEL in texts(spine.replay("all"))


def test_saying_it_again_first_hand_is_new_reality_not_resurrection(spine):
    spine.publish(say(A, "buy oat milk"))
    spine.forget(A, actor=A)
    fresh = spine.publish(say(A, "buy oat milk"))            # the voice adapter is an origin
    assert spine.publish(offer(A, "buy oat milk", fresh.seq)).seq


def test_only_the_person_may_forget_their_events(spine):
    spine.publish(say(A, SENTINEL))
    for actor in (B, "skill"):
        with pytest.raises(Refused, match="forget"):
            spine.forget(A, actor=actor)
    assert SENTINEL in texts(spine.replay("all"))
    assert [e.payload["who"] for e in spine.replay("audit") if e.kind is Kind.REFUSAL] == [B, "skill"]


def test_forget_can_name_single_events(spine):
    one = spine.publish(say(A, SENTINEL))
    two = spine.publish(say(A, "keep this one"))
    foreign = spine.publish(say(B, "not a's to forget"))
    assert spine.forget(A, actor=A, seqs=[one.seq, foreign.seq]).count == 1
    assert [e.seq for e in spine.replay("all") if e.kind is Kind.UTTERANCE] == [two.seq, foreign.seq]
