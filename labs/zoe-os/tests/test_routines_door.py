"""The routines area of the door (``zoe_os/routines_door.py``): one test per row of the table, both switches both ways, the
record (ids only, under the ``routine`` key), and the same verbs over the daemon's wire. Each test names the control that was
run against it. Invented people and ids only.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from zoe_os import door
from zoe_os import routines_door as R
from zoe_os.capability import ADULT, CHILD, GUEST, Person
from zoe_os.kernel import Kernel
from zoe_os.spine import Refused

OWNER, ADULT2, KID, KID2, VISITOR, STRANGER = "member-ova", "member-adu", "member-kip", "member-kop", "member-gus", "member-zzz"
PEOPLE = {p.id: p for p in (Person(OWNER, ADULT), Person(ADULT2, ADULT), Person(KID, CHILD, guardians=(OWNER,)),
                            Person(KID2, CHILD, guardians=(OWNER,)), Person(VISITOR, GUEST))}
ALLOW = R.Decision(True)
ASKED = (R.CREATE, R.EDIT, R.DELETE, R.EDIT_GIVEN, R.DELETE_GIVEN)
OWN = (R.CREATE, R.EDIT, R.DELETE)
SHIPPED = (R.CHILD_MAY_CHANGE_GIVEN, R.HOUSEHOLD_CHANGE_STRICT)          # read before any test touches them
ROUTINE = "0f0e0d0c-0b0a-4908-8706-050403020100"


def decided(verb, actor, **kw):
    return R.decide(PEOPLE, verb, actor, **kw)


def refused(reason):
    return (False, reason, None)


@pytest.fixture(autouse=True)
def as_shipped(monkeypatch):
    monkeypatch.setattr(R, "CHILD_MAY_CHANGE_GIVEN", False)
    monkeypatch.setattr(R, "HOUSEHOLD_CHANGE_STRICT", False)


@pytest.mark.parametrize("verb", OWN + R.TOLD_VERBS)
@pytest.mark.parametrize("who", [OWNER, KID, VISITOR])
@pytest.mark.parametrize("visibility", ["private", "household"])
def test_a_member_does_everything_to_their_own_routine(verb, who, visibility):
    """Control run: with the own-routine rule dropped a child's own private routine is ``not_owner``."""
    assert decided(verb, who, owner=who, visibility=visibility) == ALLOW


def test_a_child_ticks_a_routine_set_up_for_them_and_does_not_change_it_and_the_switch_restores_the_looser_rule(monkeypatch):
    """The owner's acceptance words (routines.md line 10), ON as shipped. Control run: with ``CHILD_MAY_CHANGE_GIVEN`` True
    by default, or ``HOUSEHOLD_CHANGE_STRICT`` True by default, the shipped-values assertion fails; with the switch ignored
    in ``decide`` the second half fails."""
    assert SHIPPED == (False, False)
    for verb in R.GIVEN:
        assert decided(verb, KID, owner=KID) == refused(door.ADULTS_ONLY)
        assert decided(verb, OWNER, owner=OWNER) == ALLOW and decided(verb, VISITOR, owner=VISITOR) == ALLOW      # not a child
    for verb in R.TOLD_VERBS:
        assert decided(verb, KID, owner=KID) == ALLOW
    monkeypatch.setattr(R, "CHILD_MAY_CHANGE_GIVEN", True)
    for verb in R.GIVEN:
        assert decided(verb, KID, owner=KID) == ALLOW


@pytest.mark.parametrize("verb", ASKED)
def test_an_adult_sets_up_and_changes_a_childs_routine_and_never_ticks_it(verb):
    """Control run: with the adult-for-a-child rule dropped this is ``not_owner``; with it covering the told verbs an adult
    ticks a child's step."""
    assert decided(verb, ADULT2, owner=KID) == ALLOW
    for told in R.TOLD_VERBS:
        assert decided(told, ADULT2, owner=KID) == refused(door.NOT_OWNER)


@pytest.mark.parametrize("verb", R.VERBS)
@pytest.mark.parametrize("who, owner", [(ADULT2, OWNER), (KID2, KID), (KID, OWNER), (VISITOR, KID), (KID2, None)])
def test_nobody_else_touches_a_private_routine(verb, who, owner):
    """An adult's from another adult, a child's from another child or a guest. Control run: with the last rule answering
    allow a child changes an adult's private routine."""
    assert decided(verb, who, owner=owner) == refused(door.NOT_OWNER)


@pytest.mark.parametrize("verb", R.VERBS)
@pytest.mark.parametrize("who", [ADULT2, KID2, VISITOR])
def test_anyone_in_the_house_changes_and_ticks_a_household_routine(verb, who):
    """Today's rule, kept (that it is the SHIPPED value is asserted with the other switch above). Control run: with the
    household rule answering ``not_owner`` this fails."""
    assert decided(verb, who, owner=OWNER, visibility="household") == ALLOW


