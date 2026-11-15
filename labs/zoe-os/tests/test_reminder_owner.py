"""A reminder id names one person's row: an action for A cannot change B's reminder (rule 6).

Two people have both enabled the reminders skill. The kernel checks A's grant, A's cause and A's
own yes, all of which a skill acting for A can honestly have; it never reads a store row, so it
cannot see that the id A's action names is B's. The store refuses that the way it refuses an id
that does not exist, so ids cannot be confirmed across members. The people and words are invented.
"""
from __future__ import annotations

import pytest
from reminder_store import ReminderStore
from test_reminders import ask, respond, tick
from world import ALEX, NOW, SAM, household

from zoe_os.skills.reminders import Reminders

MISSING = "r987654"      # an id no row has


def build(tmp_path):
    k = household(tmp_path)
    k.now = [NOW]
    k.clock = lambda: k.now[0]
    k.grants.install({"skill": "clock", "emits": ["timer_fired"], "origin": True})
    k.grants.install({"skill": "cards", "reads": {"offer": "private"}})
    k.skill, k.store = Reminders(), ReminderStore().register(k)
    k.install(k.skill)
    for person in (ALEX, SAM):
        k.grants.grant(person, "reminders")
    k.grants.grant(SAM, "cards")
    return k


class Reach(Reminders):
    """On SAM's honest yes to SAM's own reminder, act FOR SAM on another id."""
    action, target, got = "snooze", None, []

    def _answered(self, ev, ctx) -> None:
        args = {"id": self.target} | ({"until": ctx.now() + 600} if self.action == "snooze" else {})
        self.got.append(ctx.act(f"reminder.{self.action}", ev.subject, args, caused_by=(ev.seq,)))


def reach(tmp_path, action, target_alex: bool):
    """SAM's action names ALEX's reminder (or an id that does not exist). Returns the kernel,
    ALEX's id, ALEX's row before, and what the receipt says."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    k = build(tmp_path)
    mine, theirs = ask(k, SAM, "sam's reminder", NOW), ask(k, ALEX, "zx-owner-alex-plum-7720", NOW)
    before = dict(k.store.rows[theirs])
    Reach.got = []
    k.skill.__class__ = Reach
    k.skill.action, k.skill.target = action, theirs if target_alex else MISSING
    respond(k, next(o for o in tick(k) if o.subject == SAM))       # SAM's own yes
    (r,) = Reach.got
    ev = k.spine._get(r.seq)
    assert ev.subject == SAM
    k.receipt = ev.payload
    return k, theirs, before, (r.verified, r.outcome, ev.payload["error"], ev.payload["reversible"])


@pytest.mark.parametrize("action", ["snooze", "ack", "delete"])
def test_one_persons_action_cannot_change_anothers_reminder_by_id(tmp_path, action):
    k, theirs, before, seen = reach(tmp_path / "a", action, target_alex=True)
    try:
        assert k.store.rows[theirs] == before, "nothing changed for ALEX"
        assert "zx-owner-alex-plum-7720" not in str(k.receipt), "ALEX's row is never copied into SAM's receipt"
        assert (k.receipt.get("undo") or {}).get("before") in (None, {"row": None})
        if action != "delete":       # recorded: an unverified receipt, the driver refused
            assert seen[:3] == (False, "unverified", "driver_failed")
    finally:
        k.close()
    m, _, _, missing = reach(tmp_path / "b", action, target_alex=False)
    m.close()
    assert seen == missing, "ALEX's id answers exactly like an id that does not exist"


@pytest.mark.parametrize("action", ["snooze", "ack", "delete"])
def test_a_persons_own_reminder_still_changes(tmp_path, action):
    k = build(tmp_path)
    try:
        mine = ask(k, SAM, "sam's reminder", NOW)
        (offer,) = [o for o in tick(k) if o.subject == SAM]
        if action == "delete":
            utter = k.spine._get(int(mine[1:]))
            assert k.actions.act("reminders", "reminder.delete", SAM, {"id": mine},
                                 caused_by=(utter.seq,)).verified and mine not in k.store.rows
            return
        respond(k, offer, **({"choice": "snooze", "minutes": 10} if action == "snooze" else {}))
        row = k.store.rows[mine]
        assert row["due"] == NOW + 600 if action == "snooze" else row["acknowledged"]
    finally:
        k.close()


def test_create_never_takes_over_another_persons_id(tmp_path):
    k = build(tmp_path)
    try:
        theirs = ask(k, ALEX, "zx-owner-alex-plum-7720", NOW)
        before = dict(k.store.rows[theirs])
        said = k.spine._get(int(ask(k, SAM, "sam's reminder", NOW)[1:]))
        r = k.actions.act("reminders", "reminder.create", SAM, {"id": theirs, "at": NOW, "note": "sam's"},
                          caused_by=(said.seq,))
        assert not r.verified and k.store.rows[theirs] == before
    finally:
        k.close()


def test_the_erasure_duty_still_reaches_every_owner(tmp_path):
    """The purge path is owner-blind on purpose: forgetting a request removes its reminder."""
    k = build(tmp_path)
    try:
        theirs = ask(k, ALEX, "zx-owner-alex-plum-7720", NOW)
        k.spine.forget(ALEX, actor=ALEX)
        k.pump()
        assert theirs not in k.store.rows
    finally:
        k.close()


@pytest.mark.parametrize("action", ["snooze", "ack", "delete"])
def test_CONTROL_without_the_owner_check_sams_action_changes_alexs_reminder(tmp_path, monkeypatch, action):
    monkeypatch.setattr(ReminderStore, "_own", lambda self, person, rid: self.rows.get(rid))
    k, theirs, before, _ = reach(tmp_path, action, target_alex=True)
    try:
        assert k.store.rows.get(theirs) != before
    finally:
        k.close()



def test_CONTROL_without_the_owner_check_create_takes_over_alexs_row(tmp_path, monkeypatch):
    monkeypatch.setattr(ReminderStore, "_own", lambda self, person, rid: self.rows.get(rid))
    k = build(tmp_path)
    try:
        theirs = ask(k, ALEX, "zx-owner-alex-plum-7720", NOW)
        said = k.spine._get(int(ask(k, SAM, "sam's reminder", NOW)[1:]))
        k.actions.act("reminders", "reminder.create", SAM, {"id": theirs, "at": NOW, "note": "sam's"},
                      caused_by=(said.seq,))
        assert k.store.rows[theirs]["owner"] == SAM
    finally:
        k.close()
