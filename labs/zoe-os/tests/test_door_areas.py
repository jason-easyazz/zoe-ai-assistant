"""The door is ONE mechanism with an area each (``zoe_os/door.py``): lists are its first area, and a second area adds a permission
table, never a second door.

* ``door_ask`` / ``door_report`` are the area-neutral wire ops; ``list_ask`` / ``list_report`` stay served as the lists area's
  older spelling, and the two spellings leave the SAME record;
* an area is closed: an unknown area, or a verb of another area, is a malformed request (nothing is decided or written);
* a verb belongs to exactly one area (a report finds its ask's area by the verb);
* a second area is decided by ITS table and recorded under ITS key, with the shared rules intact (unknown owner refused, a
  stale erase stamp writes nothing, one transaction for the receipt and its open ask).

Invented people, ids and one toy area only.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from zoe_os import door, lists, wire
from zoe_os.capability import ADULT, CHILD, Person
from zoe_os.kernel import Kernel
from zoe_os.spine import Decision, Refused

OWNER, KID, STRANGER = "member-ova", "member-kip", "member-zzz"
PEOPLE = (Person(OWNER, ADULT), Person(KID, CHILD, guardians=(OWNER,)))


def _toy_decide(people, verb, actor, *, via=None, owner=None, visibility=door.PRIVATE):
    """A toy table: only the owner, and nobody may ``toy.break``."""
    if via != door.PANEL and actor not in people:
        return Decision(False, door.UNKNOWN_PERSON)
    if verb == "toy.break":
        return Decision(False, door.ADULTS_ONLY)
    return Decision(True) if owner == actor else Decision(False, door.NOT_OWNER)


TOY = door.Area("toys", ("toy.make", "toy.break"), _toy_decide, target="toy")


def kernel(tmp_path, areas=(lists.AREA, TOY)):
    k = Kernel(str(tmp_path / "k.db"), list(PEOPLE))
    k.door = door.Door(k.spine, k.enforcer, areas)
    k.grants.install({"skill": "door", "acts": [*lists.VERBS, *TOY.verbs]})
    return k


def test_the_neutral_op_and_the_lists_spelling_leave_the_same_record(tmp_path):
    """Control run: with ``turn=turn`` dropped from ``ListDoor.ask`` the two payloads differ."""
    k = kernel(tmp_path)
    old = k.lists.ask("door", "item.add", KID, list_id="list-qov", items=["item-1"], owner=OWNER, visibility="household", turn="turn-7")
    new = k.door.ask("door", "lists", "item.add", KID, target="list-qov", parts=["item-1"], owner=OWNER, visibility="household",
                     turn="turn-7")
    assert k.spine._get(old["seq"]).payload == k.spine._get(new["seq"]).payload
    assert k.door.report("door", old["ask"], "done")["outcome"] == "done"          # either spelling closes either ask
    assert k.lists.report("door", new["ask"], "failed")["outcome"] == "failed"
    k.close()


def test_an_area_is_closed_and_a_verb_belongs_to_one_area(tmp_path):
    """Control run: with the ``verb not in spec.verbs`` check dropped from ``Door.ask`` the cross-area ask is decided by the
    wrong table and recorded (no ValueError)."""
    k = kernel(tmp_path)
    before = k.spine.refusals
    for area, verb in (("nowhere", "item.add"), ("toys", "item.add"), ("lists", "toy.make"), (None, "item.add"), (7, "item.add")):
        with pytest.raises(ValueError):
            k.door.ask("door", area, verb, OWNER, target="thing-1", owner=OWNER, visibility="private")
    with pytest.raises(ValueError):
        k.door.ask("door", "toys", "toy.make", OWNER, target="toy-1", parts=["part-1"], owner=OWNER)    # this area has no parts
    assert k.spine.refusals == before                                # malformed: not a decision, nothing recorded
    with pytest.raises(ValueError):
        door.Door(k.spine, k.enforcer, (lists.AREA, door.Area("again", ("item.add",), _toy_decide, target="x")))
    k.close()


def test_a_second_area_is_decided_by_its_own_table_and_recorded_under_its_own_key(tmp_path):
    """Control run: with ``spec.decide`` replaced by the lists area's table in ``Door.ask`` the owner's ``toy.break`` is allowed."""
    k = kernel(tmp_path)
    out = k.door.ask("door", "toys", "toy.make", OWNER, target="toy_bar_0001", owner=OWNER, visibility="household")
    made = k.spine._get(out["seq"])
    assert made.payload["toy"] == "toy_bar_0001" and "list" not in made.payload and made.subject == OWNER
    for person, verb, owner, reason in ((KID, "toy.make", OWNER, door.NOT_OWNER), (OWNER, "toy.break", OWNER, door.ADULTS_ONLY),
                                        (STRANGER, "toy.make", STRANGER, door.UNKNOWN_PERSON),
                                        (OWNER, "toy.make", STRANGER, door.NOT_OWNER)):
        with pytest.raises(Refused) as exc:
            k.door.ask("door", "toys", verb, person, target="toy_bar_0001", owner=owner, visibility="household")
        assert exc.value.wire == reason
    with pytest.raises(Refused) as exc:                              # the shared rule: an owner the kernel does not know is never written
        k.door.ask("door", "lists", "item.add", OWNER, target="list-qov", owner=STRANGER, visibility="household")
    assert exc.value.wire == door.UNKNOWN_PERSON
    assert k.door.report("door", out["ask"], "done")["outcome"] == "done"
    k.close()


def test_the_daemon_serves_the_neutral_ops_beside_the_lists_spelling(tmp_path):
    """Over the real socket: ``door_ask`` with ``area="lists"`` decides by the lists table, and its report closes the ask.
    Control run: with ``sync_roster()`` removed from ``ShadowServer._op_door_ask`` a member added to people.json is still
    ``unknown_person`` on this op (covered for the lists spelling in ``test_roster_sync.py``)."""
    from test_shadow_daemon import Shadow, settled

    state = Path(tempfile.mkdtemp(prefix="zd"))
    state.chmod(0o700)
    settled(state)
    (state / "people.json").write_text(json.dumps({"people": [{"id": OWNER, "role": "adult"}, {"id": KID, "role": "child",
                                                                                                "guardians": [OWNER]}]}))
    d = Shadow(state)
    try:
        out = d.brain.call("door_ask", area="lists", verb="item.tick", person=KID, target="list-qov", parts=["item-3"], owner=OWNER,
                           visibility="household")
        assert d.brain.call("door_report", ask=out["ask"], outcome="done")["outcome"] == "done"
        with pytest.raises(Refused, match=lists.ADULTS_ONLY):
            d.brain.call("door_ask", area="lists", verb="list.delete", person=KID, target="list-qov", owner=OWNER, visibility="household")
        with pytest.raises(wire.WireError):
            d.brain.call("door_ask", area="nowhere", verb="item.add", person=OWNER, target="list-qov", owner=OWNER)
        with pytest.raises(wire.WireError):
            d.brain.call("door_ask", area="lists", verb="item.add", person=OWNER, target="list-qov", text="words")   # no field for text
        with pytest.raises(Refused, match=lists.UNKNOWN_PERSON):
            d.brain.call("door_ask", area="lists", verb="item.add", person=STRANGER, target="list-qov", owner=OWNER, visibility="household")
        people = json.loads((state / "people.json").read_text())
        people["people"].append({"id": STRANGER, "role": "adult"})
        (state / "people.json").write_text(json.dumps(people))
        assert d.brain.call("door_ask", area="lists", verb="item.add", person=STRANGER, target="list-qov", owner=OWNER,
                            visibility="household")["ask"]           # the roster is re-read where the kernel decides
    finally:
        d.stop()
