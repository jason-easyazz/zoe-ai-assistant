"""The notes and journal areas of the door (``zoe_os/notes_door.py``): the kernel decides and records a change to a note or a
journal entry by ids; it holds neither, and no title, text, mood or tag reaches it.

One test per row of each permission table; the record (ids only, under the ``note`` / ``entry`` key); the rules every area
shares, proven again here; and the same verbs over the daemon's wire. Each test names the control that was run against it (the
code broken, the test seen to fail, the code restored). Invented people and ids only.
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

from zoe_os import door, notes_door
from zoe_os.capability import ADULT, CHILD, GUEST, Person
from zoe_os.kernel import Kernel
from zoe_os.spine import Refused

OWNER, ADULT2, KID, KID2, VISITOR, STRANGER = "member-ova", "member-adu", "member-kip", "member-kop", "member-gus", "member-zzz"
PEOPLE = {p.id: p for p in (Person(OWNER, ADULT), Person(ADULT2, ADULT), Person(KID, CHILD, guardians=(OWNER,)),
                            Person(KID2, CHILD, guardians=(OWNER,)), Person(VISITOR, GUEST))}
NOTE, JOURNAL = notes_door.NOTE_VERBS, notes_door.JOURNAL_VERBS
OLD, AT, NEW = [7, 2], [7, 3], [7, 4]


def note(verb, actor, **kw):
    return notes_door.decide_note(PEOPLE, verb, actor, **kw)


def journal(verb, actor, **kw):
    return notes_door.decide_journal(PEOPLE, verb, actor, **kw)


# ---- notes -----------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("verb", NOTE)
@pytest.mark.parametrize("who", [OWNER, KID, VISITOR])
@pytest.mark.parametrize("visibility", ["private", "household"])
def test_an_author_may_do_everything_to_their_own_note(verb, who, visibility):
    """Control run: with the ``owner == actor`` rule removed, a child's own private ``note.edit`` is ``not_owner``."""
    assert note(verb, who, owner=who, visibility=visibility).allowed


@pytest.mark.parametrize("verb", NOTE)
@pytest.mark.parametrize("who", [ADULT2, KID2, VISITOR])
@pytest.mark.parametrize("owner", [OWNER, KID, None])
def test_nobody_else_touches_a_private_note_not_even_an_adult_a_childs(verb, who, owner):
    """Control run: with an "adult, for a child" allowance added to the private rule, ADULT2's delete of KID's note is allowed."""
    assert note(verb, who, owner=owner, visibility="private") == (False, door.NOT_OWNER, None)


@pytest.mark.parametrize("verb", ["note.edit", "note.delete"])
def test_a_household_note_is_changed_by_its_author_or_an_adult(verb):
    """Control run: with the adult rule dropped, ADULT2's edit is ``adults_only``; with the last line allowing, a child's is allowed."""
    assert note(verb, ADULT2, owner=KID, visibility="household").allowed and note(verb, ADULT2, owner=OWNER, visibility="household").allowed
    for who in (KID2, VISITOR):
        assert note(verb, who, owner=OWNER, visibility="household").reason == door.ADULTS_ONLY


@pytest.mark.parametrize("who", [ADULT2, KID2, VISITOR])
def test_nobody_makes_a_note_in_another_persons_name(who):
    """Control run: with the create rule removed, an adult's ``note.create`` under a child's name is allowed."""
    assert note("note.create", who, owner=KID, visibility="household").reason == door.NOT_OWNER
    assert note("note.create", who, owner=None, visibility="household").reason == door.NOT_OWNER


@pytest.mark.parametrize("verb", (*NOTE, *JOURNAL))
@pytest.mark.parametrize("owner, visibility", [(OWNER, "private"), (OWNER, "household"), (None, "household")])
def test_the_shared_panel_writes_no_notes_and_no_journal(verb, owner, visibility):
    """Control run: with the panel rule dropped from ``decide_note`` the panel falls to the roster check (``unknown_person``)."""
    decide = note if verb in NOTE else journal
    assert decide(verb, "kiosk", via="panel", owner=owner, visibility=visibility).reason == door.NOT_OWNER


@pytest.mark.parametrize("verb", (*NOTE, *JOURNAL))
def test_someone_not_on_the_roster_is_refused_everything(verb):
    """Control run: with the roster check dropped from ``decide_journal`` a stranger's own entry is allowed."""
    decide = note if verb in NOTE else journal
    assert decide(verb, STRANGER, owner=STRANGER, visibility="private").reason == door.UNKNOWN_PERSON
    assert decide(verb, STRANGER, owner=None, visibility="household").reason == door.UNKNOWN_PERSON


# ---- the journal -------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("verb", JOURNAL)
@pytest.mark.parametrize("who", [OWNER, KID, VISITOR])
def test_a_writer_may_do_everything_to_their_own_journal(verb, who):
    assert journal(verb, who, owner=who).allowed and journal(verb, who, owner=who, visibility="household").allowed


