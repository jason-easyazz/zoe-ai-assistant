"""A forget notice is never erased without its duty being re-delivered.

A scoped notice names its subject in clear, so a later whole-person forget must erase it.
A subscriber that had not processed it yet would then never learn the ids it listed, and
cross-owner state derived from them (another member's) would survive. The replacing notice
carries those ids, anonymously. Controls drop the carry.
"""
from __future__ import annotations

import pytest
from procs import Box
from spine_helpers import AllowList
from test_history_cost import vm_budget
from world import ALEX, SAM, emit, household

from zoe_os.events import Event, Kind
from zoe_os.spine import Spine
from zoe_os.twin.baseline import Twin


def seqs(twin, person):
    return {v[1] for facts in twin.days.get(person, {}).values() for v in facts.values()}


@pytest.fixture
def k(tmp_path):
    kernel = household(tmp_path)
    kernel.twin = Twin()
    kernel.install(kernel.twin)
    for person in (ALEX, SAM):
        kernel.grants.grant(person, "twin")
    yield kernel
    kernel.close()


def derived(k):
    """A's utterance, and B's presence derived from it (cross-owner)."""
    said = emit(k, Kind.UTTERANCE, "voice", ALEX, text="synthetic words")
    seen = emit(k, Kind.PRESENCE, "presence", SAM, cause=(said.seq,), state="up", confidence=0.95)
    return said, seen


def test_whole_forget_before_pump_still_reaches_cross_owner_state(k):
    said, seen = derived(k)
    k.pump()
    assert seqs(k.twin, SAM) == {seen.seq}
    k.spine.forget(ALEX, actor=ALEX, seqs=[said.seq])      # scoped: lists said and seen
    k.spine.forget(ALEX, actor=ALEX)                       # erases that notice before anyone read it
    notices = [e for e in k.spine._fetch(0, frozenset({Kind.FORGET}), None, 100)]
    assert all('subject' not in n.payload for n in notices)  # the survivor names nobody
    k.pump()
    assert SAM not in k.twin.days


def test_control_without_the_carry_the_cross_owner_fact_survives(k, monkeypatch):
    monkeypatch.setattr(Spine, "_carried", lambda self, dead: set())
    said, seen = derived(k)
    k.pump()
    k.spine.forget(ALEX, actor=ALEX, seqs=[said.seq])
    k.spine.forget(ALEX, actor=ALEX)
    k.pump()
    assert seqs(k.twin, SAM) == {seen.seq}


def test_a_chain_of_two_scoped_forgets_then_a_whole_one(k):
    first, second = derived(k), derived(k)
    k.pump()
    for said, _ in (first, second):
        k.spine.forget(ALEX, actor=ALEX, seqs=[said.seq])
    k.spine.forget(ALEX, actor=ALEX)
    k.pump()
    assert SAM not in k.twin.days


def test_a_subscriber_that_already_processed_the_notice_is_unaffected(k):
    said, seen = derived(k)
    k.pump()
    k.spine.forget(ALEX, actor=ALEX, seqs=[said.seq])
    k.pump()
    assert SAM not in k.twin.days
    own = emit(k, Kind.PRESENCE, "presence", SAM, state="left", confidence=0.95)
    k.pump()
    k.spine.forget(ALEX, actor=ALEX)                       # re-delivers said and seen: no-ops now
    k.pump()
    assert seqs(k.twin, SAM) == {own.seq}


@pytest.mark.parametrize("size", [200, 5000])
def test_carrying_is_bounded_by_the_erased_notices(tmp_path, size):
    s = Spine(str(tmp_path / "carry.db"), AllowList(origins={"voice"}, readers={"all": {"*"}}))
    try:
        with s._lock:
            s._append([Event(Kind.UTTERANCE, "voice", SAM, payload={"text": "synthetic"})
                       for _ in range(size)])
        said = s.publish(Event(Kind.UTTERANCE, "voice", ALEX, payload={"text": "synthetic a"}))
        s.forget(ALEX, actor=ALEX, seqs=[said.seq])
        with vm_budget(s._db):
            s.forget(ALEX, actor=ALEX)
        (notice,) = [e for e in s.replay("all") if e.kind is Kind.FORGET]
        assert said.seq in notice.payload["seqs"]
    finally:
        s.close()


def test_remote_twin_through_client_run():
    """Remotely the notice is unresolved, so the "keep only what is still readable" rule also
    drops the fact (its source is erased); the carried ids reach it either way."""
    box = Box()
    try:
        box.cfg["install"].append(Twin.manifest)
        box.restart()
        panel = box.client("panel")
        for person in (ALEX, SAM):
            panel.grant(person, "twin")
        said = box.client("voice").publish("utterance", subject=ALEX, payload={"text": "synthetic words"})
        seen = box.client("presence").publish("presence", subject=SAM, caused_by=[said.seq],
                                              payload={"state": "up", "confidence": 0.95})
        twin, client = Twin(), box.client("twin")
        client.run(twin, from_seq=0)
        assert seqs(twin, SAM) == {seen.seq}
        panel.forget(ALEX, actor=ALEX, seqs=[said.seq])
        panel.forget(ALEX, actor=ALEX)
        client.run(twin, from_seq=seen.seq)
        assert SAM not in twin.days
    finally:
        box.close()
