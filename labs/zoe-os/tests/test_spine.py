"""The event spine: schema, default deny, durable order, bounded queues, replay.

Hand-run (never CI: ``pytest.ini`` testpaths does not collect ``labs/``):
    python3 -m pytest -p no:cacheprovider -q labs/zoe-os/tests
"""
from __future__ import annotations

import threading

import pytest
from spine_helpers import AllowList

from zoe_os.events import Event, Kind, Visibility
from zoe_os.spine import Refused, Spine


def say(person: str, text: str, **kw) -> Event:
    return Event(Kind.UTTERANCE, "voice", subject=person, payload={"text": text}, **kw)


@pytest.fixture
def auth():
    return AllowList(origins={"voice"}, skills={"skill"},
                     readers={"a-reader": {"person-a"}, "all": {"*"}, "audit": {"kernel"}})


@pytest.fixture
def spine(tmp_path, auth):
    s = Spine(str(tmp_path / "spine.db"), auth, queue_size=4)
    yield s
    s.close()


def drain(sub) -> list[Event]:
    out = []
    while (ev := sub.poll()) is not None:
        out.append(ev)
    return out


# ---- schema -------------------------------------------------------------------------
def test_kinds_are_a_closed_set():
    with pytest.raises(ValueError):
        Event("made_up_kind", "voice", subject="person-a")


def test_private_is_the_default_and_needs_a_subject():
    assert say("person-a", "hello").visibility is Visibility.PRIVATE
    with pytest.raises(ValueError):
        Event(Kind.UTTERANCE, "voice", payload={"text": "to nobody"})


def test_required_payload_keys():
    with pytest.raises(ValueError):
        Event(Kind.PRESENCE, "presence", subject="person-a", payload={"state": "left"})


# ---- default deny -------------------------------------------------------------------
def test_no_authorizer_means_nothing_is_allowed(tmp_path, auth):
    s = Spine(str(tmp_path / "deny.db"))
    with pytest.raises(Refused):
        s.publish(say("person-a", "hello"))
    with pytest.raises(Refused):
        s.subscribe("anyone")
    with pytest.raises(Refused):
        list(s.replay("anyone"))
    assert s.refusals == 3
    s.authorizer = auth  # an auditor can now see that each refusal was recorded
    refusals = [e for e in s.replay("audit") if e.kind is Kind.REFUSAL]
    assert [r.payload["who"] for r in refusals] == ["voice", "anyone", "anyone"]
    assert all("hello" not in str(r.payload) for r in refusals)  # no content in a refusal


def test_kernel_only_kinds_cannot_be_published(spine):
    for kind in (Kind.FORGET, Kind.ACTION_RECEIPT, Kind.GRANT_CHANGE, Kind.REFUSAL):
        with pytest.raises(Refused, match="kernel-only"):
            spine.publish(Event(kind, "voice", visibility=Visibility.KERNEL))


# ---- provenance ---------------------------------------------------------------------
def test_a_skill_must_cite_a_cause_it_may_read(spine, auth):
    seen = spine.publish(say("person-a", "private words"))
    offer = {"offer_id": "o1", "text": "shall I?"}
    with pytest.raises(Refused, match="derives from"):
        spine.publish(Event(Kind.OFFER, "skill", subject="person-a", payload=offer))
    with pytest.raises(Refused, match="does not exist"):
        spine.publish(Event(Kind.OFFER, "skill", subject="person-a", payload=offer,
                            caused_by=(999,)))
    with pytest.raises(Refused, match="may not read"):
        spine.publish(Event(Kind.OFFER, "skill", subject="person-a", payload=offer,
                            caused_by=(seen.seq,)))
    auth.readers["skill"] = {"person-a"}
    ok = spine.publish(Event(Kind.OFFER, "skill", subject="person-a", payload=offer,
                             caused_by=(seen.seq,)))
    assert ok.caused_by == (seen.seq,)


