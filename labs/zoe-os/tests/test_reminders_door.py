"""The reminders area of the door (``zoe_os/remind_door.py``): the kernel decides and records a reminder change by ids; it holds
no reminder, and no title, day or time reaches it.

One test per row of the permission table; the record (ids only, under the ``reminder`` key); the rules every area shares, proven
again for this one because a refusal here is final in production (unknown person never written, a stale erase stamp writes
nothing that names anyone, one transaction for the receipt and its open ask); the id shapes reminders use; and the same verbs
over the daemon's wire with the credentials it already issues.

Each test names the control that was run against it (the code broken, the test seen to fail, the code restored).
Invented people and ids only.
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

from zoe_os import door, remind_door
from zoe_os.capability import ADULT, CHILD, GUEST, Person
from zoe_os.events import Kind
from zoe_os.kernel import Kernel
from zoe_os.spine import Refused

OWNER, ADULT2, KID, KID2, VISITOR, STRANGER = "member-ova", "member-adu", "member-kip", "member-kop", "member-gus", "member-zzz"
PEOPLE = {p.id: p for p in (Person(OWNER, ADULT), Person(ADULT2, ADULT), Person(KID, CHILD, guardians=(OWNER,)),
                            Person(KID2, CHILD, guardians=(OWNER,)), Person(VISITOR, GUEST))}
RID = "rem_bar_0001"
OLD, AT, NEW = [7, 2], [7, 3], [7, 4]                     # a stamp read before a forget, the forget's own, one read after


def allowed(verb, actor, **kw):
    return remind_door.decide(PEOPLE, verb, actor, **kw)


# ---- the permission table, one rule each ---------------------------------------------------------------------------------------
@pytest.mark.parametrize("verb", remind_door.VERBS)
@pytest.mark.parametrize("who", [OWNER, KID, VISITOR])
@pytest.mark.parametrize("visibility", ["private", "household"])
def test_a_person_may_do_everything_to_their_own_reminder(verb, who, visibility):
    """Control run: with the ``owner == actor`` rule removed, a child's own household ``reminder.edit`` is ``adults_only``."""
    assert allowed(verb, who, owner=who, visibility=visibility).allowed


@pytest.mark.parametrize("verb", remind_door.VERBS)
def test_an_adult_may_do_everything_to_a_reminder_for_a_child_but_not_to_another_adults_private_one(verb):
    """Control run: with ``whom.role == CHILD`` dropped from the rule, another adult's private reminder is allowed."""
    assert allowed(verb, ADULT2, owner=KID, visibility="private").allowed
    assert allowed(verb, ADULT2, owner=OWNER, visibility="private") == (False, door.NOT_OWNER, None)
    assert allowed(verb, ADULT2, owner=VISITOR, visibility="private").reason == door.NOT_OWNER      # a guest is not a child


@pytest.mark.parametrize("verb", remind_door.VERBS)
@pytest.mark.parametrize("who", [KID2, VISITOR])
def test_a_child_or_a_guest_never_touches_another_persons_private_reminder(verb, who):
    """Control run: with the private branch returning ``Decision(True)`` this fails for every verb."""
    for owner in (OWNER, KID):
        assert allowed(verb, who, owner=owner, visibility="private").reason == door.NOT_OWNER


@pytest.mark.parametrize("verb", remind_door.VERBS)
def test_the_shared_panel_never_touches_a_private_reminder(verb):
    assert allowed(verb, "anything", via="panel", owner=OWNER, visibility="private").reason == door.NOT_OWNER
    assert allowed(verb, "anything", via="panel", owner=KID, visibility="private").reason == door.NOT_OWNER


@pytest.mark.parametrize("verb", remind_door.VERBS)
def test_an_adult_may_do_everything_to_a_household_reminder(verb):
    assert allowed(verb, ADULT2, owner=OWNER, visibility="household").allowed


