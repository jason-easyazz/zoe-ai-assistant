"""The memory area of the door (``zoe_os/memory_door.py``): the kernel decides and records a member's change to a memory row by
ids; it holds no memory, and no text, name, excerpt, tag or reason reaches it.

One test per row of the permission table; the owner's one switch, both ways; forget as a told-afterwards verb; the record (ids
only, under the ``memory`` key); the rules every area shares, proven again here; and the same verbs over the daemon's wire.
Each test names the control that was run against it (the code broken, the test seen to fail, the code restored). Invented
people and ids only.
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

from zoe_os import door, memory_door
from zoe_os.capability import ADULT, CHILD, GUEST, Person
from zoe_os.kernel import Kernel
from zoe_os.spine import Refused

OWNER, ADULT2, KID, KID2, VISITOR, STRANGER = "member-ova", "member-adu", "member-kip", "member-kop", "member-gus", "member-zzz"
PEOPLE = {p.id: p for p in (Person(OWNER, ADULT), Person(ADULT2, ADULT), Person(KID, CHILD, guardians=(OWNER,)),
                            Person(KID2, CHILD, guardians=(OWNER,)), Person(VISITOR, GUEST))}
VERBS = memory_door.VERBS
ASKED = (memory_door.REMEMBER, memory_door.CORRECT, memory_door.APPROVE)
OLD, AT, NEW = [7, 2], [7, 3], [7, 4]
ALLOW = memory_door.Decision(True)
ROW = "zoe_member-kip_0123456789abcdef01234567"
AS_SHIPPED = memory_door.STRICT          # read before any test touches it


def decided(verb, actor, **kw):
    return memory_door.decide(PEOPLE, verb, actor, **kw)


def refused(reason):
    return (False, reason, None)


@pytest.fixture(autouse=True)
def todays_rule(monkeypatch):
    monkeypatch.setattr(memory_door, "STRICT", False)


# ---- the table, today's rule ---------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("verb", VERBS)
@pytest.mark.parametrize("who", [OWNER, KID, VISITOR])
@pytest.mark.parametrize("visibility", ["private", "household"])
def test_a_member_may_do_everything_to_their_own_memory(verb, who, visibility):
    """Control run: with the own-memory rule dropped a child's own private row is ``not_owner``."""
    assert decided(verb, who, owner=who, visibility=visibility) == ALLOW


@pytest.mark.parametrize("verb", VERBS)
@pytest.mark.parametrize("owner", [OWNER, KID])
def test_another_adult_may_change_a_private_row_as_production_lets_a_curator_or_an_admin(verb, owner):
    """Today's rule: production decides WHICH adult (a curator the member opted in to, an admin); the kernel knows only that
    the asker is an adult. Control run: with the adult rule dropped this is ``not_owner``."""
    assert decided(verb, ADULT2, owner=owner, visibility="private") == ALLOW


@pytest.mark.parametrize("verb", VERBS)
@pytest.mark.parametrize("who", [KID2, VISITOR])
@pytest.mark.parametrize("owner", [OWNER, KID, None])
def test_a_child_or_a_guest_never_changes_another_members_private_row(verb, who, owner):
    """Control run: with the role check dropped (any roster member allowed) a child changes an adult's private row."""
    assert decided(verb, who, owner=owner, visibility="private") == refused(door.NOT_OWNER)
    assert decided(verb, who, owner=owner) == refused(door.NOT_OWNER)                        # visibility not told: private


@pytest.mark.parametrize("verb", VERBS)
@pytest.mark.parametrize("who", [ADULT2, KID2, VISITOR])
@pytest.mark.parametrize("owner", [OWNER, KID])
def test_anyone_in_the_house_may_change_a_household_row(verb, who, owner):
    """Today's rule, kept. Control run: with the household rule answering ``not_owner`` this fails."""
    assert decided(verb, who, owner=owner, visibility="household") == ALLOW


