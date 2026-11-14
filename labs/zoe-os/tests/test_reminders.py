"""Reminders as a kernel skill against a fake, rule-free store: create, fire, snooze, forget.

The second half is the point of the exercise: rules production implements inside the
reminders feature are enforced here by the KERNEL, shown by a careless or hostile variant of
the skill being stopped while the store itself checks nothing.
"""
from __future__ import annotations

import glob

import pytest
from reminder_store import ReminderStore
from world import ALEX, KID, NOW, SAM, emit, household, of_kind, refusal_reasons

from zoe_os.attention import CARD, DIGEST, SPOKEN
from zoe_os.events import Kind, Visibility
from zoe_os.replay import record, replay
from zoe_os.skills.reminders import Reminders
from zoe_os.spine import Refused

SENTINEL = "zx-sentinel-moss-lantern-2291"
HOUR = 3600


@pytest.fixture
def k(tmp_path):
    kernel = household(tmp_path)
    kernel.now = [NOW]
    kernel.clock = lambda: kernel.now[0]
    kernel.grants.install({"skill": "clock", "emits": ["timer_fired"], "origin": True})
    kernel.grants.install({"skill": "cards", "reads": {"offer": "private"}})
    kernel.skill, kernel.store = Reminders(), ReminderStore().register(kernel)
    kernel.install(kernel.skill)
    for person in (ALEX, SAM):
        kernel.grants.grant(person, "reminders")
    kernel.grants.grant(SAM, "cards")
    yield kernel
    kernel.close()


def ask(k, person, note, at):
    ev = emit(k, Kind.UTTERANCE, "voice", person, text=f"remind me: {note}",
              intent={"name": "reminder.create", "at": at, "note": note})
    k.pump()
    return f"r{ev.seq}"


def tick(k, at=None):
    k.now[0] = at or k.now[0]
    emit(k, Kind.TIMER_FIRED, "clock", None, Visibility.HOUSEHOLD, tick=True)
    k.pump()
    return of_kind(k, Kind.OFFER)


def respond(k, offer, **choice):
    emit(k, Kind.OFFER_RESPONSE, "panel", offer.subject, cause=(offer.seq,),
         offer_id=offer.payload["offer_id"], response="accepted", **choice)
    k.pump()


# ---- the job ---------------------------------------------------------------------------------------
def test_create_lands_in_the_store_with_a_verified_receipt_and_a_backed_claim(k):
    rid = ask(k, SAM, "take the bins out", NOW + HOUR)
    assert k.store.rows[rid] == {"owner": SAM, "note": "take the bins out", "due": NOW + HOUR,
                                 "acknowledged": False}
    (receipt,) = of_kind(k, Kind.ACTION_RECEIPT)
    assert receipt.payload["verified"] and receipt.payload["reversible"] and receipt.subject == SAM
    assert len(of_kind(k, Kind.CLAIM)) == 1 and k.auditor.scan() == [] and refusal_reasons(k) == []


def test_it_fires_once_when_due_as_an_offer_to_its_owner_only(k):
    ask(k, ALEX, "call the dentist", NOW + HOUR)
    assert tick(k, NOW + HOUR - 1) == []                         # not due yet
    (offer,) = tick(k, NOW + HOUR)
    assert offer.subject == ALEX and offer.payload["text"] == "Reminder: call the dentist"
    assert len(tick(k)) == 1 and len(tick(k, NOW + 5 * HOUR)) == 1          # once, not every tick
    assert [e for e in k.spine.replay("cards") if e.kind is Kind.OFFER] == []   # SAM's screen: nothing


def test_snooze_moves_it_and_it_fires_again_once_and_done_closes_it(k):
    rid = ask(k, SAM, "water the plants", NOW)
    (first,) = tick(k)
    respond(k, first, choice="snooze", minutes=10)
    assert k.store.rows[rid]["due"] == NOW + 600
    assert len(tick(k, NOW + 599)) == 1
    first_again, second = tick(k, NOW + 600)
    assert second.payload["gen"] == 1 and second.payload["reminder_id"] == rid
    respond(k, first_again)                                      # an answer to the OLD fire: ignored
    assert not k.store.rows[rid]["acknowledged"]
    respond(k, second)                                           # "done"
    assert k.store.rows[rid]["acknowledged"] and len(tick(k, NOW + 9 * HOUR)) == 2
    assert refusal_reasons(k) == []


def test_a_restarted_skill_relearns_from_the_log_and_neither_recreates_nor_refires(k):
    rid = ask(k, SAM, "water the plants", NOW)
    (first,) = tick(k)
    respond(k, first, choice="snooze", minutes=10)
    again = Reminders()
    again.restore(k.spine.replay("reminders"))
    assert again._items[rid] == k.skill._items[rid] and again._offered == k.skill._offered


# ---- rules the skill does not implement, because the kernel does ---------------------------------
def test_when_and_how_it_reaches_the_person_is_the_kernels_decision_not_the_skills(k):
    ask(k, SAM, "water the plants", NOW)
    (offer,) = tick(k)
    assert k.attention.route(offer, hour=12, day=1) == CARD      # unprompted speech is off by default
    assert k.attention.route(offer, hour=23, day=1) == DIGEST    # quiet hours
    k.attention.set(SAM, by=SAM, spoken_unprompted=True)
    assert k.attention.route(offer, hour=12, day=1) == SPOKEN    # only because SAM turned it on


