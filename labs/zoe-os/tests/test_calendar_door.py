"""The calendar area of the door (``zoe_os/calendar_door.py``): the kernel decides and records a change to a calendar event by
ids; it holds no event, and no title, day, time, place or guest reaches it.

One test per row of the permission table; the record (ids only, under the ``event`` key); the rules every area shares, proven
again here (unknown person never written, a stale erase stamp writes nothing that names anyone); and the same verbs over the
daemon's wire. Each test names the control that was run against it (the code broken, the test seen to fail, the code restored).
Invented people and ids only.
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

from zoe_os import calendar_door, door
from zoe_os.capability import ADULT, CHILD, GUEST, Person
from zoe_os.kernel import Kernel
from zoe_os.spine import Refused

OWNER, ADULT2, KID, KID2, VISITOR, STRANGER = "member-ova", "member-adu", "member-kip", "member-kop", "member-gus", "member-zzz"
PEOPLE = {p.id: p for p in (Person(OWNER, ADULT), Person(ADULT2, ADULT), Person(KID, CHILD, guardians=(OWNER,)),
                            Person(KID2, CHILD, guardians=(OWNER,)), Person(VISITOR, GUEST))}
VERBS = calendar_door.VERBS
CHANGE = ("event.edit", "event.delete")
OLD, AT, NEW = [7, 2], [7, 3], [7, 4]
KIOSK = ("kiosk", "panel")


def allowed(verb, actor, **kw):
    return calendar_door.decide(PEOPLE, verb, actor, **kw)


# ---- the permission table, one rule each ---------------------------------------------------------------------------------------
@pytest.mark.parametrize("verb", VERBS)
@pytest.mark.parametrize("who", [OWNER, KID, VISITOR])
@pytest.mark.parametrize("visibility", ["private", "household"])
def test_a_person_may_do_everything_to_an_event_on_their_own_calendar(verb, who, visibility):
    """Control run: with the ``owner == actor`` rule removed, a child's ``event.edit`` of their own household event is ``adults_only``."""
    assert allowed(verb, who, owner=who, visibility=visibility).allowed


@pytest.mark.parametrize("verb", VERBS)
def test_an_adult_may_add_change_and_delete_an_event_for_a_child_but_not_another_adults_private_one(verb):
    """Control run: with ``whom.role == CHILD`` dropped from the rule, another adult's private event is allowed."""
    assert allowed(verb, ADULT2, owner=KID, visibility="private").allowed
    assert allowed(verb, ADULT2, owner=OWNER, visibility="private") == (False, door.NOT_OWNER, None)
    assert allowed(verb, ADULT2, owner=VISITOR, visibility="private").reason == door.NOT_OWNER      # a guest is not a child


@pytest.mark.parametrize("verb", VERBS)
@pytest.mark.parametrize("who, via", [(KID2, None), (VISITOR, None), KIOSK])
def test_a_child_a_guest_and_the_panel_never_touch_another_persons_private_event(verb, who, via):
    """Control run: with the private rule's last line returning ``Decision(True)`` this fails for every verb."""
    for owner in (OWNER, KID):
        assert allowed(verb, who, via=via, owner=owner, visibility="private").reason == door.NOT_OWNER
    assert allowed(verb, who, via=via, owner=None, visibility="private").reason == door.NOT_OWNER


@pytest.mark.parametrize("who, via", [(OWNER, None), (KID, None), (VISITOR, None), KIOSK])
@pytest.mark.parametrize("owner", [None, OWNER, KID])
def test_anyone_in_the_house_and_the_panel_may_add_a_household_event(who, via, owner):
    """Control run: with the create rule removed, a child's household ``event.create`` is ``adults_only``."""
    assert allowed("event.create", who, via=via, owner=owner, visibility="household").allowed


@pytest.mark.parametrize("verb", CHANGE)
def test_a_household_event_is_changed_by_its_maker_or_an_adult(verb):
    """Control run: with the adult rule requiring ``owner == actor`` another adult's change is refused; with the last line
    returning ``Decision(True)`` the child's and the guest's are allowed."""
    assert allowed(verb, KID, owner=KID, visibility="household").allowed                  # its maker, a child
    assert allowed(verb, ADULT2, owner=OWNER, visibility="household").allowed             # an adult, another adult's
    assert allowed(verb, ADULT2, owner=KID, visibility="household").allowed
    assert allowed(verb, ADULT2, owner=None, visibility="household").allowed              # an adult, the house's own
    for who in (KID2, VISITOR):
        for owner in (OWNER, KID, None):
            assert allowed(verb, who, owner=owner, visibility="household").reason == door.ADULTS_ONLY, (who, owner)