@pytest.mark.parametrize("who, via", [(KID, None), (VISITOR, None), ("kiosk", "panel")])
def test_a_child_a_guest_and_the_panel_may_answer_a_household_reminder_but_not_change_or_delete_it(who, via):
    """Control run: with the ``ANSWER_VERBS`` rule widened to every verb, the three change verbs are allowed."""
    for verb in remind_door.ANSWER_VERBS:
        assert allowed(verb, who, via=via, owner=OWNER, visibility="household").allowed, verb
    for verb in remind_door.CHANGE_VERBS:
        assert allowed(verb, who, via=via, owner=OWNER, visibility="household") == (False, door.ADULTS_ONLY, None), verb
    assert set(remind_door.ANSWER_VERBS) | set(remind_door.CHANGE_VERBS) == set(remind_door.VERBS)


@pytest.mark.parametrize("verb", remind_door.VERBS)
@pytest.mark.parametrize("visibility", ["private", "household"])
def test_someone_not_on_the_roster_is_refused_everything(verb, visibility):
    assert allowed(verb, STRANGER, owner=STRANGER, visibility=visibility).reason == door.UNKNOWN_PERSON
    assert allowed(verb, STRANGER, owner=OWNER, visibility=visibility).reason == door.UNKNOWN_PERSON


def test_a_reminder_whose_owner_or_visibility_was_not_told_is_someone_elses_private_one():
    assert allowed("reminder.done", OWNER).reason == door.NOT_OWNER
    assert allowed("reminder.done", OWNER, visibility="household").allowed          # an adult; nothing is assumed about the owner


# ---- the record ----------------------------------------------------------------------------------------------------------------
def kernel(tmp_path):
    k = Kernel(str(tmp_path / "k.db"), list(PEOPLE.values()))
    k.grants.install({"skill": "door", "acts": list(remind_door.VERBS)})
    return k


def ask(k, verb, actor, owner=OWNER, visibility="household", **kw):
    return k.door.ask("door", "reminders", verb, actor, target=RID, owner=owner, visibility=visibility, **kw)


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


def test_an_allowed_ask_records_ids_only_and_the_report_closes_it(tmp_path):
    """Control run: with ``Area(..., target="list")`` for this area the payload assertion fails (the id sits under ``list``)."""
    k = kernel(tmp_path)
    out = ask(k, "reminder.edit", ADULT2, owner=KID, visibility="private", turn="turn-7")
    asked = k.spine._get(out["seq"])
    assert asked.kind is Kind.ACTION_RECEIPT and asked.subject == ADULT2
    assert asked.payload == {"action": "reminder.edit", "skill": "door", "grant_id": None, "proof": None, "verified": False,
                             "outcome": "allowed", "reversible": False, "vis": "private", "reminder": RID, "owner": KID,
                             "turn": "turn-7"}
    done = k.door.report("door", out["ask"], "done")
    closed = k.spine._get(done["seq"])
    assert closed.payload["outcome"] == "done" and closed.payload["verified"] is False and closed.caused_by == (out["seq"],)
    with pytest.raises(ValueError):
        k.door.report("door", out["ask"], "done")                # one report per ask
    with pytest.raises(ValueError):
        ask(k, "reminder.edit", OWNER, parts=["part-1"])         # a reminder has no parts: malformed, never recorded
    k.close()


def test_a_refusal_is_recorded_with_fixed_codes_and_the_panels_record_is_the_households(tmp_path):
    k = kernel(tmp_path)
    with pytest.raises(Refused) as exc:
        ask(k, "reminder.delete", KID)
    assert exc.value.wire == door.ADULTS_ONLY
    with pytest.raises(Refused) as exc:
        ask(k, "reminder.delete", "kiosk-1", via="panel")
    assert exc.value.wire == door.ADULTS_ONLY
    out = ask(k, "reminder.done", "kiosk-1", via="panel")
    assert k.spine._get(out["seq"]).subject is None and k.spine._get(out["seq"]).payload["via"] == "panel"
    k.close()
    assert naming(tmp_path, "kiosk-1") == {}                     # the panel's device id is never kept


