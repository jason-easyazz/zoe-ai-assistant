"""The alarms and timers areas of the door (``zoe_os/alarm_door.py``): the kernel decides and records an alarm or timer change by
ids; it holds no alarm and no timer, and no label, time or sound reaches it.

One test per row of the permission table; the record (ids only, under the ``alarm`` / ``timer`` key); the rules every area
shares, proven again here (unknown person never written, a stale erase stamp writes nothing that names anyone); and the same
verbs over the daemon's wire. Each test names the control that was run against it (the code broken, the test seen to fail, the
code restored). Invented people and ids only.
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

from zoe_os import alarm_door, door
from zoe_os.capability import ADULT, CHILD, GUEST, Person
from zoe_os.kernel import Kernel
from zoe_os.spine import Refused

OWNER, ADULT2, KID, KID2, VISITOR, STRANGER = "member-ova", "member-adu", "member-kip", "member-kop", "member-gus", "member-zzz"
PEOPLE = {p.id: p for p in (Person(OWNER, ADULT), Person(ADULT2, ADULT), Person(KID, CHILD, guardians=(OWNER,)),
                            Person(KID2, CHILD, guardians=(OWNER,)), Person(VISITOR, GUEST))}
VERBS = (*alarm_door.ALARM_VERBS, *alarm_door.TIMER_VERBS)
CHANGE = tuple(v for v in VERBS if v not in alarm_door.SILENCE_VERBS)
OLD, AT, NEW = [7, 2], [7, 3], [7, 4]


def allowed(verb, actor, **kw):
    return alarm_door.decide(PEOPLE, verb, actor, **kw)


# ---- the permission table, one rule each ---------------------------------------------------------------------------------------
@pytest.mark.parametrize("verb", VERBS)
@pytest.mark.parametrize("who", [OWNER, KID, VISITOR])
def test_a_person_may_do_everything_to_their_own(verb, who):
    """Control run: with the ``owner == actor`` rule removed, a child's own ``alarm.edit`` is ``not_owner``."""
    assert allowed(verb, who, owner=who, visibility="private").allowed


@pytest.mark.parametrize("verb", CHANGE)
def test_an_adult_may_change_one_for_a_child_but_not_another_adults_private_one(verb):
    """Control run: with ``whom.role == CHILD`` dropped from the rule, another adult's private alarm is allowed."""
    assert allowed(verb, ADULT2, owner=KID, visibility="private").allowed
    assert allowed(verb, ADULT2, owner=OWNER, visibility="private") == (False, door.NOT_OWNER, None)
    assert allowed(verb, ADULT2, owner=VISITOR, visibility="private").reason == door.NOT_OWNER      # a guest is not a child


@pytest.mark.parametrize("verb", CHANGE)
@pytest.mark.parametrize("who, via", [(KID2, None), (VISITOR, None), ("kiosk", "panel")])
def test_a_child_a_guest_and_the_panel_never_change_another_persons_private_one(verb, who, via):
    """Control run: with the last line returning ``Decision(True)`` this fails for every change verb."""
    for owner in (OWNER, KID):
        assert allowed(verb, who, via=via, owner=owner, visibility="private").reason == door.NOT_OWNER


@pytest.mark.parametrize("verb", alarm_door.SILENCE_VERBS)
@pytest.mark.parametrize("who, via", [(OWNER, None), (ADULT2, None), (KID2, None), (VISITOR, None), ("kiosk", "panel")])
@pytest.mark.parametrize("owner, visibility", [(OWNER, "private"), (KID, "private"), (None, "household"), (None, "private")])
def test_whoever_is_standing_there_may_silence_what_is_ringing(verb, who, via, owner, visibility):
    """Anyone on the roster, and the shared panel with nobody identified, whoever it is for. Control run: with the silence rule
    moved below the private rule, the panel's stop of a private alarm is ``not_owner``."""
    assert allowed(verb, who, via=via, owner=owner, visibility=visibility).allowed
    assert set(alarm_door.SILENCE_VERBS) == {"alarm.snooze", "alarm.stop", "timer.stop"}


@pytest.mark.parametrize("verb", VERBS)
@pytest.mark.parametrize("who, via", [(OWNER, None), (KID, None), (VISITOR, None), ("kiosk", "panel")])
def test_the_houses_own_alarm_or_timer_is_anyones_in_the_house_and_the_panels(verb, who, via):
    """Control run: with the household rule requiring an adult, a child's household ``timer.create`` is refused."""
    assert allowed(verb, who, via=via, owner=None, visibility="household").allowed


@pytest.mark.parametrize("verb", VERBS)
@pytest.mark.parametrize("visibility", ["private", "household"])
def test_someone_not_on_the_roster_is_refused_everything_even_silencing(verb, visibility):
    """Control run: with the silence rule placed before the roster check, a stranger's ``alarm.stop`` is allowed (and recorded)."""
    assert allowed(verb, STRANGER, owner=STRANGER, visibility=visibility).reason == door.UNKNOWN_PERSON
    assert allowed(verb, STRANGER, owner=None, visibility=visibility).reason == door.UNKNOWN_PERSON