@pytest.mark.parametrize("verb", CHANGE)
def test_the_panel_changes_only_the_houses_own_household_events(verb):
    """The ones made at the panel are on nobody's calendar (no owner): the panel is their maker. Control run: with the
    ``owner is None`` condition dropped the panel may delete a member's household event."""
    assert allowed(verb, "kiosk", via="panel", owner=None, visibility="household").allowed
    for owner in (OWNER, KID):
        assert allowed(verb, "kiosk", via="panel", owner=owner, visibility="household").reason == door.ADULTS_ONLY


@pytest.mark.parametrize("verb", VERBS)
@pytest.mark.parametrize("visibility", ["private", "household"])
def test_someone_not_on_the_roster_is_refused_everything(verb, visibility):
    """Control run: with the roster check skipped for ``event.create``, a stranger's household ``event.create`` is allowed."""
    assert allowed(verb, STRANGER, owner=STRANGER, visibility=visibility).reason == door.UNKNOWN_PERSON
    assert allowed(verb, STRANGER, owner=None, visibility=visibility).reason == door.UNKNOWN_PERSON


def test_an_event_whose_owner_or_visibility_was_not_told_is_someone_elses_private_one():
    for verb in VERBS:
        assert allowed(verb, OWNER).reason == door.NOT_OWNER
    assert calendar_door.VERBS == ("event.create", "event.edit", "event.delete")      # reading has no verb: it is never asked


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
    """Control run: with the area given ``target="alarm"`` the payload assertion fails."""
    k = kernel(tmp_path)
    a = k.door.ask("door", "calendar", "event.edit", ADULT2, target="ev0000000001", owner=KID, visibility="private")
    p = k.door.ask("door", "calendar", "event.create", "kiosk-1", via="panel", target="ev0000000002", visibility="household")
    assert k.spine._get(a["seq"]).payload == {"action": "event.edit", "skill": "door", "grant_id": None, "proof": None, "verified": False,
                                              "outcome": "allowed", "reversible": False, "vis": "private", "event": "ev0000000001",
                                              "owner": KID}
    made = k.spine._get(p["seq"])
    assert made.payload["event"] == "ev0000000002" and "owner" not in made.payload and made.subject is None
    assert k.door.report("door", p["ask"], "done")["outcome"] == "done"
    for area, verb in (("calendar", "alarm.edit"), ("alarms", "event.edit"), ("reminders", "event.delete")):
        with pytest.raises(ValueError):
            k.door.ask("door", area, verb, OWNER, target="x1", owner=OWNER)
    k.close()
    assert naming(tmp_path, "kiosk-1") == {}


def test_a_person_the_kernel_does_not_know_is_never_written(tmp_path):
    k = kernel(tmp_path)
    for actor, owner, vis, reason in ((STRANGER, STRANGER, "private", door.UNKNOWN_PERSON), (STRANGER, None, "household", door.UNKNOWN_PERSON),
                                      (OWNER, STRANGER, "private", door.NOT_OWNER), (OWNER, STRANGER, "household", door.UNKNOWN_PERSON)):
        with pytest.raises(Refused) as exc:           # (the last: an adult MAY change a household event, but not one that names a stranger)
            k.door.ask("door", "calendar", "event.edit", actor, target="ev0000000001", owner=owner, visibility=vis)
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
            k.door.ask("door", "calendar", verb, KID, target="ev0000000001", owner=KID, visibility="private", stamps={KID: stamp})
        assert exc.value.wire == door.ERASED
    k.close()
    assert naming(tmp_path, KID) == {}
    k = kernel(tmp_path)
    assert k.door.ask("door", "calendar", verb, KID, target="ev0000000001", owner=KID, visibility="private", stamps={KID: NEW})["ask"]
    k.close()


# ---- the same verbs on the daemon's wire -----------------------------------------------------------------------------------------
def test_the_daemon_serves_the_calendar_on_its_existing_socket(tmp_path):
    """Control run: with the calendar verbs left out of the daemon's brain manifest every ask is ``not_in_manifest``."""
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
            return d.brain.call("door_ask", area="calendar", verb=verb, person=person, target="ev0000000001", **kw)

        out = call("event.create", "kiosk-1", via="panel", visibility="household")             # the wall panel adds a household event
        assert d.brain.call("door_report", ask=out["ask"], outcome="done")["outcome"] == "done"
        assert call("event.delete", "kiosk-1", via="panel", visibility="household")["ask"]     # ... and removes the house's own
        assert call("event.edit", OWNER, owner=KID, visibility="private")["ask"]
        assert call("event.create", VISITOR, owner=VISITOR, visibility="household")["ask"]
        for verb, person, kw, reason in (
                ("event.delete", "kiosk-1", {"via": "panel", "owner": OWNER, "visibility": "household"}, door.ADULTS_ONLY),
                ("event.edit", KID, {"owner": OWNER, "visibility": "household"}, door.ADULTS_ONLY),
                ("event.edit", KID, {"owner": OWNER, "visibility": "private"}, door.NOT_OWNER),
                ("event.create", STRANGER, {"owner": STRANGER, "visibility": "household"}, door.UNKNOWN_PERSON)):
            with pytest.raises(Refused) as exc:
                call(verb, person, **kw)
            assert str(exc.value) == reason
        with pytest.raises(wire.WireError):
            call("event.edit", OWNER, owner=OWNER, title="words")                     # no field for a title, a day or a place
    finally:
        d.stop()
    assert b"kiosk-1" not in (state / "kernel" / "zoe-os.db").read_bytes()


