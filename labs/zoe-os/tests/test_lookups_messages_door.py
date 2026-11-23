"""The lookups and messages areas of the door (``zoe_os/lookups_door.py``, ``zoe_os/messages_door.py``): one test per row of
each table; the record (ids and closed codes only); the wire (no field for a query, a URL, an address or the words of a
message). Each test names the control that was run against it. Invented people and ids only.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from zoe_os import door, lookups_door, messages_door
from zoe_os.capability import ADULT, CHILD, GUEST, Person
from zoe_os.kernel import Kernel
from zoe_os.spine import Refused

OWNER, ADULT2, KID, KID2, VISITOR, STRANGER = "member-ova", "member-adu", "member-kip", "member-kop", "member-gus", "member-zzz"
PEOPLE = {p.id: p for p in (Person(OWNER, ADULT), Person(ADULT2, ADULT), Person(KID, CHILD, guardians=(OWNER,)),
                            Person(KID2, CHILD, guardians=(OWNER,)), Person(VISITOR, GUEST))}
ALLOW = lookups_door.Decision(True)
M = messages_door


def looked(verb, actor, **kw):
    return lookups_door.decide(PEOPLE, verb, actor, **kw)


def sent(verb, actor, **kw):
    return messages_door.decide(PEOPLE, verb, actor, **kw)


def refused(reason):
    return (False, reason, None)


# ---- lookups ---------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("verb", lookups_door.VERBS)
@pytest.mark.parametrize("who, via", [(OWNER, None), (KID, None), (VISITOR, None), ("kiosk-1", "panel")])
def test_anyone_in_the_house_and_the_panel_looks_things_up_a_child_too(verb, who, via):
    """There is no child rule for the web in production, so none here. Control run: with the table requiring an adult a
    child's lookup is an objection."""
    assert looked(verb, who, via=via) == ALLOW


@pytest.mark.parametrize("verb", lookups_door.VERBS)
def test_a_lookup_by_someone_not_on_the_roster_is_an_objection(verb):
    """Control run: with the roster check dropped a stranger's lookup is recorded under their id."""
    assert looked(verb, STRANGER) == refused(door.UNKNOWN_PERSON)
    assert lookups_door.VERBS == ("lookup.weather", "lookup.web_search", "lookup.web_fetch") == lookups_door.TOLD_VERBS


# ---- messages --------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("who, via", [(OWNER, None), (KID, None), (VISITOR, None), ("kiosk-1", "panel")])
def test_anyone_in_the_house_and_the_panel_may_send_to_the_houses_own_panels(who, via):
    """Control run: with ``send.panel`` falling to the adult rule a child's announcement is refused."""
    assert sent(M.PANEL_SEND, who, via=via) == ALLOW


@pytest.mark.parametrize("verb", M.VERBS)
@pytest.mark.parametrize("recipient", [OWNER, KID, None])
def test_an_adult_may_send_on_every_channel(verb, recipient):
    """Control run: with the adult rule dropped an adult's message to a child's phone is ``adults_only``."""
    assert sent(verb, ADULT2, owner=recipient) == ALLOW


@pytest.mark.parametrize("who", [KID, VISITOR])
def test_a_child_or_a_guest_sends_only_to_themself_and_never_to_an_outside_address(who):
    """Control run: with the own-phone rule answering for any recipient a child messages an adult's phone."""
    for verb in (M.TELEGRAM, M.PUSH):
        assert sent(verb, who, owner=who) == ALLOW
        assert sent(verb, who, owner=OWNER) == refused(door.ADULTS_ONLY)
        assert sent(verb, who) == refused(door.ADULTS_ONLY)
    assert sent(M.EXTERNAL, who) == refused(door.ADULTS_ONLY) and sent(M.EXTERNAL, who, owner=who) == refused(door.ADULTS_ONLY)


@pytest.mark.parametrize("verb", [M.TELEGRAM, M.PUSH, M.EXTERNAL])
def test_the_panel_with_nobody_identified_sends_nothing_off_the_panels(verb):
    """Control run: with the panel rule dropped the bare panel sends to an outside address."""
    assert sent(verb, "kiosk-1", via="panel") == refused(door.NOT_OWNER)
    assert sent(verb, "kiosk-1", via="panel", owner=OWNER) == refused(door.NOT_OWNER)


