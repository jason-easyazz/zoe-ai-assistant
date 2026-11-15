"""A whole-person FORGET notice carries no subject. A remote skill (``Client.run`` against a
kernel in another process) cannot resolve it, so it must treat the notice as possibly any
member, never as nobody. State whose source already left the log (retired by retention) is
in no notice list: it is the case that needs this rule.

Mechanism (``provenance.still_live``): keep only state whose source the skill can still read
on the live log, drop the rest whoever it belonged to. Another member's state with a live
source survives; their state whose source had also been retired is dropped and relearned
from new events. In process, ``Context`` resolves the subject and scopes exactly.
"""
from __future__ import annotations

import pytest
from procs import Box
from world import ALEX, SAM, household, presence

from zoe_os.skills import leaving_catch
from zoe_os.skills.leaving_catch import LeavingCatch
from zoe_os.twin import baseline
from zoe_os.twin.baseline import Twin


@pytest.fixture
def box():
    b = Box()
    b.cfg["install"].append(Twin.manifest)
    b.restart()
    yield b
    b.close()


def seqs(twin, person):
    return {v[1] for facts in twin.days.get(person, {}).values() for v in facts.values()}


def remote_twin_scene(box):
    panel, sensor = box.client("panel"), box.client("presence")
    for person in (ALEX, SAM):
        panel.grant(person, "twin")
    old_a = sensor.publish("presence", subject=ALEX, payload={"state": "up", "confidence": 0.95, "room": "kitchen"})
    old_b = sensor.publish("presence", subject=SAM, payload={"state": "up", "confidence": 0.95})
    live_b = sensor.publish("presence", subject=SAM, payload={"state": "left", "confidence": 0.95})
    twin, client = Twin(), box.client("twin")
    assert client.run(twin, from_seq=0) == 3
    assert seqs(twin, ALEX) == {old_a.seq} and seqs(twin, SAM) == {old_b.seq, live_b.seq}
    box.sup("retire", seqs=[old_a.seq, old_b.seq])       # retention took the sources away
    panel.forget(ALEX, actor=ALEX)
    return twin, client, live_b


def test_remote_twin_holds_nothing_for_the_forgotten_member(box):
    twin, client, live_b = remote_twin_scene(box)
    assert client.run(twin, from_seq=live_b.seq) >= 1     # the anonymous notice(s)
    assert ALEX not in twin.days
    assert seqs(twin, SAM) == {live_b.seq}                 # live source kept, retired one dropped
    back = box.client("presence").publish("presence", subject=SAM,
                                          payload={"state": "arrived", "confidence": 0.95})
    client.run(twin, from_seq=back.seq - 1)
    assert seqs(twin, SAM) == {live_b.seq, back.seq}       # and it goes on learning


def test_control_remote_twin_treating_the_notice_as_nobody_keeps_the_stale_fact(box, monkeypatch):
    monkeypatch.setattr(baseline, "unresolved_person", lambda notice, ctx: False)
    twin, client, live_b = remote_twin_scene(box)
    client.run(twin, from_seq=live_b.seq)
    assert ALEX in twin.days                               # the leak this rule closes


def remote_catch_scene(box):
    panel, routines = box.client("panel"), box.client("routines")
    for person in (ALEX, SAM):
        panel.grant(person, "leaving-catch")

    def undone(person, item):
        return routines.publish("routine_step", subject=person, payload={
            "routine": "take-with-you", "item": item, "done": False})
    old_a, old_b, live_b = undone(ALEX, "synthetic umbrella"), undone(SAM, "synthetic keys"), \
        undone(SAM, "synthetic badge")
    catch, client = LeavingCatch(), box.client("leaving-catch")
    client.run(catch, from_seq=0)
    assert set(catch._undone) == {ALEX, SAM}
    box.sup("retire", seqs=[old_a.seq, old_b.seq])
    box.client("panel").forget(ALEX, actor=ALEX)
    client.run(catch, from_seq=live_b.seq)
    return catch, live_b


def test_remote_leaving_catch_holds_nothing_for_the_forgotten_member(box):
    catch, live_b = remote_catch_scene(box)
    assert catch._undone == {SAM: {"synthetic badge": live_b.seq}}


def test_control_remote_leaving_catch_without_the_rule_keeps_the_stale_item(box, monkeypatch):
    monkeypatch.setattr(leaving_catch, "unresolved_person", lambda notice, ctx: False)
    catch, _ = remote_catch_scene(box)
    assert ALEX in catch._undone


def test_in_process_context_resolves_the_subject_and_scopes_exactly(tmp_path):
    k = household(tmp_path)
    try:
        twin = Twin()
        k.install(twin)
        for person in (ALEX, SAM):
            k.grants.grant(person, "twin")
        old_a, old_b = presence(k, ALEX, "up"), presence(k, SAM, "up")
        live_b = presence(k, SAM, "left")
        k.pump()
        k.spine._expire([old_a.seq, old_b.seq])
        k.spine._retire([old_a.seq, old_b.seq])
        k.spine.forget(ALEX, actor=ALEX)
        k.pump()
        assert ALEX not in twin.days
        assert seqs(twin, SAM) == {old_b.seq, live_b.seq}  # nothing of SAM's is lost in process
    finally:
        k.close()
