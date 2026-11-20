"""The contacts area of the door (``zoe_os/people_door.py``): the kernel decides and records a change to a contact by ids; it
holds no contact, and no name, number, birthday, note or relationship reaches it.

One test per row of the permission table; the owner's one switch; the record (ids only, under the ``contact`` key); the rules
every area shares, proven again here; and the same verbs over the daemon's wire. Each test names the control that was run
against it (the code broken, the test seen to fail, the code restored). Invented people and ids only.
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

from zoe_os import door, people_door
from zoe_os.capability import ADULT, CHILD, GUEST, Person
from zoe_os.kernel import Kernel
from zoe_os.spine import Refused

OWNER, ADULT2, KID, KID2, VISITOR, STRANGER = "member-ova", "member-adu", "member-kip", "member-kop", "member-gus", "member-zzz"
PEOPLE = {p.id: p for p in (Person(OWNER, ADULT), Person(ADULT2, ADULT), Person(KID, CHILD, guardians=(OWNER,)),
                            Person(KID2, CHILD, guardians=(OWNER,)), Person(VISITOR, GUEST))}
VERBS = people_door.VERBS
OLD, AT, NEW = [7, 2], [7, 3], [7, 4]
ALLOW = people_door.Decision(True)


def decided(verb, actor, **kw):
    return people_door.decide(PEOPLE, verb, actor, **kw)


def refused(reason):
    return (False, reason, None)


# ---- the permission table, one rule each ---------------------------------------------------------------------------------------
@pytest.mark.parametrize("verb", VERBS)
@pytest.mark.parametrize("who", [OWNER, KID, VISITOR])
@pytest.mark.parametrize("visibility", ["private", "household"])
def test_a_member_may_do_everything_in_their_own_book(verb, who, visibility):
    """Control run: with the ``owner == actor`` rule removed, a child's ``contact.edit`` of their own private contact is ``not_owner``."""
    assert decided(verb, who, owner=who, visibility=visibility) == ALLOW


@pytest.mark.parametrize("verb", VERBS)
@pytest.mark.parametrize("who, via", [(ADULT2, None), (KID2, None), (VISITOR, None), ("kiosk", "panel")])
@pytest.mark.parametrize("owner", [OWNER, KID, None])
def test_a_private_contact_is_its_owners_alone_from_adults_too(verb, who, via, owner):
    """Control run: with the private rule allowing an adult, ADULT2's delete of KID's private contact is allowed."""
    assert decided(verb, who, via=via, owner=owner, visibility="private") == refused(door.NOT_OWNER)


@pytest.mark.parametrize("verb", VERBS)
@pytest.mark.parametrize("who", [ADULT2, KID2, VISITOR])
@pytest.mark.parametrize("owner", [OWNER, KID])
def test_anyone_in_the_house_may_add_change_or_delete_a_household_contact(verb, who, owner):
    """Today's rule. Control run: with ``HOUSEHOLD_CHANGE_ADULTS_ONLY`` set True, a child's ``contact.delete`` is ``adults_only``."""
    assert people_door.HOUSEHOLD_CHANGE_ADULTS_ONLY is False
    assert decided(verb, who, owner=owner, visibility="household") == ALLOW


def test_the_panel_may_add_a_household_contact_and_nothing_else():
    """Control run: with the panel rule dropped, the panel's ``contact.delete`` of a household contact raises (no role to read)
    or is allowed; either way this fails."""
    for owner in (OWNER, None):
        assert decided("contact.create", "kiosk", via="panel", owner=owner, visibility="household") == ALLOW
        for verb in ("contact.edit", "contact.delete"):
            assert decided(verb, "kiosk", via="panel", owner=owner, visibility="household") == refused(door.NOT_OWNER)


def test_the_owners_switch_makes_a_household_change_adults_only(monkeypatch):
    """ONE line for the owner to switch. Control run: with the switch's rule requiring a child (``who.role == ADULT``), an
    adult's change is refused and this fails."""
    monkeypatch.setattr(people_door, "HOUSEHOLD_CHANGE_ADULTS_ONLY", True)
    for verb in ("contact.edit", "contact.delete"):
        assert decided(verb, ADULT2, owner=KID, visibility="household") == ALLOW
        assert decided(verb, KID, owner=KID, visibility="household") == ALLOW                 # its owner, a child
        for who in (KID2, VISITOR):
            assert decided(verb, who, owner=OWNER, visibility="household") == refused(door.ADULTS_ONLY)
    assert decided("contact.create", KID2, owner=OWNER, visibility="household") == ALLOW      # anyone still adds


@pytest.mark.parametrize("verb", VERBS)
@pytest.mark.parametrize("visibility", ["private", "household"])
def test_someone_not_on_the_roster_is_refused_everything(verb, visibility):
    """Control run: with the create rule placed before the roster check, a stranger's household ``contact.create`` is allowed."""
    assert decided(verb, STRANGER, owner=STRANGER, visibility=visibility) == refused(door.UNKNOWN_PERSON)
    assert decided(verb, STRANGER, owner=None, visibility=visibility) == refused(door.UNKNOWN_PERSON)


def test_a_contact_whose_owner_or_visibility_was_not_told_is_someone_elses_private_one():
    for verb in VERBS:
        assert decided(verb, OWNER) == refused(door.NOT_OWNER)
    assert VERBS == ("contact.create", "contact.edit", "contact.delete")              # reading has no verb: it is never asked


# ---- the record ----------------------------------------------------------------------------------------------------------------
def kernel(tmp_path):
    k = Kernel(str(tmp_path / "k.db"), list(PEOPLE.values()))
    k.grants.install({"skill": "door", "acts": list(VERBS)})
    return k


