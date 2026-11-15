"""Undo: "undo that" reverses the person's last action, for that person only, once only, with
its own receipt; and an action that cannot be reversed says so and is never faked.

One rule per test, and a negative control for each rule that has a mechanism to remove.
"""
from __future__ import annotations

import glob

import pytest
from world import ALEX, KID, SAM, answer, emit, household, of_kind

from zoe_os.events import Kind
from zoe_os.kernel import Kernel
from zoe_os.receipts import ClaimAuditor, Dispatcher
from zoe_os.spine import Refused

SENTINEL = "zx-sentinel-rust-harbour-6142"
MOVER = {"skill": "mover", "reads": {"calendar_change": "private", "offer_response": "private",
                                     "action_receipt": "private"},
         "emits": ["offer", "claim"], "acts": ["alarm.move", "light.toggle", "message.send"]}


class Outbox:
    """An action with no way back: a sent message."""

    def __init__(self):
        self.sent: list = []

    def do(self, person, args):
        self.sent.append((person, args["to"]))

    def verify(self, person, args, result):
        return {"sent": len(self.sent)}


@pytest.fixture
def k(tmp_path):
    kernel = household(tmp_path)
    kernel.outbox = Outbox()
    kernel.actions.register("message.send", kernel.outbox.do, kernel.outbox.verify, needs_accept=False)
    kernel.grants.install(MOVER)
    for person in (ALEX, SAM):
        kernel.grants.grant(person, "mover")
    kernel.grants.grant(KID, "mover", guardian=ALEX)
    yield kernel
    kernel.close()


def move(k, person, to):
    """One consequential action for ``person``: offer, their yes, alarm.move."""
    cal = emit(k, Kind.CALENDAR_CHANGE, "calendar", person, event_id="e", change="created")
    offer = emit(k, Kind.OFFER, "mover", person, cause=(cal.seq,), offer_id=f"o{cal.seq}", text="move?")
    return k.actions.act("mover", "alarm.move", person, {"to": to}, caused_by=(answer(k, offer).seq,))


def send(k, person):
    cal = emit(k, Kind.CALENDAR_CHANGE, "calendar", person, event_id="e", change="created")
    return k.actions.act("mover", "message.send", person, {"to": "someone"}, caused_by=(cal.seq,))


def reasons(k):
    return [e.payload["reason"] for e in of_kind(k, Kind.REFUSAL)]


# ---- rule 1: the receipt holds enough to reverse it, and the undo leaves its own receipt ---------
def test_undo_puts_back_what_was_there_and_leaves_its_own_receipt(k):
    k.alarms.at[SAM] = "07:00"
    done = move(k, SAM, "05:45")
    stored = k.spine._get(done.seq).payload
    assert done.reversible and stored["undo"] == {"args": {"to": "05:45"}, "before": {"alarm": "07:00"}}
    back = k.actions.undo(SAM, actor=SAM)
    assert k.alarms.at[SAM] == "07:00"
    assert back.verified and back.action == "undo" and back.proof == {"restored": {"alarm": "07:00"}}
    receipt = k.spine._get(back.seq)
    assert receipt.kind is Kind.ACTION_RECEIPT and receipt.subject == SAM
    assert receipt.caused_by == (done.seq,) and receipt.payload["undoes"] == done.seq
    assert receipt.payload["asked_by"] == SAM and not receipt.payload["reversible"]
    assert k.explainer.why(back.seq, person=SAM)["sentence"] == (
        f"alarm.move (#{done.seq}) was undone for sam because sam asked, and the read-back confirmed it.")


def test_undo_of_a_first_alarm_removes_it(k):
    move(k, SAM, "05:45")
    assert k.actions.undo(SAM, actor=SAM).verified and SAM not in k.alarms.at


def test_an_undo_that_did_nothing_is_not_called_done(k):
    """Never faked: a driver that reports success and restores nothing gets an unverified receipt."""
    k.alarms.at[SAM] = "07:00"
    move(k, SAM, "05:45")
    k.actions._reverse["alarm.move"] = (k.alarms.snapshot, lambda person, args, before: None)
    back = k.actions.undo(SAM, actor=SAM)
    assert k.alarms.at[SAM] == "05:45" and not back.verified and back.outcome == "unverified"
    claim = emit(k, Kind.CLAIM, "mover", SAM, cause=(back.seq,), action="undo", receipt_seq=back.seq)
    assert [c.seq for c in ClaimAuditor(k.spine, k.enforcer).scan()] == [claim.seq]


