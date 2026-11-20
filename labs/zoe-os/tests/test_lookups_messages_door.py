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
def test_the_record_holds_who_and_the_class_and_never_what(tmp_path):
    """Control run: with the lookups area keyed ``target="memory"`` the payload assertion still holds no target (there is
    none to send); with the messages area's recipient written under ``target`` the payload assertion fails."""
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


def test_the_daemon_serves_both_areas_and_has_no_field_for_a_query_an_address_or_the_words(tmp_path):
    """Control run: with the lookup and message verbs left out of the daemon's brain manifest every ask is refused."""
    from test_shadow_daemon import Shadow, settled
    from zoe_os import wire

    state = Path(tempfile.mkdtemp(prefix="zd"))
    state.chmod(0o700)
    settled(state)
    (state / "people.json").write_text(json.dumps({"people": [{"id": OWNER, "role": "adult"}, {"id": KID, "role": "child",
                                                                                                "guardians": [OWNER]}]}))
    d = Shadow(state)
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
    finally:
        d.stop()