def naming(tmp_path, member) -> dict:
    con = sqlite3.connect(str(tmp_path / "k.db"))
    try:
        found = {}
        for (table,) in con.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"):
            n = sum(1 for row in con.execute(f"SELECT * FROM {table}") if any(
                member in (c.decode("utf-8", "replace") if isinstance(c, bytes) else str(c)) for c in row))
            if n:
                found[table] = n
        return found
    finally:
        con.close()


def test_an_ask_records_ids_only_under_its_own_key_and_a_verb_belongs_to_its_own_area(tmp_path):
    """Control run: with the area given ``target="note"`` the payload assertion fails."""
    k = kernel(tmp_path)
    a = k.door.ask("door", "people", "contact.edit", KID2, target="pp0000000001", owner=OWNER, visibility="household")
    p = k.door.ask("door", "people", "contact.create", "kiosk-1", via="panel", target="pp0000000002", visibility="household")
    assert k.spine._get(a["seq"]).payload == {"action": "contact.edit", "skill": "door", "grant_id": None, "proof": None, "verified": False,
                                              "outcome": "allowed", "reversible": False, "vis": "household", "contact": "pp0000000001",
                                              "owner": OWNER}
    made = k.spine._get(p["seq"])
    assert made.payload["contact"] == "pp0000000002" and "owner" not in made.payload and made.subject is None
    assert k.door.report("door", p["ask"], "done")["outcome"] == "done"
    for area, verb in (("people", "note.edit"), ("notes", "contact.edit"), ("calendar", "contact.delete")):
        with pytest.raises(ValueError):
            k.door.ask("door", area, verb, OWNER, target="x1", owner=OWNER)
    k.close()
    assert naming(tmp_path, "kiosk-1") == {}


def test_a_person_the_kernel_does_not_know_is_never_written(tmp_path):
    k = kernel(tmp_path)
    for actor, owner, vis, reason in ((STRANGER, STRANGER, "private", door.UNKNOWN_PERSON), (STRANGER, None, "household", door.UNKNOWN_PERSON),
                                      (OWNER, STRANGER, "private", door.NOT_OWNER), (OWNER, STRANGER, "household", door.UNKNOWN_PERSON)):
        with pytest.raises(Refused) as exc:           # (the last: anyone MAY change a household contact, but not one in a stranger's book)
            k.door.ask("door", "people", "contact.edit", actor, target="pp0000000001", owner=owner, visibility=vis)
        assert exc.value.wire == reason, (actor, owner, vis)
    k.close()
    assert naming(tmp_path, STRANGER) == {}


@pytest.mark.parametrize("verb", VERBS)
def test_an_ask_made_before_a_forget_that_arrives_after_it_writes_nothing_that_names_anyone(tmp_path, verb):
    """Control run: with ``stale`` forced False in ``Door.ask`` every case is recorded and this fails."""
    k = kernel(tmp_path)
    k.spine.forget(KID, actor=KID, stamp=AT)
    for stamp in (OLD, AT):
        with pytest.raises(Refused) as exc:
            k.door.ask("door", "people", verb, KID, target="pp0000000001", owner=KID, visibility="private", stamps={KID: stamp})
        assert exc.value.wire == door.ERASED
    k.close()
    assert naming(tmp_path, KID) == {}
    k = kernel(tmp_path)
    assert k.door.ask("door", "people", verb, KID, target="pp0000000001", owner=KID, visibility="private", stamps={KID: NEW})["ask"]
    k.close()


# ---- the same verbs on the daemon's wire -----------------------------------------------------------------------------------------
def test_the_daemon_serves_contacts_on_its_existing_socket(tmp_path):
    """Control run: with the contact verbs left out of the daemon's brain manifest every ask is ``not_in_manifest``."""
    from test_shadow_daemon import Shadow, settled
    from zoe_os import wire

    state = Path(tempfile.mkdtemp(prefix="zd"))
    state.chmod(0o700)
    settled(state)
    (state / "people.json").write_text(json.dumps({"people": [{"id": OWNER, "role": "adult"}, {"id": KID, "role": "child",
                                                                                                "guardians": [OWNER]}, VISITOR]}))
    d = Shadow(state)
    try:
        def call(verb, person, **kw):
            return d.brain.call("door_ask", area="people", verb=verb, person=person, target="pp0000000001", **kw)

        out = call("contact.create", "kiosk-1", via="panel", visibility="household")
        assert d.brain.call("door_report", ask=out["ask"], outcome="done")["outcome"] == "done"
        assert call("contact.delete", KID, owner=OWNER, visibility="household")["ask"]            # today's rule
        assert call("contact.edit", VISITOR, owner=VISITOR, visibility="private")["ask"]
        for verb, person, kw, reason in (
                ("contact.delete", "kiosk-1", {"via": "panel", "owner": OWNER, "visibility": "household"}, door.NOT_OWNER),
                ("contact.edit", OWNER, {"owner": KID, "visibility": "private"}, door.NOT_OWNER),
                ("contact.create", STRANGER, {"owner": STRANGER, "visibility": "household"}, door.UNKNOWN_PERSON)):
            with pytest.raises(Refused) as exc:
                call(verb, person, **kw)
            assert str(exc.value) == reason
        with pytest.raises(wire.WireError):
            call("contact.edit", OWNER, owner=OWNER, name="words")                    # no field for a name, a number or a note
    finally:
        d.stop()
    assert b"kiosk-1" not in (state / "kernel" / "zoe-os.db").read_bytes()