@pytest.mark.parametrize("verb", VERBS)
@pytest.mark.parametrize("visibility", ["private", "household"])
@pytest.mark.parametrize("owner", [OWNER, None])
def test_the_panel_with_nobody_identified_changes_nothing(verb, visibility, owner):
    """A member whose voice the panel verified asks as THAT member (``via`` is not ``panel``); the bare panel is nobody.
    Control run: with the panel rule dropped the panel changes a household row."""
    assert decided(verb, "kiosk-1", via="panel", owner=owner, visibility=visibility) == refused(door.NOT_OWNER)


@pytest.mark.parametrize("verb", VERBS)
@pytest.mark.parametrize("visibility", ["private", "household"])
def test_someone_not_on_the_roster_is_refused_everything(verb, visibility):
    """Control run: with the roster check dropped a stranger changes their "own" row."""
    assert decided(verb, STRANGER, owner=STRANGER, visibility=visibility) == refused(door.UNKNOWN_PERSON)
    assert decided(verb, STRANGER, owner=OWNER, visibility=visibility) == refused(door.UNKNOWN_PERSON)
    assert VERBS == ("memory.remember", "memory.correct", "memory.approve", "memory.forget")      # reading has no verb
    assert memory_door.TOLD_VERBS == ("memory.forget",)


# ---- the owner's switch, both ways -----------------------------------------------------------------------------------------------
def test_the_strict_switch_is_off_and_when_set_private_is_the_owners_alone_and_household_needs_the_author_or_an_adult(monkeypatch):
    """Control run: with ``STRICT`` set by default the today's-rule tests above fail; with the switch ignored in ``decide`` the
    strict half below fails."""
    assert AS_SHIPPED is False
    monkeypatch.setattr(memory_door, "STRICT", True)
    for verb in ASKED:
        assert decided(verb, KID, owner=KID, visibility="private") == ALLOW                      # their own: unchanged
        assert decided(verb, ADULT2, owner=KID, visibility="private") == refused(door.NOT_OWNER)   # an adult too
        assert decided(verb, KID2, owner=KID, visibility="private") == refused(door.NOT_OWNER)
        assert decided(verb, "kiosk-1", via="panel", visibility="household") == refused(door.NOT_OWNER)
        assert decided(verb, STRANGER, owner=STRANGER, visibility="household") == refused(door.UNKNOWN_PERSON)
    for who in (ADULT2, KID2, VISITOR):
        assert decided(memory_door.REMEMBER, who, owner=who, visibility="household") == ALLOW     # anyone remembers a household fact
        assert decided(memory_door.REMEMBER, who, owner=OWNER, visibility="household") == ALLOW
    for verb in (memory_door.CORRECT, memory_door.APPROVE):
        assert decided(verb, KID, owner=KID, visibility="household") == ALLOW                    # its author
        assert decided(verb, ADULT2, owner=KID, visibility="household") == ALLOW                 # an adult
        assert decided(verb, KID2, owner=KID, visibility="household") == refused(door.ADULTS_ONLY)
        assert decided(verb, VISITOR, owner=OWNER, visibility="household") == refused(door.ADULTS_ONLY)
    # a forget is recorded, never decided: the switch leaves it exactly as it was
    assert decided(memory_door.FORGET, ADULT2, owner=KID, visibility="private") == ALLOW
    assert decided(memory_door.FORGET, KID2, owner=KID, visibility="household") == ALLOW
    assert decided(memory_door.FORGET, KID2, owner=KID, visibility="private") == refused(door.NOT_OWNER)


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
    """Control run: with the area given ``target="contact"`` the payload assertion fails."""
    k = kernel(tmp_path)
    a = k.door.ask("door", "memory", "memory.correct", KID, target=ROW, owner=KID, visibility="private")
    assert k.spine._get(a["seq"]).payload == {"action": "memory.correct", "skill": "door", "grant_id": None, "proof": None, "verified": False,
                                              "outcome": "allowed", "reversible": False, "vis": "private", "memory": ROW, "owner": KID}
    assert k.door.report("door", a["ask"], "done")["outcome"] == "done"
    new = k.door.ask("door", "memory", "memory.remember", KID, owner=KID, visibility="household")        # no row yet: no target
    assert "memory" not in k.spine._get(new["seq"]).payload
    told = k.door.ask("door", "memory", "memory.forget", OWNER, target=ROW, owner=KID, visibility="private")     # a guardian's forget
    assert k.spine._get(told["seq"]).payload["action"] == "memory.forget"
    for area, verb in (("memory", "contact.edit"), ("people", "memory.forget"), ("notes", "memory.remember")):
        with pytest.raises(ValueError):
            k.door.ask("door", area, verb, OWNER, target="x1", owner=OWNER)
    with pytest.raises(ValueError):
        k.door.ask("door", "memory", "memory.remember", KID, target="words with spaces", owner=KID)       # an id, never words
    k.close()