def test_a_batch_with_one_refused_event_appends_none(spine):
    before = spine.last_seq
    with pytest.raises(Refused):
        spine.publish_many([say("person-a", "one"),
                            Event(Kind.UTTERANCE, "stranger", subject="person-a",
                                  payload={"text": "two"})])
    assert [e.kind for e in spine.replay("all", before)] == []


# ---- durable order ------------------------------------------------------------------
def test_sequence_is_monotonic_and_survives_a_restart(tmp_path, auth):
    path = str(tmp_path / "s.db")
    s = Spine(path, auth)
    assert [s.publish(say("person-a", f"m{i}")).seq for i in range(3)] == [1, 2, 3]
    s.close()
    s = Spine(path, auth)
    assert s.last_seq == 3
    assert s.publish(say("person-a", "m3")).seq == 4
    assert [e.payload["text"] for e in s.replay("all")] == ["m0", "m1", "m2", "m3"]
    assert [e.seq for e in s.replay("all", from_seq=2)] == [3, 4]
    s.close()


# ---- capability-filtered reads --------------------------------------------------------
def test_a_reader_only_receives_what_it_is_granted(spine):
    sub = spine.subscribe("a-reader")
    spine.publish(say("person-a", "for a"))
    spine.publish(say("person-b", "for b"))
    assert [e.payload["text"] for e in drain(sub)] == ["for a"]
    assert [e.payload["text"] for e in spine.replay("a-reader")] == ["for a"]
    with pytest.raises(Refused, match="no grant from that person"):
        spine.subscribe("a-reader", subject="person-b")


def test_negative_control_without_the_read_check_private_events_leak(spine, monkeypatch):
    spine.publish(say("person-b", "b's private words"))
    assert list(spine.replay("a-reader")) == []
    monkeypatch.setattr(Spine, "_deliverable", lambda self, reader, ev: True)
    assert [e.payload["text"] for e in spine.replay("a-reader")] == ["b's private words"]


def test_a_revoked_reader_loses_events_already_in_its_queue(spine, auth):
    sub = spine.subscribe("a-reader")
    spine.publish(say("person-a", "queued before the revoke"))
    assert len(sub._queue) == 1
    auth.readers["a-reader"] = set()
    assert sub.poll() is None


# ---- backpressure ---------------------------------------------------------------------
def test_a_slow_subscriber_never_blocks_the_publisher_and_loses_nothing(spine):
    sub = spine.subscribe("all", kinds={Kind.UTTERANCE})
    for i in range(50):                      # nobody is polling: the publisher must not wait
        spine.publish(say("person-a", f"m{i}"))
        assert len(sub._queue) <= spine.queue_size
    assert sub.lagged == 1
    got = drain(sub)
    assert [e.payload["text"] for e in got] == [f"m{i}" for i in range(50)]
    assert [e.seq for e in got] == sorted({e.seq for e in got})
    spine.publish(say("person-a", "live again"))   # caught up: back on the live queue
    assert sub.poll().payload["text"] == "live again" and sub.lagged == 1


def test_an_offline_subscriber_resumes_after_its_saved_cursor(spine):
    sub = spine.subscribe("all")
    first = spine.publish(say("person-a", "seen"))
    assert sub.poll().seq == first.seq
    sub.ack(first.seq)
    sub.close()
    spine.publish(say("person-a", "missed 1"))
    spine.publish(say("person-a", "missed 2"))
    back = spine.subscribe("all")
    assert [e.payload["text"] for e in drain(back)] == ["missed 1", "missed 2"]
    fresh = spine.subscribe("audit")           # no cursor: starts at now, not at the beginning
    assert fresh.poll() is None


def test_threaded_publisher_and_consumer_keep_order(spine):
    sub = spine.subscribe("all", kinds={Kind.UTTERANCE})
    worker = threading.Thread(
        target=lambda: [spine.publish(say("person-a", f"m{i}")) for i in range(200)])
    worker.start()
    got = []
    while len(got) < 200:
        ev = sub.poll(timeout=5)
        assert ev is not None, "consumer starved"
        got.append(ev.payload["text"])
    worker.join(5)
    assert got == [f"m{i}" for i in range(200)]