def test_a_skill_whose_manifest_does_not_name_the_verb_is_refused(tmp_path):
    k = Kernel(str(tmp_path / "k.db"), list(PEOPLE.values()))
    k.grants.install({"skill": "door", "acts": ["reminder.done"]})
    assert ask(k, "reminder.done", OWNER)["ask"]
    with pytest.raises(Refused) as exc:
        ask(k, "reminder.delete", OWNER)
    assert exc.value.wire == door.NOT_IN_MANIFEST
    k.close()


def test_a_person_the_kernel_does_not_know_is_never_written(tmp_path):
    """Actor or the person the reminder is for. Control run: without the owner check in ``Door.ask`` the adult's household ask
    for a stranger's reminder is allowed and the receipt holds the stranger's id."""
    k = kernel(tmp_path)
    for actor, owner in ((STRANGER, STRANGER), (STRANGER, OWNER), (OWNER, STRANGER), (ADULT2, STRANGER)):
        with pytest.raises(Refused) as exc:
            ask(k, "reminder.create", actor, owner=owner)
        assert exc.value.wire == door.UNKNOWN_PERSON, (actor, owner)
    k.close()
    assert naming(tmp_path, STRANGER) == {}


# ---- ordered against a forget ---------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("actor, forgotten", [(KID, KID), (ADULT2, KID), (ADULT2, ADULT2)])
def test_an_ask_made_before_a_forget_that_arrives_after_it_writes_nothing_that_names_anyone(tmp_path, actor, forgotten):
    """The acting person, or the person the reminder is for, was forgotten after the caller stamped the ask. Control run: with
    ``stale`` forced False in ``Door.ask`` all three cases fail (the ask is recorded)."""
    k = kernel(tmp_path)
    k.spine.forget(forgotten, actor=forgotten, stamp=AT)
    for stamp in (OLD, AT):                                      # older, and equal (read before the forget was confirmed)
        with pytest.raises(Refused) as exc:
            ask(k, "reminder.done", actor, owner=KID, visibility="private", stamps={actor: stamp, KID: stamp})
        assert exc.value.wire == door.ERASED
    with pytest.raises(Refused):
        ask(k, "reminder.done", actor, owner=KID, visibility="private")       # no stamp for a person who has a mark: older
    k.close()
    assert naming(tmp_path, forgotten) == {}
    k = kernel(tmp_path)                                         # a strictly newer stamp: recorded afresh
    assert ask(k, "reminder.done", actor, owner=KID, visibility="private", stamps={actor: NEW, KID: NEW})["ask"]
    k.close()


def test_a_report_after_a_forget_writes_nothing_that_names_the_forgotten_person(tmp_path):
    """Control run: with the stamp check removed from ``Door.report`` the stamped report raises ValueError, not ``erased``."""
    k = kernel(tmp_path)
    out = ask(k, "reminder.snooze", KID, owner=KID, visibility="private", stamps={KID: OLD})
    k.spine.forget(KID, actor=KID, stamp=AT)
    with pytest.raises(Refused) as exc:
        k.door.report("door", out["ask"], "done", stamps={KID: OLD})
    assert exc.value.wire == door.ERASED
    with pytest.raises(ValueError):
        k.door.report("door", out["ask"], "done")                # unstamped: the forget removed the open ask, nothing to close
    k.close()
    assert naming(tmp_path, KID) == {}


def test_an_allowed_receipt_and_its_open_ask_land_together_or_not_at_all(tmp_path):
    """Control run: with the ask row written after the receipt's transaction, the receipt stays behind and the counts differ."""
    k = kernel(tmp_path)

    def events() -> int:
        return k.spine._db.execute("SELECT COUNT(*) FROM events").fetchone()[0]

    before = events()
    k.spine._db.execute("ALTER TABLE intents RENAME TO intents_away")
    with pytest.raises(sqlite3.OperationalError):
        ask(k, "reminder.create", OWNER)
    k.spine._db.execute("ALTER TABLE intents_away RENAME TO intents")
    assert events() == before
    out = ask(k, "reminder.create", OWNER)
    assert k.door.report("door", out["ask"], "failed")["outcome"] == "failed"
    k.close()