@pytest.mark.parametrize("verb", JOURNAL)
@pytest.mark.parametrize("who", [OWNER, ADULT2, KID2, VISITOR])
@pytest.mark.parametrize("visibility", ["private", "household"])
def test_a_journal_is_private_to_its_writer_even_from_an_adult_and_whatever_its_visibility_says(verb, who, visibility):
    """An adult may NOT change a child's journal. Control run: with ``decide_journal`` made ``decide_note`` (so an adult may
    change a household one) the household case is allowed for both adults."""
    assert journal(verb, who, owner=KID, visibility=visibility) == (False, door.NOT_OWNER, None)
    assert journal(verb, who, owner=None, visibility=visibility) == (False, door.NOT_OWNER, None)


# ---- the record ----------------------------------------------------------------------------------------------------------------
def kernel(tmp_path):
    k = Kernel(str(tmp_path / "k.db"), list(PEOPLE.values()))
    k.grants.install({"skill": "door", "acts": [*NOTE, *JOURNAL]})
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
    """Control run: with ``JOURNAL`` given ``target="note"`` the entry's payload assertion fails."""
    k = kernel(tmp_path)
    a = k.door.ask("door", "notes", "note.edit", ADULT2, target="nt0000000001", owner=KID, visibility="household")
    j = k.door.ask("door", "journal", "journal.delete", KID, target="je0000000001", owner=KID, visibility="private")
    assert k.spine._get(a["seq"]).payload == {"action": "note.edit", "skill": "door", "grant_id": None, "proof": None, "verified": False,
                                              "outcome": "allowed", "reversible": False, "vis": "household", "note": "nt0000000001",
                                              "owner": KID}
    kept = k.spine._get(j["seq"]).payload
    assert kept["entry"] == "je0000000001" and "note" not in kept
    assert k.door.report("door", j["ask"], "done")["outcome"] == "done"
    for area, verb in (("notes", "journal.edit"), ("journal", "note.edit"), ("calendar", "note.edit")):
        with pytest.raises(ValueError):
            k.door.ask("door", area, verb, OWNER, target="x1", owner=OWNER)
    k.close()


def test_a_person_the_kernel_does_not_know_is_never_written(tmp_path):
    k = kernel(tmp_path)
    for area, verb, actor, owner, vis, reason in (("notes", "note.create", STRANGER, STRANGER, "private", door.UNKNOWN_PERSON),
                                                  ("journal", "journal.create", STRANGER, STRANGER, "private", door.UNKNOWN_PERSON),
                                                  ("notes", "note.edit", OWNER, STRANGER, "household", door.UNKNOWN_PERSON),
                                                  ("journal", "journal.edit", OWNER, STRANGER, "private", door.NOT_OWNER)):
        with pytest.raises(Refused) as exc:
            k.door.ask("door", area, verb, actor, target="x00000000001", owner=owner, visibility=vis)
        assert exc.value.wire == reason, (area, actor, owner)
    k.close()
    assert naming(tmp_path, STRANGER) == {}


@pytest.mark.parametrize("area, verb", [("notes", "note.delete"), ("journal", "journal.create")])
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
def test_the_daemon_serves_notes_and_the_journal_on_its_existing_socket(tmp_path):
    """Control run: with the note and journal verbs left out of the daemon's brain manifest every ask is ``not_in_manifest``."""
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

        out = call("notes", "note.create", KID, owner=KID, visibility="private")
        assert d.brain.call("door_report", ask=out["ask"], outcome="done")["outcome"] == "done"
        assert call("notes", "note.delete", OWNER, owner=KID, visibility="household")["ask"]
        assert call("journal", "journal.create", VISITOR, owner=VISITOR, visibility="private")["ask"]
        for area, verb, person, kw, reason in (
                ("notes", "note.delete", OWNER, {"owner": KID, "visibility": "private"}, door.NOT_OWNER),
                ("notes", "note.edit", KID, {"owner": OWNER, "visibility": "household"}, door.ADULTS_ONLY),
                ("notes", "note.create", "kiosk-1", {"via": "panel", "visibility": "household"}, door.NOT_OWNER),
                ("journal", "journal.edit", OWNER, {"owner": KID, "visibility": "private"}, door.NOT_OWNER),
                ("journal", "journal.create", STRANGER, {"owner": STRANGER, "visibility": "private"}, door.UNKNOWN_PERSON)):
            with pytest.raises(Refused) as exc:
                call(area, verb, person, **kw)
            assert str(exc.value) == reason
        with pytest.raises(wire.WireError):
            call("notes", "note.edit", OWNER, owner=OWNER, title="words")             # no field for a title, a text or a mood
    finally:
        d.stop()
    assert b"kiosk-1" not in (state / "kernel" / "zoe-os.db").read_bytes()