def test_someone_who_never_enabled_it_gets_no_reminder_and_the_skill_never_sees_their_words(k):
    k.grants.revoke(ALEX, "reminders")
    for person in (ALEX, KID):                                   # KID: no guardian ever said yes
        ask(k, person, f"private note {SENTINEL}", NOW)
    assert k.store.rows == {} and tick(k) == [] and k.skill._items == {}


class Careless(Reminders):
    """A bug, not malice: on any answer it snoozes EVERY open reminder, whoever owns it."""

    def _answered(self, ev, ctx) -> None:
        for rid, item in sorted(self._items.items()):
            ctx.act("reminder.snooze", item["person"], {"id": rid, "until": ctx.now() + 600},
                    caused_by=(ev.seq,))


def careless_scene(k):
    k.skill.__class__ = Careless
    mine, theirs = ask(k, SAM, "sam's reminder", NOW), ask(k, ALEX, "alex's reminder", NOW)
    sams_offer = next(o for o in tick(k) if o.subject == SAM)
    return mine, theirs, sams_offer


def test_one_persons_yes_cannot_move_another_persons_reminder(k):
    mine, theirs, offer = careless_scene(k)
    respond(k, offer)                                            # SAM says yes to SAM's reminder
    assert k.store.rows[mine]["due"] == NOW + 600 and k.store.rows[theirs]["due"] == NOW
    assert refusal_reasons(k) == ["the person has not accepted an offer for this"]


def test_control_without_the_kernels_yes_rule_the_dumb_store_moves_it(k):
    k.actions.register("reminder.snooze", k.store.snooze, k.store.snoozed, needs_accept=False)
    mine, theirs, offer = careless_scene(k)
    respond(k, offer)
    assert k.store.rows[theirs]["due"] == NOW + 600              # ALEX's reminder, on SAM's word


def test_undo_that_removes_a_reminder_just_created(k):
    rid = ask(k, SAM, "take the bins out", NOW + HOUR)
    assert k.actions.undo(SAM, actor=SAM).verified and rid not in k.store.rows
    with pytest.raises(Refused, match="not theirs to undo"):
        k.actions.undo(SAM, actor=ALEX)


def test_why_did_you_remind_me(k):
    ask(k, SAM, "water the plants", NOW)
    (offer,) = tick(k)
    why = k.explainer.why(offer.seq, person=SAM)
    assert [n["kind"] for n in why["chain"]] == ["utterance", "action_receipt", "timer_fired"]
    assert why["sentence"].startswith("reminders made this offer to sam. It has not been answered.")


# ---- forget ---------------------------------------------------------------------------------------
def on_disk(k) -> bytes:
    return b"".join(open(p, "rb").read() for p in glob.glob(k.path + "*"))


def test_forget_erases_the_reminder_from_the_log_the_skill_and_the_store(k):
    mine = ask(k, SAM, f"ring {SENTINEL}", NOW)
    theirs = ask(k, ALEX, "alex's reminder", NOW)
    tick(k)
    assert SENTINEL.encode() in on_disk(k) and SENTINEL in str(k.store.rows)
    k.spine.forget(SAM, actor=SAM)
    k.pump()
    assert SENTINEL.encode() not in on_disk(k)
    assert mine not in k.store.rows and SENTINEL not in str(k.skill._items)
    assert theirs in k.store.rows and theirs in k.skill._items and refusal_reasons(k) == []
    assert len(tick(k, NOW + HOUR)) == 1                         # ALEX's still fires; SAM's is gone


def test_a_topic_forget_takes_only_the_matching_reminder(k):
    gone = ask(k, SAM, f"dentist appointment {SENTINEL}", NOW + HOUR)
    kept = ask(k, SAM, "water the plants", NOW + HOUR)
    k.spine.forget(SAM, actor=SAM, topic="dentist appointment")
    k.pump()
    assert gone not in k.store.rows and kept in k.store.rows and SENTINEL.encode() not in on_disk(k)


def test_HONEST_LIMIT_a_skill_that_ignores_the_notice_leaves_the_words_in_its_store(k, monkeypatch):
    """The control for the test above, and a real limit: the store is outside the kernel."""
    monkeypatch.setattr(Reminders, "_forget", lambda self, notice, ctx: None)
    ask(k, SAM, f"ring {SENTINEL}", NOW)
    k.spine.forget(SAM, actor=SAM)
    k.pump()
    assert SENTINEL.encode() not in on_disk(k)                   # the kernel's own copy is gone
    assert SENTINEL in str(k.store.rows)                         # the skill's store still has it


# ---- and it replays -------------------------------------------------------------------------------
def test_a_day_with_reminders_replays_through_the_harness(k):
    ask(k, SAM, "water the plants", NOW)
    tick(k, NOW + 60)
    day = record(k, answers={SAM: {"reminders": "accepted"}},
                 no_accept=["reminder.create", "reminder.delete"])      # asked for first-hand
    report = replay(day, [Reminders()])
    assert report.counts == {"offers": 1, "actions": 2, "refusals": 0, "claims": 1, "findings": 0}
    assert '[card, accepted] "Reminder: water the plants"' in report.text