def test_a_forget_told_by_someone_off_the_roster_is_an_objection_and_names_nobody(tmp_path):
    """Production has already erased; the kernel's "no" is the record saying OBJECTED. Nothing that names the stranger is
    written. Control run: with the roster check dropped the stranger's forget is recorded under their id."""
    k = kernel(tmp_path)
    for actor, owner, vis, reason in ((STRANGER, STRANGER, "private", door.UNKNOWN_PERSON), (STRANGER, OWNER, "household", door.UNKNOWN_PERSON),
                                      (KID2, KID, "private", door.NOT_OWNER), (OWNER, STRANGER, "household", door.UNKNOWN_PERSON)):
        with pytest.raises(Refused) as exc:
            k.door.ask("door", "memory", "memory.forget", actor, target=ROW, owner=owner, visibility=vis)
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
            k.door.ask("door", "memory", verb, KID, target=ROW, owner=KID, visibility="private", stamps={KID: stamp})
        assert exc.value.wire == door.ERASED
    k.close()
    assert naming(tmp_path, KID) == {}
    k = kernel(tmp_path)
    assert k.door.ask("door", "memory", verb, KID, target=ROW, owner=KID, visibility="private", stamps={KID: NEW})["ask"]
    k.close()


# ---- the same verbs on the daemon's wire -----------------------------------------------------------------------------------------
def test_the_daemon_serves_memory_on_its_existing_socket_and_has_no_field_for_words(tmp_path):
    """Control run: with the memory verbs left out of the daemon's brain manifest every ask is ``not_in_manifest``."""
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
            return d.brain.call("door_ask", area="memory", verb=verb, person=person, **kw)

        out = call("memory.remember", KID, owner=KID, visibility="private")
        assert d.brain.call("door_report", ask=out["ask"], outcome="done")["outcome"] == "done"
        assert call("memory.correct", OWNER, target=ROW, owner=KID, visibility="private")["ask"]            # an adult, today's rule
        assert call("memory.approve", VISITOR, target=ROW, owner=OWNER, visibility="household")["ask"]
        assert call("memory.forget", KID, target=ROW, owner=KID, visibility="private")["ask"]
        for verb, person, kw, reason in (
                ("memory.remember", "kiosk-1", {"via": "panel", "visibility": "household"}, door.NOT_OWNER),
                ("memory.correct", VISITOR, {"target": ROW, "owner": KID, "visibility": "private"}, door.NOT_OWNER),
                ("memory.forget", STRANGER, {"target": ROW, "owner": STRANGER, "visibility": "private"}, door.UNKNOWN_PERSON)):
            with pytest.raises(Refused) as exc:
                call(verb, person, **kw)
            assert str(exc.value) == reason
        for words in ({"text": "words"}, {"excerpt": "words"}, {"tags": ["words"]}, {"reason": "words"}, {"name": "words"}):
            with pytest.raises(wire.WireError):
                call("memory.remember", OWNER, owner=OWNER, **words)              # no field for text, an excerpt, a tag, a reason
    finally:
        d.stop()
    assert b"kiosk-1" not in (state / "kernel" / "zoe-os.db").read_bytes()


# ---- a FORMER member's memory: nobody's to change (review round 4; ``door.Door.ask`` checks the owner by the roster) ------------
from test_roster_sync import BRAIN, built, state, write  # noqa: E402,F401 - the daemon build fixture and its roster file