@pytest.mark.parametrize("value", ["rem_bar_0001", "3f2a9c1e-7b4d-4e2a-9c1e-7b4d4e2a9c1e", "rem1", "member_ova"])
def test_the_id_shapes_reminders_use_are_ids(tmp_path, value):
    """Production's reminder ids are uuid4 strings; older rows and fixtures carry underscores. An id rule that leaves a shape out
    makes every ask naming it malformed (the door then proceeds unasked). Control run: without the underscore in ``door._ID``
    the first and last fail."""
    k = kernel(tmp_path)
    out = k.door.ask("door", "reminders", "reminder.done", OWNER, target=value, owner=OWNER, visibility="private")
    assert k.spine._get(out["seq"]).payload["reminder"] == value
    k.close()


@pytest.mark.parametrize("value", ["", "has space", "semi;colon", "x" * 81, "new\nline"])
def test_text_that_is_not_an_id_is_never_recorded(tmp_path, value):
    k = kernel(tmp_path)
    with pytest.raises(ValueError):
        k.door.ask("door", "reminders", "reminder.done", OWNER, target=value, owner=OWNER, visibility="private")
    k.close()


# ---- the same verbs on the daemon's wire -----------------------------------------------------------------------------------------
def test_the_daemon_serves_the_reminders_area_on_its_existing_socket(tmp_path):
    """Control run: with ``REMINDER_DOOR_VERBS`` left out of the daemon's brain manifest every ask is ``not_in_manifest``."""
    from test_shadow_daemon import Shadow, settled
    from zoe_os import wire

    state = Path(tempfile.mkdtemp(prefix="zd"))                     # short: a socket path is <= 107 bytes
    state.chmod(0o700)
    settled(state)
    (state / "people.json").write_text(json.dumps({"people": [{"id": OWNER, "role": "adult"}, {"id": KID, "role": "child",
                                                                                                "guardians": [OWNER]}, VISITOR]}))
    d = Shadow(state)
    try:
        def call(verb, person, **kw):
            return d.brain.call("door_ask", area="reminders", verb=verb, person=person, target=RID, **kw)

        out = call("reminder.edit", OWNER, owner=KID, visibility="private", turn="turn-1")       # an adult, for a child
        assert d.brain.call("door_report", ask=out["ask"], outcome="done")["outcome"] == "done"
        assert call("reminder.done", KID, owner=OWNER, visibility="household")["ask"]
        assert call("reminder.done", "kiosk-1", via="panel", owner=OWNER, visibility="household")["ask"]
        for person, kw, reason in ((KID, {"verb": "reminder.delete", "owner": OWNER, "visibility": "household"}, door.ADULTS_ONLY),
                                   (VISITOR, {"verb": "reminder.edit", "owner": KID, "visibility": "private"}, door.NOT_OWNER),
                                   (STRANGER, {"verb": "reminder.create", "owner": STRANGER, "visibility": "private"}, door.UNKNOWN_PERSON),
                                   (OWNER, {"verb": "reminder.create", "owner": STRANGER, "visibility": "household"}, door.UNKNOWN_PERSON)):
            with pytest.raises(Refused) as exc:
                call(kw.pop("verb"), person, **kw)
            assert str(exc.value) == reason
        with pytest.raises(Refused, match=door.NOT_IN_MANIFEST):
            d.voice.call("door_ask", area="reminders", verb="reminder.done", person=OWNER, target=RID, owner=OWNER)
        with pytest.raises(wire.WireError):
            call("reminder.edit", OWNER, owner=OWNER, title="words")          # no field for a title, a day or a time
        with pytest.raises(wire.WireError):
            call("item.add", OWNER, owner=OWNER)                              # a lists verb is not a reminders verb
        assert d.add(OWNER, "synthetic words")["verified"] is True            # the shadow's own verbs still work beside it
    finally:
        d.stop()
    assert b"kiosk-1" not in (state / "kernel" / "zoe-os.db").read_bytes()
