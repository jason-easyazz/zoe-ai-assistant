"""The durable reminder model (``zoe_os.reminder_rows``) in-process: production's forms, rules and visibility, undo, rebuild, forget.

Socket-free and process-free: the kernel runs in this process on a temp database, and a "restart" is a new kernel on the same file
(the real-process kill tests are in ``test_authority.py``). Synthetic members and notes only.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from authority import AUTH_ACTS, AUTHORITY_BRAIN, ADAPTERS, DurableKeeper, DurableLists
from zoe_os.capability import CHILD, Person
from zoe_os.events import Event, Kind, Visibility
from zoe_os.kernel import Kernel
from zoe_os.reminder_rows import DurableReminders
from zoe_os.spine import Refused

A, B, K = "member-a", "member-b", "member-k"
T0 = datetime(2030, 1, 7, 12, 0, tzinfo=timezone.utc).timestamp()          # a Monday, noon UTC


class House:
    def __init__(self, path, clock, default_time="09:00"):
        self.k = Kernel(str(path), [Person(A), Person(B), Person(K, CHILD, guardians=(A,))], clock=lambda: clock[0])
        self.lists = DurableLists(self.k.spine).register(self.k)
        self.rem = DurableReminders(self.k.spine, lambda: clock[0], self.k.grants, default_time=default_time).register(self.k)
        self.rem.rebuild()
        for m in (*ADAPTERS, AUTHORITY_BRAIN):
            self.k.grants.install(m)
        self.k.install(DurableKeeper(self.lists, durable=(self.rem,)))
        for p in (A, B):
            self.k.grants.grant(p, "brain", acts=list(AUTH_ACTS))
        self.k.grants.grant(K, "brain", acts=list(AUTH_ACTS), guardian=A)

    def said(self, person, mentions=()):
        self.n = getattr(self, "n", 0) + 1                  # a fresh text: forgotten words are shielded for every publisher
        return self.k.spine.publish(Event(Kind.UTTERANCE, "voice", person, Visibility.PRIVATE,
                                          {"text": f"synthetic reminder words {self.n}"}, mentions=tuple(mentions))).seq

    def act(self, person, action, mentions=(), **args):
        seq = self.said(person, mentions)
        return self.k.actions.act("brain", action, person, {**args, "asked": seq}, caused_by=(seq,))

    def forget(self, subject, actor=None, **scope):
        self.k.spine.forget(subject, actor=actor or subject, **scope)
        self.k.pump()

    def row(self, rid):
        return self.rem.rows.get(rid)


@pytest.fixture
def clock():
    return [T0]


@pytest.fixture
def house(tmp_path, clock):
    h = House(tmp_path / "k.db", clock)
    yield h
    h.k.close()


def restart(house, tmp_path, clock, **kw):
    house.k.close()
    return House(tmp_path / "k.db", clock, **kw)


def rid_of(receipt):
    return receipt.proof["id"]


# ---- the forms, backward compatible ---------------------------------------------------------------------------------------------
def test_todays_arguments_still_set_a_one_off_and_the_proof_still_carries_the_note(house):
    r = house.act(A, "remind.set", note="stretch", in_minutes=30)
    assert r.verified and r.proof["note"] == "stretch" and r.reversible
    assert house.rem.next_fire(house.row(rid_of(r))) == T0 + 1800


def test_every_production_form_lands_with_its_fire_time(house):
    day = house.act(A, "remind.set", note="bins", date="2031-04-02")                      # a year out: no 7-day limit
    clock_only = house.act(A, "remind.set", note="water", time="18:30")
    rep = house.act(A, "remind.set", note="gym", repeat="FREQ=WEEKLY;INTERVAL=2;BYDAY=TU", time="07:00")
    assert house.rem.next_fire(house.row(rid_of(day))) == datetime(2031, 4, 2, 9, tzinfo=timezone.utc).timestamp()
    assert house.rem.next_fire(house.row(rid_of(clock_only))) == datetime(2030, 1, 7, 18, 30, tzinfo=timezone.utc).timestamp()
    stored = house.row(rid_of(rep))
    assert stored["date"] == "2030-01-08" and stored["repeat"] == "FREQ=WEEKLY;INTERVAL=2;BYDAY=TU"       # anchored at the coming Tuesday


def test_the_default_time_is_a_setting(tmp_path, clock):
    h = House(tmp_path / "d.db", clock, default_time="07:15")
    r = h.act(A, "remind.set", note="bins", date="2030-01-08")
    assert h.rem.next_fire(h.row(rid_of(r))) == datetime(2030, 1, 8, 7, 15, tzinfo=timezone.utc).timestamp()
    h.k.close()


def test_today_after_the_default_time_fires_shortly_like_production(house):
    r = house.act(A, "remind.set", note="call", date="2030-01-07")                          # noon, default 09:00 passed
    assert house.rem.next_fire(house.row(rid_of(r))) == T0 + 120


@pytest.mark.parametrize("args,why", [
    ({"date": "2030-01-06"}, "that day has already passed"),
    ({"date": "2030-01-07", "time": "11:00"}, "that time has already passed"),
    ({"repeat": "FREQ=HOURLY"}, "not a repeat I can use"),
    ({"repeat": "FREQ=DAILY", "until": "2030-01-01"}, "that repeat has no upcoming time"),
    ({"date": "2030-02-01", "until": "2030-03-01"}, "an end needs a repeat"),
    ({"in_minutes": 5, "date": "2030-02-01"}, "one schedule at a time"),
    ({}, "a reminder needs a time or a day"),
])
def test_what_production_refuses_is_refused_before_anything_is_written(house, args, why):
    with pytest.raises(Refused, match=why):
        house.act(A, "remind.set", note="x", **args)
    assert house.rem.rows == {} and house.k.spine._db.execute("SELECT COUNT(*) FROM intents").fetchone()[0] == 0


# ---- the other verbs, each read back and each undone ----------------------------------------------------------------------------
def test_snooze_done_cancel_edit_each_verified_and_each_really_undone(house, clock):
    rid = rid_of(house.act(A, "remind.set", note="tea", time="15:00"))
    for action, args in (("remind.snooze", {"minutes": 10}), ("remind.done", {}), ("remind.cancel", {}),
                         ("remind.edit", {"note": "green tea", "time": "16:00"})):
        before = dict(house.row(rid))
        r = house.act(A, action, id=rid, **args)
        assert r.verified and r.reversible and house.row(rid) != before
        assert house.k.actions.undo(A, actor=A).verified and house.row(rid) == before
    house.act(A, "remind.snooze", id=rid, minutes=10)
    assert house.rem.next_fire(house.row(rid)) == T0 + 600


def test_done_on_a_repeat_is_this_occurrence_only(house, clock):
    rid = rid_of(house.act(A, "remind.set", note="pills", repeat="FREQ=DAILY", time="12:30"))
    clock[0] = T0 + 1800                                                                    # 12:30 has come
    house.act(A, "remind.done", id=rid)
    assert house.row(rid)["done"] == 0
    assert house.rem.next_fire(house.row(rid)) == T0 + 86400 + 1800


def test_editing_a_repeat_to_a_new_day_re_anchors_and_resending_the_anchor_keeps_it(house):
    rid = rid_of(house.act(A, "remind.set", note="gym", repeat="FREQ=WEEKLY;BYDAY=TU", time="07:00"))
    house.act(A, "remind.edit", id=rid, date="2030-01-08", time="08:00")
    assert house.row(rid)["date"] == "2030-01-08" and house.row(rid)["time"] == "08:00"
    house.act(A, "remind.edit", id=rid, date="2030-01-20")
    assert house.row(rid)["date"] == "2030-01-22"                                           # the Tuesday after the 20th
    with pytest.raises(Refused, match="already passed"):
        house.act(A, "remind.edit", id=rid, date="2030-01-01")


# ---- who may: production's rules ------------------------------------------------------------------------------------------------
def test_visibility_and_who_may_change_what_mirror_production(house):
    mine = rid_of(house.act(A, "remind.set", note="private thing", time="20:00"))
    ours = rid_of(house.act(A, "remind.set", note="bins out", time="19:00", visibility="family"))
    assert [r["id"] for r in house.rem.visible_to(B)] == [ours]
    with pytest.raises(Refused, match="no such reminder"):
        house.act(B, "remind.cancel", id=mine)
    with pytest.raises(Refused, match="only its owner"):
        house.act(B, "remind.snooze", id=ours)                                                # owner only, as production
    house.act(B, "remind.edit", id=ours, note="bins and recycling", visibility="personal")
    assert house.row(ours)["note"] == "bins and recycling" and house.row(ours)["visibility"] == "family"   # visibility: owner only
    assert house.act(B, "remind.cancel", id=ours).verified and house.row(ours) is None


def test_a_guardian_may_set_one_for_their_child_when_the_ask_names_the_child(house):
    r = house.act(A, "remind.set", note="pack the bag", time="07:45", mentions=(K,), **{"for": K})
    row = house.row(rid_of(r))
    assert row["owner"] == K and row["by"] == A and [x["id"] for x in house.rem.visible_to(K)] == [row["id"]]
    with pytest.raises(Refused, match="must name who it is for"):
        house.act(A, "remind.set", note="x", time="07:45", **{"for": K})
    with pytest.raises(Refused, match="only a carer"):
        house.act(B, "remind.set", note="x", time="07:45", mentions=(K,), **{"for": K})
    with pytest.raises(Refused, match="only a carer"):
        house.act(A, "remind.set", note="x", time="07:45", mentions=(B,), **{"for": B})


# ---- durability: rows are the receipts ------------------------------------------------------------------------------------------
def test_a_restart_rebuilds_the_same_rows_and_an_undo_after_it_holds(house, tmp_path, clock):
    a = rid_of(house.act(A, "remind.set", note="one", time="20:00"))
    b = rid_of(house.act(A, "remind.set", note="two", repeat="FREQ=MONTHLY;BYMONTHDAY=31"))
    house.act(A, "remind.snooze", id=a, minutes=5)
    house.act(A, "remind.cancel", id=b)
    rows = {k: dict(v) for k, v in house.rem.rows.items()}
    again = restart(house, tmp_path, clock)
    assert again.rem.rows == rows
    assert again.k.actions.undo(A, actor=A).verified and again.row(b)["note"] == "two"
    third = restart(again, tmp_path, clock)
    assert third.row(b)["note"] == "two" and third.row(a)["snoozed_until"] == T0 + 300
    third.k.close()


def test_a_scoped_forget_removes_the_row_and_it_stays_gone_after_a_restart(house, tmp_path, clock):
    rid = rid_of(house.act(A, "remind.set", note="gift for Dana", time="20:00"))
    house.act(A, "remind.edit", id=rid, note="gift for Dana and Lee")
    keep = rid_of(house.act(A, "remind.set", note="eggs", time="20:00"))
    house.forget(A, entity="Dana")
    assert set(house.rem.rows) == {keep}
    assert set(restart(house, tmp_path, clock).rem.rows) == {keep}


def test_a_childs_forget_reaches_the_reminder_their_guardian_set_for_them(house, tmp_path, clock):
    house.act(A, "remind.set", note="pack the bag", time="07:45", mentions=(K,), **{"for": K})
    own = rid_of(house.act(A, "remind.set", note="mine", time="07:45"))
    house.forget(K, actor=A)                                                                 # a guardian erases the child
    assert set(house.rem.rows) == {own}
    assert set(restart(house, tmp_path, clock).rem.rows) == {own}


def test_control_a_rebuild_that_ignores_the_create_anchor_resurrects_a_forgotten_row(house, monkeypatch):
    """Without "a row exists only while its create is live", a later edit's receipt brings back what a forget took. The spine's
    forward link (a forgotten reminder takes its later records, ``Spine._row_source``) is a second layer that would hide it, so it
    is switched off here too: each layer on its own keeps the row gone (see the next test)."""
    from zoe_os import spine as spine_mod
    monkeypatch.setattr(spine_mod, "_row_source", lambda payload: None)
    rid = rid_of(house.act(A, "remind.set", note="gift for Dana", time="20:00"))
    house.act(A, "remind.edit", id=rid, note="a present")                  # the edit's proof no longer names Dana
    house.act(A, "remind.snooze", id=rid, minutes=5)
    house.forget(A, entity="Dana")                                       # takes the create and the edit (its snapshot names Dana)
    assert house.rem.rows == {}
    monkeypatch.setattr(DurableReminders, "anchored", lambda self, rid, made: True)     # the anchor rule removed
    house.rem.rebuild()
    assert rid in house.rem.rows                                         # the snooze receipt alone resurrects it: the test sees it


def test_with_the_anchor_rule_removed_the_forward_link_alone_still_keeps_a_forgotten_row_gone(house, monkeypatch):
    rid = rid_of(house.act(A, "remind.set", note="gift for Dana", time="20:00"))
    house.act(A, "remind.edit", id=rid, note="a present")
    house.act(A, "remind.snooze", id=rid, minutes=5)
    house.forget(A, entity="Dana")                                       # the create goes, and with it every later record of the row
    monkeypatch.setattr(DurableReminders, "anchored", lambda self, rid, made: True)
    house.rem.rebuild()
    assert rid not in house.rem.rows


def _held(house, word: str) -> bool:
    return any(word in (raw or "") for (raw,) in house.k.spine._db.execute(
        "SELECT payload FROM events WHERE tombstoned = 0"))


def test_forgetting_an_edit_takes_every_later_record_that_copied_its_words(house, tmp_path, clock):
    rid = rid_of(house.act(A, "remind.set", note="synthetic before", time="20:00"))
    edit = house.act(A, "remind.edit", id=rid, note="synthetic marker <edited>")
    house.act(A, "remind.snooze", id=rid, minutes=5)                  # its proof copies the edited row
    house.forget(A, seqs=[edit.seq])
    assert not _held(house, "synthetic marker <edited>")
    assert house.row(rid)["note"] == "synthetic before"               # the row is back to what it was before the edit
    assert restart(house, tmp_path, clock).row(rid)["note"] == "synthetic before"


def test_a_private_mark_shuts_out_the_carer_who_set_it(house):
    rid = rid_of(house.act(A, "remind.set", note="pack the bag", time="07:45", mentions=(K,), **{"for": K}))
    house.act(K, "remind.edit", id=rid, note="synthetic marker <kid-own>", visibility="private")
    assert [r["id"] for r in house.rem.visible_to(A)] == []           # neither the carer's view ...
    with pytest.raises(Refused, match="no such reminder"):
        house.act(A, "remind.cancel", id=rid)                          # ... nor their reach
    assert house.row(rid)["visibility"] == "private" and [r["id"] for r in house.rem.visible_to(K)] == [rid]


def test_a_carers_undo_cannot_overwrite_what_the_person_changed_since(house):
    rid = rid_of(house.act(A, "remind.set", note="pack the bag", time="07:45", mentions=(K,), **{"for": K}))
    house.act(A, "remind.edit", id=rid, note="pack the bag and lunch")
    house.act(K, "remind.edit", id=rid, note="synthetic marker <kid-later>", visibility="private")
    with pytest.raises(Refused, match="it has changed since"):
        house.k.actions.undo(A, actor=A)                               # the carer's last action is the edit the child has overwritten
    assert house.row(rid)["visibility"] == "private" and house.row(rid)["note"] == "synthetic marker <kid-later>"