def test_the_strict_switch_leaves_a_household_routine_to_its_subject_or_an_adult(monkeypatch):
    """Control run: with the switch ignored in ``decide`` a child still edits the house's routine."""
    monkeypatch.setattr(R, "HOUSEHOLD_CHANGE_STRICT", True)
    for verb in (R.EDIT, R.DELETE, R.EDIT_GIVEN, R.DELETE_GIVEN):
        assert decided(verb, KID2, owner=OWNER, visibility="household") == refused(door.ADULTS_ONLY)
        assert decided(verb, VISITOR, owner=OWNER, visibility="household") == refused(door.ADULTS_ONLY)
        assert decided(verb, ADULT2, owner=OWNER, visibility="household") == ALLOW
    assert decided(R.EDIT, KID, owner=KID, visibility="household") == ALLOW                 # its subject
    assert decided(R.CREATE, KID2, owner=OWNER, visibility="household") == ALLOW            # anyone still makes one
    for told in R.TOLD_VERBS:
        assert decided(told, KID2, owner=OWNER, visibility="household") == ALLOW            # and ticks it


def test_the_panel_ticks_a_household_routine_and_nothing_else():
    """The fridge rule. Control run: with the panel rule answering allow the bare panel edits a routine."""
    for told in R.TOLD_VERBS:
        assert decided(told, "kiosk-1", via="panel", owner=OWNER, visibility="household") == ALLOW
        assert decided(told, "kiosk-1", via="panel", owner=OWNER) == refused(door.NOT_OWNER)
    for verb in ASKED:
        for vis in ("private", "household"):
            assert decided(verb, "kiosk-1", via="panel", owner=OWNER, visibility=vis) == refused(door.NOT_OWNER)


@pytest.mark.parametrize("verb", R.VERBS)
def test_someone_not_on_the_roster_is_refused_everything(verb):
    """Control run: with the roster check dropped a stranger ticks the house's routine."""
    assert decided(verb, STRANGER, owner=STRANGER) == refused(door.UNKNOWN_PERSON)
    assert decided(verb, STRANGER, owner=OWNER, visibility="household") == refused(door.UNKNOWN_PERSON)
    assert len(R.VERBS) == 9 and R.TOLD_VERBS == ("routine.tick", "routine.skip", "routine.snooze", "routine.undo")


def test_the_record_holds_ids_only_and_the_daemon_serves_the_area_with_no_field_for_words(tmp_path):
    """Control run: with the area keyed ``target="memory"`` the payload assertion fails; with the routine verbs left out of
    the daemon's manifest every ask is refused."""
    k = Kernel(str(tmp_path / "k.db"), list(PEOPLE.values()))
    k.grants.install({"skill": "door", "acts": list(R.VERBS)})
    a = k.door.ask("door", "routines", R.EDIT, OWNER, target=ROUTINE, owner=KID)
    assert k.spine._get(a["seq"]).payload == {"action": R.EDIT, "skill": "door", "grant_id": None, "proof": None, "verified": False,
                                              "outcome": "allowed", "reversible": False, "vis": "private", "routine": ROUTINE, "owner": KID}
    with pytest.raises(Refused) as exc:
        k.door.ask("door", "routines", R.EDIT_GIVEN, KID, target=ROUTINE, owner=KID)
    assert exc.value.wire == door.ADULTS_ONLY
    with pytest.raises(ValueError):
        k.door.ask("door", "memory", R.EDIT, OWNER, owner=OWNER)
    k.close()
    from test_shadow_daemon import Shadow, settled
    from zoe_os import wire

    state = Path(tempfile.mkdtemp(prefix="zd"))
    state.chmod(0o700)
    settled(state)
    (state / "people.json").write_text(json.dumps({"people": [{"id": OWNER, "role": "adult"}, {"id": KID, "role": "child",
                                                                                                "guardians": [OWNER]}]}))
    d = Shadow(state)
    try:
        def call(verb, person, **kw):
            return d.brain.call("door_ask", area="routines", verb=verb, person=person, **kw)

        assert call(R.CREATE, OWNER, owner=KID)["ask"] and call("routine.tick", KID, target=ROUTINE, owner=KID)["ask"]
        assert call("routine.tick", "kiosk-1", via="panel", target=ROUTINE, owner=OWNER, visibility="household")["ask"]
        for verb, person, kw, reason in ((R.DELETE_GIVEN, KID, {"owner": KID}, door.ADULTS_ONLY),
                                         ("routine.tick", OWNER, {"owner": KID}, door.NOT_OWNER),
                                         (R.CREATE, STRANGER, {"owner": STRANGER}, door.UNKNOWN_PERSON)):
            with pytest.raises(Refused) as exc:
                call(verb, person, **kw)
            assert str(exc.value) == reason
        for words in ({"name": "words"}, {"text": "words"}, {"step": "words"}):
            with pytest.raises(wire.WireError):
                call(R.EDIT, OWNER, owner=OWNER, **words)
    finally:
        d.stop()