# ---- rule 2: only the person whose action it was, or a guardian for a child ---------------------
def test_only_the_person_or_a_childs_guardian_may_undo(k):
    move(k, SAM, "05:45")
    move(k, KID, "06:15")
    for actor in (ALEX, KID, "mover", "nobody"):
        with pytest.raises(Refused, match="not theirs to undo"):
            k.actions.undo(SAM, actor=actor)
    with pytest.raises(Refused, match="not theirs to undo"):
        k.actions.undo(ALEX, actor=KID)
    assert k.alarms.at == {SAM: "05:45", KID: "06:15"}
    assert k.actions.undo(KID, actor=ALEX).verified and KID not in k.alarms.at      # the guardian
    assert reasons(k) == ["not theirs to undo"] * 5


def test_control_without_the_ownership_check_anyone_reverses_anyones_action(k, monkeypatch):
    move(k, SAM, "05:45")
    monkeypatch.setattr(Dispatcher, "_may_undo", lambda self, actor, person: True)
    k.actions.undo(SAM, actor=ALEX)
    assert SAM not in k.alarms.at


# ---- rule 3: once only, and that survives a restart ----------------------------------------------
def test_an_action_is_undone_once_even_after_a_restart(k, tmp_path):
    k.alarms.at[SAM] = "07:00"
    done = move(k, SAM, "05:45")
    k.actions.undo(SAM, actor=SAM)
    with pytest.raises(Refused, match="the last action was itself an undo"):
        k.actions.undo(SAM, actor=SAM)
    assert k.actions._undone(done.seq) and k.alarms.at[SAM] == "07:00"
    k.close()
    again = Kernel(k.path, list(k.grants.people.values()))
    assert again.actions._undone(done.seq) and again.actions._cannot_undo(again.spine._get(done.seq)) == (
        "that was already undone")
    again.close()


def test_control_without_the_once_rule_a_second_undo_clobbers_a_later_change(k, monkeypatch):
    k.alarms.at[SAM] = "07:00"
    done = move(k, SAM, "05:45")
    k.actions.undo(SAM, actor=SAM)
    k.alarms.at[SAM] = "09:30"                                 # SAM set it by hand afterwards
    monkeypatch.setattr(Dispatcher, "last_action", lambda self, person: self.spine._get(done.seq))
    with pytest.raises(Refused, match="already undone"):
        k.actions.undo(SAM, actor=SAM)
    assert k.alarms.at[SAM] == "09:30"
    monkeypatch.setattr(Dispatcher, "_undone", lambda self, seq: False)
    k.actions.undo(SAM, actor=SAM)
    assert k.alarms.at[SAM] == "07:00"                         # the hand-set alarm is gone


# ---- rule 4: what cannot be reversed says so, and nothing is faked -------------------------------
def test_an_irreversible_action_says_so_and_nothing_older_is_undone_in_its_place(k):
    k.alarms.at[SAM] = "07:00"
    move(k, SAM, "05:45")                                      # reversible, but not the last
    sent = send(k, SAM)
    assert not sent.reversible and "undo" not in k.spine._get(sent.seq).payload
    before = len(of_kind(k, Kind.ACTION_RECEIPT))
    with pytest.raises(Refused, match="that action cannot be undone"):
        k.actions.undo(SAM, actor=SAM)
    assert k.alarms.at[SAM] == "05:45" and k.outbox.sent == [(SAM, "someone")]
    assert len(of_kind(k, Kind.ACTION_RECEIPT)) == before      # no receipt pretends otherwise


def test_control_if_that_meant_the_last_reversible_action_an_older_one_is_silently_reversed(k, monkeypatch):
    k.alarms.at[SAM] = "07:00"
    done = move(k, SAM, "05:45")
    send(k, SAM)
    monkeypatch.setattr(Dispatcher, "last_action", lambda self, person: self.spine._get(done.seq))
    k.actions.undo(SAM, actor=SAM)
    assert k.alarms.at[SAM] == "07:00" and k.outbox.sent       # "undo that" hit the wrong thing


