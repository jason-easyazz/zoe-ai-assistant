"""An UNRESOLVED whole-person notice must not cost another member a reminder.

Each member has a reminder whose source request has been retired. The kernel's whole-member
sweep removes only the forgotten member's store rows, so the skill's cache must keep every
reminder the store still holds and that can still fire. In process the notice is made
unresolvable (the context resolves nobody); remotely it is the normal anonymous notice.
"""
from __future__ import annotations

import pytest
from reminder_store import ReminderStore
from world import ALEX, NOW, SAM, emit, household, of_kind

from zoe_os.events import Kind, Visibility
from zoe_os.kernel import Context
from zoe_os.skills.reminders import Reminders

HOUR = 3600


@pytest.fixture
def k(tmp_path):
    kernel = household(tmp_path)
    kernel.now = [NOW]
    kernel.clock = lambda: kernel.now[0]
    kernel.grants.install({"skill": "clock", "emits": ["timer_fired"], "origin": True})
    kernel.skill, kernel.store = Reminders(), ReminderStore().register(kernel)
    kernel.install(kernel.skill)
    for person in (ALEX, SAM):
        kernel.grants.grant(person, "reminders")
    yield kernel
    kernel.close()


def scene(k):
    rids = {}
    for person in (ALEX, SAM):
        ev = emit(k, Kind.UTTERANCE, "voice", person, text="synthetic ask",
                  intent={"name": "reminder.create", "at": NOW + HOUR, "note": "synthetic note"})
        rids[person] = f"r{ev.seq}"
    k.pump()
    asked = [int(r[1:]) for r in rids.values()]
    k.spine._expire(asked)
    k.spine._retire(asked)                       # the requests are gone; the receipts remain
    assert set(k.skill._items) == set(k.store.rows) == set(rids.values())
    return rids


def fired(k):
    k.now[0] = NOW + 2 * HOUR
    emit(k, Kind.TIMER_FIRED, "clock", None, Visibility.HOUSEHOLD, tick=True)
    k.pump()
    return {(o.subject, o.payload.get("reminder_id")) for o in of_kind(k, Kind.OFFER)}


@pytest.mark.parametrize("resolved", [False, True], ids=["unresolved", "resolved"])
def test_whole_forget_keeps_and_fires_the_other_members_reminder(k, monkeypatch, resolved):
    rids = scene(k)
    if not resolved:
        monkeypatch.setattr(Context, "forgotten_subject", lambda self, notice: None)   # names nobody
    k.spine.forget(ALEX, actor=ALEX)
    k.pump()
    assert set(k.store.rows) == set(k.skill._items) == {rids[SAM]}
    assert fired(k) == {(SAM, rids[SAM])}


def test_a_reminder_whose_create_receipt_is_gone_can_never_fire(k):
    """Why the unresolved rule may drop such an item: without any forget it is already inert."""
    rids = scene(k)
    receipts = [e.seq for e in of_kind(k, Kind.ACTION_RECEIPT)]
    k.spine._expire(receipts)
    k.spine._retire(receipts)
    assert set(k.skill._items) == set(rids.values())
    assert fired(k) == set()


# ---- the remote path: Client.run against a kernel in its own process ---------------------------
def test_remote_whole_forget_keeps_and_fires_the_other_members_reminder():
    import time
    from procs import Box
    box = Box()
    try:
        box.cfg["install"] += [Reminders.manifest, {"skill": "clock", "emits": ["timer_fired"], "origin": True}]
        box.cfg["reminder_store"] = True
        box.restart()
        panel, voice = box.client("panel"), box.client("voice")
        for person in (ALEX, SAM):
            panel.grant(person, "reminders")
        panel.grant(SAM, "cards")
        due = time.time() - 60                    # the remote skill's clock is the wall clock
        rids = {p: f"r{voice.publish('utterance', subject=p, payload={'text': 'synthetic ask', 'intent': {'name': 'reminder.create', 'at': due, 'note': 'synthetic note'}}).seq}"
                for p in (ALEX, SAM)}
        skill, client = Reminders(), box.client("reminders")
        client.run(skill, from_seq=0)
        assert set(skill._items) == set(rids.values())
        box.sup("retire", seqs=[int(r[1:]) for r in rids.values()])
        mark = box.sup("last")["seq"]
        panel.forget(ALEX, actor=ALEX)
        client.run(skill, from_seq=mark)
        assert set(skill._items) == {rids[SAM]}
        assert box.sup("reminder_rows")["rows"] == [[rids[SAM], SAM]]
        tick = box.client("clock").publish("timer_fired", subject=None, visibility="household",
                                           payload={"tick": True})
        client.run(skill, from_seq=tick.seq - 1)
        cards = box.client("cards")
        offers = [e for e in cards.drain(cards.subscribe(from_seq=0)) if e.kind is Kind.OFFER]
        assert [(o.subject, o.payload["reminder_id"]) for o in offers] == [(SAM, rids[SAM])]
    finally:
        box.close()