@pytest.mark.parametrize("verb", M.VERBS)
def test_someone_not_on_the_roster_sends_nothing(verb):
    """Control run: with the roster check dropped a stranger announces on the panels."""
    assert sent(verb, STRANGER) == refused(door.UNKNOWN_PERSON)
    assert M.VERBS == ("send.panel", "send.telegram", "send.push", "send.external")


# ---- the record, and the wire --------------------------------------------------------------------------------------------------------
QUERY, ADDRESS = "womble-weather", "person@example.com"       # a query and an address, both id-shaped: the shared id check passes them
#: (area, verb, the fields this area does not have). Lookups: no target, no owner, no turn. Messages: no target, no turn (the
#: recipient is ``owner``, and only a member on the roster is ever written).
NO_FIELD = (("lookups", "lookup.web_search", ("target", "owner", "turn")),
            ("lookups", "lookup.weather", ("target", "owner", "turn")),
            ("messages", "send.external", ("target", "turn")),
            ("messages", "send.telegram", ("target", "turn")))


def _counts(k) -> tuple:
    db = k.spine._db
    return tuple(db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("events", "intents")) + (k.spine.refusals,)


def _files_hold(root: Path, *words: str) -> list:
    """Every file under ``root`` (the database, its journal and write-ahead files, any log) that holds one of ``words``."""
    return [(f.name, w) for f in root.rglob("*") if f.is_file() for w in words if w.encode() in f.read_bytes()]


def test_the_record_holds_who_and_the_class_and_never_what(tmp_path):
    """Control run: with the messages area's recipient written under ``target`` the payload assertion fails."""
    k = Kernel(str(tmp_path / "k.db"), list(PEOPLE.values()))
    k.grants.install({"skill": "door", "acts": [*lookups_door.VERBS, *M.VERBS]})
    a = k.door.ask("door", "lookups", "lookup.web_search", KID, visibility="household")
    assert k.spine._get(a["seq"]).payload == {"action": "lookup.web_search", "skill": "door", "grant_id": None, "proof": None,
                                              "verified": False, "outcome": "allowed", "reversible": False, "vis": "household"}
    b = k.door.ask("door", "messages", "send.telegram", KID, owner=KID)
    assert k.spine._get(b["seq"]).payload == {"action": "send.telegram", "skill": "door", "grant_id": None, "proof": None,
                                              "verified": False, "outcome": "allowed", "reversible": False, "vis": "private", "owner": KID}
    for area, verb in (("lookups", "send.panel"), ("messages", "lookup.weather"), ("memory", "lookup.web_fetch")):
        with pytest.raises(ValueError):
            k.door.ask("door", area, verb, OWNER)
    with pytest.raises(Refused) as exc:
        k.door.ask("door", "messages", "send.telegram", OWNER, owner=STRANGER)        # a recipient the kernel does not know
    assert exc.value.wire == door.UNKNOWN_PERSON
    k.close()
    assert STRANGER.encode() not in (tmp_path / "k.db").read_bytes()


@pytest.mark.parametrize("area, verb, fields", NO_FIELD)
@pytest.mark.parametrize("who, via", [(OWNER, None), (KID, None), (STRANGER, None), ("kiosk-1", "panel")])
def test_a_query_or_an_address_sent_in_a_field_the_area_does_not_have_is_rejected_and_stored_nowhere(tmp_path, area, verb,
                                                                                                    fields, who, via):
    """A lookup records who and the class; a message the channel and a known recipient. A caller that puts a query or an
    address in ``target`` (or, for these areas, ``owner`` / ``turn``) is REJECTED before anything is read or written: no
    receipt, no intent, no refusal event, and neither word in any file the kernel keeps. Allowed, refused and panel callers alike.
    Controls run: with ``Door.ask``'s no-target check dropped the target ask is ALLOWED and ``womble-weather`` is in the receipt
    (``{"lookup": ...}`` / ``{"message": ...}``, the old ``target="lookup"`` / ``"message"`` keys); with the lookups area's
    ``turn=False`` dropped the turn ask records ``turn``; with its ``owner=False`` dropped an owner ask is a refusal
    (the address is not written, but it is not rejected either, so the "nothing recorded" count fails)."""
    k = Kernel(str(tmp_path / "k.db"), list(PEOPLE.values()))
    k.grants.install({"skill": "door", "acts": [*lookups_door.VERBS, *M.VERBS]})
    for field in fields:
        for word in (QUERY, ADDRESS):
            before = _counts(k)
            with pytest.raises(ValueError):
                k.door.ask("door", area, verb, who, via=via, visibility="household", **{field: word})
            assert _counts(k) == before, (field, word)
    k.close()
    assert _files_hold(tmp_path, QUERY, ADDRESS) == []