def test_nothing_to_undo_and_actions_never_confirmed_are_refused(k):
    with pytest.raises(Refused, match="there is nothing to undo"):
        k.actions.undo(SAM, actor=SAM)
    k.alarms.at[SAM] = "07:00"
    real, k.actions._actions["alarm.move"] = k.actions._actions["alarm.move"], (
        lambda person, args: None, k.alarms.verify, True)      # says ok, does nothing
    assert not move(k, SAM, "05:45").verified
    with pytest.raises(Refused, match="never confirmed done"):
        k.actions.undo(SAM, actor=SAM)
    k.actions._actions["alarm.move"] = real
    k.spine._db.execute("UPDATE intents SET receipt_seq = NULL WHERE id = (SELECT MAX(id) FROM intents)")
    k.actions.recover()                                        # the last action is now "uncertain"
    with pytest.raises(Refused, match="never confirmed done"):
        k.actions.undo(SAM, actor=SAM)
    assert k.alarms.at[SAM] == "07:00"


def test_an_undo_cut_short_by_a_dead_kernel_is_uncertain_and_stays_spent(k):
    """The recovery path for an undo intent (written in the undo PR, first tested here)."""
    k.alarms.at[SAM] = "07:00"
    done = move(k, SAM, "05:45")
    k.actions.undo(SAM, actor=SAM)
    k.spine._db.execute("UPDATE intents SET receipt_seq = NULL WHERE undoes IS NOT NULL")   # died mid-undo
    (recovered,) = k.actions.recover()
    assert recovered.payload["outcome"] == "uncertain" and recovered.payload["undoes"] == done.seq
    assert recovered.payload["action"] == "undo" and not recovered.payload["verified"]
    with pytest.raises(Refused, match="the last action was itself an undo"):
        k.actions.undo(SAM, actor=SAM)                             # not run again
    assert k.actions._undone(done.seq)


# ---- forget still reaches it ---------------------------------------------------------------------
def test_a_forget_erases_the_saved_before_state_and_the_undo_receipt(k):
    k.alarms.at[SAM] = SENTINEL                                # the old state is the person's data
    done = move(k, SAM, "05:45")
    back = k.actions.undo(SAM, actor=SAM)
    on_disk = lambda: b"".join(open(p, "rb").read() for p in glob.glob(k.path + "*"))  # noqa: E731
    assert SENTINEL.encode() in on_disk()
    k.spine.forget(SAM, actor=SAM)
    assert SENTINEL.encode() not in on_disk()
    assert k.spine._get(done.seq) is None and k.spine._get(back.seq) is None


# ---- through the socket ---------------------------------------------------------------------------
def test_undo_that_through_the_socket_and_a_skill_cannot_ask():
    from procs import Box
    box = Box()
    try:
        panel, mover = box.client("panel"), box.client("mover")
        panel.grant(SAM, "mover")
        cal = box.client("calendar").publish("calendar_change", subject=SAM,
                                             payload={"event_id": "e1", "change": "created"})
        offer = mover.publish("offer", subject=SAM, caused_by=[cal.seq],
                              payload={"offer_id": "o1", "text": "move the alarm?"})
        yes = panel.publish("offer_response", subject=SAM, caused_by=[offer.seq],
                            payload={"offer_id": "o1", "response": "accepted"})
        done = mover.act("alarm.move", SAM, {"to": "05:45"}, caused_by=[yes.seq])
        assert done.reversible
        with pytest.raises(Refused, match="trusted surface"):
            mover.undo(SAM, actor=SAM)
        with pytest.raises(Refused, match="not theirs to undo"):
            panel.undo(SAM, actor=ALEX)
        back = panel.undo(SAM, actor=SAM)
        assert back.verified and back.action == "undo"
        assert box.written() == [[SAM, {"to": "05:45"}], [SAM, {"to": None}]]
        with pytest.raises(Refused, match="itself an undo"):
            panel.undo(SAM, actor=SAM)
    finally:
        box.close()
