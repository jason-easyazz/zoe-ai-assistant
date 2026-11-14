"""The malicious skill: three attacks, every attempt refused and recorded.

Then the same attacks with one guarantee removed each time. If a control does not show the
attack SUCCEEDING, the refusal above it proves nothing.
"""
from __future__ import annotations

import glob

import pytest
from world import ALEX, SAM, emit, household, of_kind, presence, refusal_reasons

from zoe_os.capability import Enforcer
from zoe_os.events import Kind, Visibility
from zoe_os.receipts import Dispatcher
from zoe_os.skills.malicious import Malicious
from zoe_os.spine import Decision, Spine

VICTIM_SECRET = "zx-victim-secret-amber-lantern-4410"
FORGOTTEN = "zx-forgotten-words-teal-meadow-9056"


def say(k, person, text, visibility=Visibility.PRIVATE):
    return emit(k, Kind.UTTERANCE, "voice", person, visibility, text=text)


@pytest.fixture
def scene(tmp_path):
    """ALEX never enabled the helper. SAM did, said something, then asked to forget it."""
    k = household(tmp_path)
    bad = Malicious()
    k.install(bad)
    k.grants.grant(SAM, bad.name)
    say(k, ALEX, VICTIM_SECRET)
    presence(k, ALEX, "home", visibility=Visibility.HOUSEHOLD)
    say(k, SAM, FORGOTTEN)
    k.pump()                                    # the helper legitimately sees SAM's words
    assert FORGOTTEN in str([e.payload for e in bad.cache])
    yield k, bad
    k.close()


def forget_sam(k) -> int:
    k.spine.forget(SAM, actor=SAM)
    return say(k, SAM, "something new and harmless").seq      # a live event the helper may cite


def test_attack_1_reading_another_persons_private_events(scene):
    k, bad = scene
    assert bad.read_private_of(k, ALEX) == [e for e in bad.cache if e.kind is Kind.PRESENCE]
    assert VICTIM_SECRET not in str([e.payload for e in bad.cache])
    assert refusal_reasons(k) == ["no grant from that person"] * 2 + ["asks for more than its manifest"]


def test_attack_2_acting_without_a_grant(scene):
    k, bad = scene
    cause = of_kind(k, Kind.UTTERANCE)[-1].seq
    assert bad.act_without_grant(k, ALEX, cause) == []
    assert k.lights.toggled == [] and k.alarms.at == {}
    assert refusal_reasons(k) == ["no grant from that person", "asks for more than its manifest"]


def test_attack_3_resurrecting_a_forgotten_subject(scene):
    k, bad = scene
    live = forget_sam(k)
    assert bad.resurrect(k, SAM, live) == []
    assert refusal_reasons(k) == [
        "derives from a forgotten event", "carries forgotten content", "carries forgotten content",
        "asks for more than its manifest", "kernel-only kind"]
    for reader in ("audit", bad.name):
        assert FORGOTTEN not in str(k.spine.export(reader))
    raw = b"".join(open(p, "rb").read() for p in glob.glob(k.path + "*"))
    assert FORGOTTEN.encode() not in raw
    assert FORGOTTEN in str([e.payload for e in bad.cache])   # honest limit: its own memory


def test_every_refusal_is_an_event_an_auditor_can_read_and_none_carries_content(scene):
    k, bad = scene
    bad.read_private_of(k, ALEX)
    bad.act_without_grant(k, ALEX, 1)
    bad.resurrect(k, SAM, forget_sam(k))
    recorded = [e for e in k.spine.replay("audit") if e.kind is Kind.REFUSAL]
    assert len(recorded) == len(bad.refused) == k.spine.refusals == 10
    assert {e.payload["who"] for e in recorded} == {bad.name}
    assert VICTIM_SECRET not in str([e.payload for e in recorded])
    assert FORGOTTEN not in str([e.payload for e in recorded])


# ---- negative controls: remove one guarantee, the attack must succeed ------------------------
def test_control_without_the_capability_check_private_events_are_read(scene, monkeypatch):
    k, bad = scene
    monkeypatch.setattr(Enforcer, "may_read", lambda self, reader, ev: Decision(True))
    monkeypatch.setattr(Enforcer, "may_subscribe", lambda self, r, kinds, s: Decision(True))
    assert VICTIM_SECRET in str([e.payload for e in bad.read_private_of(k, ALEX)])


def test_control_without_the_capability_check_the_action_runs(scene, monkeypatch):
    k, bad = scene
    monkeypatch.setattr(Dispatcher, "_authorize", lambda self, s, a, p: Decision(True, "", "none"))
    done = bad.act_without_grant(k, ALEX, of_kind(k, Kind.UTTERANCE)[-1].seq)
    assert k.lights.toggled == ["study"] and [r.action for r in done] == ["light.toggle"]
    assert k.alarms.at == {}          # the second layer still held: nobody said yes to an alarm


def test_control_without_the_tombstone_the_forgotten_words_replay(scene, monkeypatch):
    k, bad = scene
    monkeypatch.setattr(Spine, "_tombstone", lambda self, seqs: None)
    back = bad.resurrect(k, SAM, forget_sam(k))
    assert FORGOTTEN in str([e.payload for e in back])


def test_control_without_the_shield_the_forgotten_words_are_republished(scene, monkeypatch):
    k, bad = scene
    monkeypatch.setattr(Spine, "_shielded", lambda self, payload: False)
    back = bad.resurrect(k, SAM, forget_sam(k))
    assert len(back) == 2 and FORGOTTEN in str(k.spine.export("audit") + [e.payload for e in back])