def test_the_other_areas_still_take_a_target_owner_and_turn(tmp_path):
    """The shared wire is unchanged for an area that HAS a target: a reminder ask keeps its id, its owner and its turn.
    Control run: with the no-target check written as ``target is not None`` (every area) this ask is rejected."""
    k = Kernel(str(tmp_path / "k.db"), list(PEOPLE.values()))
    k.grants.install({"skill": "door", "acts": ["reminder.edit"]})
    a = k.door.ask("door", "reminders", "reminder.edit", OWNER, target="demo_rem_0001", owner=OWNER, turn="turn-0001")
    payload = k.spine._get(a["seq"]).payload
    assert (payload["reminder"], payload["owner"], payload["turn"]) == ("demo_rem_0001", OWNER, "turn-0001")
    assert (lookups_door.AREA.target, messages_door.AREA.target) == (None, None)
    k.close()


def test_the_daemon_serves_both_areas_and_has_no_field_for_a_query_an_address_or_the_words(tmp_path):
    """Over the real socket: ``target`` (and ``turn``; a lookup's ``owner``) is a malformed request for these two areas, and
    after the daemon stops neither the query nor the address is in any file of its state directory, nor in its own output.
    Controls run: with the lookup and message verbs left out of the daemon's brain manifest every ask is refused; with
    ``Door.ask``'s no-target check dropped the ``target`` ask is answered with an ask id and the state directory holds the query."""
    from test_shadow_daemon import Shadow, settled
    from zoe_os import wire

    state = Path(tempfile.mkdtemp(prefix="zd"))
    state.chmod(0o700)
    settled(state)
    (state / "people.json").write_text(json.dumps({"people": [{"id": OWNER, "role": "adult"}, {"id": KID, "role": "child",
                                                                                                "guardians": [OWNER]}]}))
    d = Shadow(state)
    err = ""
    try:
        def call(area, verb, person, **kw):
            return d.brain.call("door_ask", area=area, verb=verb, person=person, **kw)

        assert call("lookups", "lookup.weather", KID, visibility="household")["ask"]
        assert call("lookups", "lookup.web_fetch", "kiosk-1", via="panel", visibility="household")["ask"]
        assert call("messages", "send.panel", KID)["ask"] and call("messages", "send.telegram", KID, owner=KID)["ask"]
        for area, verb, person, kw, reason in (("lookups", "lookup.web_search", STRANGER, {}, door.UNKNOWN_PERSON),
                                               ("messages", "send.external", KID, {}, door.ADULTS_ONLY),
                                               ("messages", "send.telegram", KID, {"owner": OWNER}, door.ADULTS_ONLY)):
            with pytest.raises(Refused) as exc:
                call(area, verb, person, **kw)
            assert str(exc.value) == reason
        for words in ({"query": "words"}, {"url": "https://example.invalid"}, {"text": "words"}, {"address": "words"}, {"to": "words"}):
            with pytest.raises(wire.WireError):
                call("lookups", "lookup.web_search", OWNER, **words)
            with pytest.raises(wire.WireError):
                call("messages", "send.external", OWNER, **words)
        for area, verb, fields in NO_FIELD:
            for field in fields:
                for word in (QUERY, ADDRESS):
                    with pytest.raises(wire.WireError) as bad:
                        call(area, verb, OWNER, visibility="household", **{field: word})
                    assert bad.value.code == wire.BAD_REQUEST, (area, field)
    finally:
        err = d.stop()
    assert _files_hold(state, QUERY, ADDRESS) == []
    assert QUERY not in err and ADDRESS not in err
