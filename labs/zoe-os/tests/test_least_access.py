"""Least access by default: a person with no stated role, an unknown role, or a roster entry the loader cannot fully read gets the
LEAST access there is (``capability.LEAST``): no grant (so no consent, for themself or anyone), no household or kernel reads, acted
for nowhere. Nothing defaults to adult. The shadow daemon still RECORDS such a member's adds (it only watches). Synthetic only.
"""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import pytest

from zoe_os.capability import ADULT, GUEST, LEAST, ConsentError, Enforcer, GrantStore, Person
from zoe_os.events import Event, Kind, Visibility

AUDIT = {"skill": "audit", "reads": {"presence": "household", "refusal": "kernel"}}
BRAIN = {"skill": "brain", "acts": ["list.add"]}


@pytest.mark.parametrize("who", [Person("member-u"), Person("member-u", "wizard"), Person("member-u", ""),
                                 Person("member-u", "Adult")])
def test_unlabelled_or_unknown_principal_gets_least_access(who):
    assert LEAST == GUEST and who.role == LEAST
    store = GrantStore([who, Person("member-a", ADULT)])
    for m in (AUDIT, BRAIN):
        store.install(m)
    with pytest.raises(ConsentError):
        store.grant(who.id, "brain")                                    # cannot consent, so is acted for nowhere
    with pytest.raises(ConsentError):
        store.grant(who.id, "audit")
    enforcer = Enforcer(store)
    assert not enforcer.may_act("brain", "list.add", who.id).allowed
    store.grant("member-a", "audit")
    shared = Event(Kind.PRESENCE, "presence", "member-a", Visibility.HOUSEHOLD, {"state": "home", "confidence": 0.9})
    assert enforcer.may_read("audit", shared).allowed                    # an adult's grant opens a household event ...
    store.revoke("member-a", "audit")
    with pytest.raises(ConsentError):
        store.grant(who.id, "audit", reads={"presence": "household"})   # ... the least-access member can open nothing


def test_a_record_only_grant_is_refused_anywhere_but_a_shadow_store():
    store = GrantStore([Person("member-u")])
    store.install(BRAIN)
    with pytest.raises(ConsentError, match="only a shadow"):
        store.grant("member-u", "brain", record_only=True)
    store.shadow = True
    assert store.grant("member-u", "brain", record_only=True).acts == frozenset({"list.add"})


@pytest.mark.parametrize("entry, role", [
    ("member-x", LEAST),                                                        # a plain id: unstated
    ({"id": "member-x"}, LEAST),                                                # no role
    ({"id": "member-x", "role": "superuser"}, LEAST),                           # an unknown role
    ({"id": "member-x", "role": "adult", "colour": "blue"}, LEAST),             # an unknown key: not fully understood
    ({"id": "member-x", "role": "adult", "guardians": "member-y"}, LEAST),      # guardians not a list
    ({"id": "member-x", "role": "adult"}, ADULT),                               # stated: an adult
])
def test_every_roster_loader_gives_least_access_unless_the_entry_fully_says_otherwise(entry, role):
    from shadow_daemon import roster_people
    (person,) = roster_people([entry])
    assert person.id == "member-x" and person.role == role


def test_the_shadow_still_records_a_least_access_members_add():
    import shadow_daemon
    state = Path(tempfile.mkdtemp(prefix="zl"))
    state.chmod(0o700)
    (state / "people.json").write_text(json.dumps({"people": ["member-u", {"id": "member-a", "role": "adult"}]}))
    k, srv, _creds = shadow_daemon.build(state)
    try:
        assert k.grants.people["member-u"].role == LEAST and k.grants.people["member-a"].role == ADULT
        said = k.spine.publish(Event(Kind.UTTERANCE, "voice", "member-u", Visibility.PRIVATE, {"text": "synthetic words"}))
        receipt = k.actions.act("brain", "list.add", "member-u", {"item": "synthetic item", "asked": said.seq},
                                caused_by=(said.seq,))
        assert receipt.verified                                          # recorded for comparison: the shadow only watches
    finally:
        srv.shutdown()
        k.close()
        shutil.rmtree(state, ignore_errors=True)


@pytest.mark.parametrize("load", ["replay", "twin"])
def test_the_replay_and_twin_loaders_give_least_access_to_an_entry_they_cannot_fully_read(load):
    bad = {"id": "member-u", "role": "adult", "guardians": "member-a", "unrecognised": True}
    if load == "replay":
        from zoe_os.replay import Recording
        rec = Recording.loads(json.dumps({"recording": __import__("zoe_os.replay", fromlist=["FORMAT"]).FORMAT,
                                          "people": [bad, {"id": "member-a", "role": "adult"}]}))
        people = {p.id: p for p in rec.people}
    else:
        from zoe_os.twin.days import household_from
        people = {m.id: m for m in household_from({"members": [bad, {"id": "member-a", "role": "adult"}]}).members}
    assert people["member-u"].role == LEAST and tuple(people["member-u"].guardians) == ()
    assert people["member-a"].role == ADULT