def _ask(srv, verb: str, person: str, *, owner: str | None, visibility: str, via: str | None = None) -> str:
    """The daemon's own door op (the roster file re-read first, as on the wire): ``ok`` or the refusal code."""
    try:
        with srv.kernel.spine._lock:
            srv._op_door_ask(BRAIN, {"area": "memory", "verb": verb, "person": person, "target": ROW, "owner": owner,
                                     "visibility": visibility, "via": via})
        return "ok"
    except Refused as exc:
        return exc.wire


def test_a_memory_row_of_a_member_taken_off_the_roster_is_nobodys_to_change(built):
    """Review round 4 (#2707): the kernel keeps a member taken off people.json in its people map while a record names them (so
    it can be forgotten), ``decide`` checks only who ASKS, and the door checked the row's OWNER against that map. So an adult
    could still correct a former member's private row, and any member their household row. Through the real daemon build:
    every ask the table allowed while the member was on the roster is ``unknown_person`` once they are off it (remember,
    correct, approve; an adult on a private row, anyone on a household one), what it refused stays refused, and back on the
    roster their rows are changed as before.

    FORGETTING still reaches them. ``memory.forget`` is told AFTER production has erased (the kernel can neither delay nor
    stop an erase): for a former member's row the kernel's answer is the same ``unknown_person``, which for that verb only
    means the record says OBJECTED and names nobody but the asker; the erase stands. And the kernel's own whole-person forget
    of the former member still works.

    Control run: ``owner not in acting`` put back to ``owner not in people`` in ``door.Door.ask`` -> an adult's correction
    of the former member's private row is ``ok`` again and this fails."""
    daemon, k, srv, path = built
    everyone = [{"id": OWNER, "role": "adult"}, {"id": ADULT2, "role": "adult"}, {"id": KID, "role": "child", "guardians": [OWNER]},
                {"id": KID2, "role": "child", "guardians": [OWNER]}]
    asks = [(verb, who, visibility, via) for verb in VERBS for visibility in ("household", "private")
            for who, via in ((OWNER, None), (ADULT2, None), (KID2, None), ("kiosk-1", "panel"))]

    def answers() -> dict:
        return {a: _ask(srv, a[0], a[1], owner=KID, visibility=a[2], via=a[3]) for a in asks}

    write(path, everyone)
    assert _ask(srv, memory_door.REMEMBER, KID, owner=KID, visibility="private") == "ok"          # a record names them
    on_the_roster = answers()
    were_allowed = {a for a in asks if on_the_roster[a] == "ok"}
    assert were_allowed == {(verb, who, visibility, None) for verb in VERBS for who, visibility in (
        (OWNER, "private"), (ADULT2, "private"), (OWNER, "household"), (ADULT2, "household"), (KID2, "household"))}
    write(path, [person for person in everyone if person["id"] != KID])                           # KID taken off the file
    assert _ask(srv, memory_door.CORRECT, OWNER, owner=OWNER, visibility="private") == "ok"       # the file is re-read here
    assert KID in k.grants.people and KID not in srv.roster                                       # kept (forgettable), off it
    refusals = k.spine.refusals
    off_the_roster = answers()
    for a in asks:
        assert off_the_roster[a] == (door.UNKNOWN_PERSON if a in were_allowed else on_the_roster[a]), a
    assert "ok" not in off_the_roster.values() and k.spine.refusals == refusals + len(asks)      # refusals recorded, not errors
    assert off_the_roster[(memory_door.FORGET, OWNER, "private", None)] == door.UNKNOWN_PERSON    # told afterwards: an objection
    write(path, everyone)
    assert answers() == on_the_roster                                                             # back on the roster: as before
    write(path, [person for person in everyone if person["id"] != KID])
    assert _ask(srv, memory_door.CORRECT, ADULT2, owner=KID, visibility="private") == door.UNKNOWN_PERSON
    assert k.spine.forget(KID, actor=KID).count >= 1                                              # and they can still be forgotten