def test_one_whose_owner_or_visibility_was_not_told_is_someone_elses_private_one():
    assert allowed("alarm.edit", OWNER).reason == door.NOT_OWNER and allowed("timer.cancel", OWNER).reason == door.NOT_OWNER
    assert allowed("alarm.stop", OWNER).allowed


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
    """Control run: with ``TIMERS`` given ``target="alarm"`` the timer's payload assertion fails."""
    k = kernel(tmp_path)
    a = k.door.ask("door", "alarms", "alarm.edit", ADULT2, target="a00000000001", owner=KID, visibility="private")
    t = k.door.ask("door", "timers", "timer.stop", "kiosk-1", via="panel", target="t00000000001", owner=OWNER, visibility="private")
    assert k.spine._get(a["seq"]).payload == {"action": "alarm.edit", "skill": "door", "grant_id": None, "proof": None, "verified": False,
                                              "outcome": "allowed", "reversible": False, "vis": "private", "alarm": "a00000000001",
                                              "owner": KID}
    stopped = k.spine._get(t["seq"])
    assert stopped.payload["timer"] == "t00000000001" and "alarm" not in stopped.payload and stopped.subject is None
    assert k.door.report("door", t["ask"], "done")["outcome"] == "done"
    for area, verb in (("alarms", "timer.stop"), ("timers", "alarm.stop"), ("reminders", "alarm.edit")):
        with pytest.raises(ValueError):
            k.door.ask("door", area, verb, OWNER, target="x1", owner=OWNER)
    k.close()
    assert naming(tmp_path, "kiosk-1") == {}


def test_a_person_the_kernel_does_not_know_is_never_written(tmp_path):
    k = kernel(tmp_path)
    # (an adult's change to a stranger's private alarm is refused by the table itself, as someone else's: refused either way)
    for actor, owner, reason in ((STRANGER, STRANGER, door.UNKNOWN_PERSON), (STRANGER, None, door.UNKNOWN_PERSON),
                                 (OWNER, STRANGER, door.NOT_OWNER)):
        with pytest.raises(Refused) as exc:
            k.door.ask("door", "alarms", "alarm.create", actor, target="a00000000001", owner=owner,
                       visibility="private" if owner else "household")
        assert exc.value.wire == reason, (actor, owner)
    with pytest.raises(Refused) as exc:                       # even a SILENCE names nobody the kernel does not know
        k.door.ask("door", "timers", "timer.stop", OWNER, target="t00000000001", owner=STRANGER, visibility="private")
    assert exc.value.wire == door.UNKNOWN_PERSON
    k.close()
    assert naming(tmp_path, STRANGER) == {}


@pytest.mark.parametrize("area, verb", [("alarms", "alarm.stop"), ("timers", "timer.cancel")])
def test_an_ask_made_before_a_forget_that_arrives_after_it_writes_nothing_that_names_anyone(tmp_path, area, verb):
    """Control run: with ``stale`` forced False in ``Door.ask`` both cases are recorded and this fails."""
    k = kernel(tmp_path)
    k.spine.forget(KID, actor=KID, stamp=AT)
    for stamp in (OLD, AT):
        with pytest.raises(Refused) as exc:
            k.door.ask("door", area, verb, KID, target="x00000000001", owner=KID, visibility="private", stamps={KID: stamp})
        assert exc.value.wire == door.ERASED
    k.close()
    assert naming(tmp_path, KID) == {}
    k = kernel(tmp_path)
    assert k.door.ask("door", area, verb, KID, target="x00000000001", owner=KID, visibility="private", stamps={KID: NEW})["ask"]
    k.close()


# ---- the same verbs on the daemon's wire -----------------------------------------------------------------------------------------
def test_the_daemon_serves_alarms_and_timers_on_its_existing_socket(tmp_path):
    """Control run: with the alarm and timer verbs left out of the daemon's brain manifest every ask is ``not_in_manifest``."""
    from test_shadow_daemon import Shadow, settled
    from zoe_os import wire

    state = Path(tempfile.mkdtemp(prefix="zd"))
    state.chmod(0o700)
    settled(state)
    (state / "people.json").write_text(json.dumps({"people": [{"id": OWNER, "role": "adult"}, {"id": KID, "role": "child",
                                                                                                "guardians": [OWNER]}, VISITOR]}))
    d = Shadow(state)
    try:
        def call(area, verb, person, **kw):
            return d.brain.call("door_ask", area=area, verb=verb, person=person, target="x00000000001", **kw)

        out = call("alarms", "alarm.create", "kiosk-1", via="panel", visibility="household")        # the wall panel sets the house's alarm
        assert d.brain.call("door_report", ask=out["ask"], outcome="done")["outcome"] == "done"
        assert call("alarms", "alarm.stop", "kiosk-1", via="panel", owner=OWNER, visibility="private")["ask"]
        assert call("timers", "timer.create", KID, owner=KID, visibility="private")["ask"]
        assert call("timers", "timer.stop", VISITOR, owner=KID, visibility="private")["ask"]
        for area, verb, person, kw, reason in (
                ("alarms", "alarm.delete", "kiosk-1", {"via": "panel", "owner": OWNER, "visibility": "private"}, door.NOT_OWNER),
                ("alarms", "alarm.edit", KID, {"owner": OWNER, "visibility": "private"}, door.NOT_OWNER),
                ("timers", "timer.cancel", VISITOR, {"owner": KID, "visibility": "private"}, door.NOT_OWNER),
                ("timers", "timer.stop", STRANGER, {"owner": OWNER, "visibility": "private"}, door.UNKNOWN_PERSON)):
            with pytest.raises(Refused) as exc:
                call(area, verb, person, **kw)
            assert str(exc.value) == reason
        with pytest.raises(wire.WireError):
            call("alarms", "alarm.edit", OWNER, owner=OWNER, label="words")           # no field for a label, a time or a sound
    finally:
        d.stop()
    assert b"kiosk-1" not in (state / "kernel" / "zoe-os.db").read_bytes()