# ---- the calendar of a FORMER member: nobody's to change (review round 4; ``door.Door.ask`` checks the owner by the roster) ------
from test_roster_sync import BRAIN, built, state, write  # noqa: E402,F401 - the daemon build fixture and its roster file


def _event(srv, verb: str, person: str, *, owner: str | None, visibility: str, via: str | None = None) -> str:
    """The daemon's own door op (the roster file re-read first, as on the wire): ``ok`` or the refusal code."""
    try:
        with srv.kernel.spine._lock:
            srv._op_door_ask(BRAIN, {"area": "calendar", "verb": verb, "person": person, "target": "ev0000000001", "owner": owner,
                                     "visibility": visibility, "via": via})
        return "ok"
    except Refused as exc:
        return exc.wire


def test_an_event_on_the_calendar_of_a_member_taken_off_the_roster_is_nobodys_to_change(built):
    """Review round 4 (#2691): the kernel keeps a member taken off people.json in its people map while a record names them (so
    it can be forgotten), and the door checked an event's OWNER against that map. So an adult could still add, change or
    delete an event on a former member's calendar, a child or the panel could still add a household one there. Through the
    real daemon build: every ask the table allowed while the member was on the roster is ``unknown_person`` once they are
    off it (whoever asks: an adult, a guardian, a child, the panel), what the table refused stays refused, the former member
    can still be forgotten, and back on the roster their calendar is changed as before.

    Control run: ``owner not in acting`` put back to ``owner not in people`` in ``door.Door.ask`` -> the first ask that was
    allowed is ``ok`` again and this fails."""
    daemon, k, srv, path = built
    everyone = [{"id": OWNER, "role": "adult"}, {"id": ADULT2, "role": "adult"}, {"id": KID, "role": "child", "guardians": [OWNER]},
                {"id": KID2, "role": "child", "guardians": [OWNER]}]
    asks = [(verb, who, visibility, via) for verb in VERBS for visibility in ("household", "private")
            for who, via in ((OWNER, None), (ADULT2, None), (KID2, None), ("kiosk-1", "panel"))]

    def answers() -> dict:
        return {a: _event(srv, a[0], a[1], owner=KID, visibility=a[2], via=a[3]) for a in asks}

    write(path, everyone)
    assert _event(srv, "event.create", KID, owner=KID, visibility="private") == "ok"              # a record names them
    on_the_roster = answers()
    were_allowed = [a for a in asks if on_the_roster[a] == "ok"]
    assert {("event.edit", OWNER, "private", None), ("event.delete", ADULT2, "household", None),
            ("event.create", KID2, "household", None), ("event.create", "kiosk-1", "household", "panel")} <= set(were_allowed)
    write(path, [person for person in everyone if person["id"] != KID])                           # KID taken off the file
    assert _event(srv, "event.edit", OWNER, owner=OWNER, visibility="private") == "ok"            # the file is re-read here
    assert KID in k.grants.people and KID not in srv.roster                                       # kept (forgettable), off it
    refusals = k.spine.refusals
    off_the_roster = answers()
    for a in asks:                         # (an adult's ask for a CHILD's private event: the table no longer knows a child, not_owner)
        assert off_the_roster[a] in ((door.UNKNOWN_PERSON, door.NOT_OWNER) if a in were_allowed else (on_the_roster[a],)), a
    assert all(off_the_roster[a] == door.UNKNOWN_PERSON for a in were_allowed if a[2] == "household")
    assert "ok" not in off_the_roster.values() and k.spine.refusals == refusals + len(asks)      # refusals recorded, not errors
    write(path, everyone)
    assert answers() == on_the_roster                                                             # back on the roster: as before
    write(path, [person for person in everyone if person["id"] != KID])
    assert _event(srv, "event.delete", OWNER, owner=KID, visibility="household") == door.UNKNOWN_PERSON
    assert k.spine.forget(KID, actor=KID).count >= 1                                              # and they can still be forgotten
