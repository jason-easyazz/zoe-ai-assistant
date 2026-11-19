"""The lists door (``zoe_os/lists.py``): the kernel decides and records a list change by ids; it holds no list.

One test per row of the permission table, the growth bound (rows per act do not depend on how long the list is, and nothing is
written to ``snapshot_dependencies``), restart recovery of an ask that was never reported, and the same verbs over the daemon's wire.
Invented people and ids only.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from zoe_os import lists
from zoe_os.capability import ADULT, CHILD, GUEST, Person
from zoe_os.events import Kind
from zoe_os.kernel import Kernel
from zoe_os.spine import Refused

OWNER, ADULT2, KID, VISITOR, STRANGER = "member-ova", "member-adu", "member-kip", "member-gus", "member-zzz"
DOOR = {"skill": "door", "acts": list(lists.VERBS)}
PEOPLE = {p.id: p for p in (Person(OWNER, ADULT), Person(ADULT2, ADULT), Person(KID, CHILD, guardians=(OWNER,)),
                            Person(VISITOR, GUEST))}


def allowed(verb, actor, **kw):
    return lists.decide(PEOPLE, verb, actor, **kw)


# ---- the permission table, one rule each ---------------------------------------------------------------------------------------
@pytest.mark.parametrize("verb", lists.VERBS)
def test_a_private_list_is_its_owners_for_every_verb(verb):
    assert allowed(verb, OWNER, owner=OWNER, visibility="private").allowed


@pytest.mark.parametrize("verb", lists.VERBS)
@pytest.mark.parametrize("actor", [ADULT2, KID, VISITOR])
def test_a_private_list_refuses_everyone_else_even_an_adult(verb, actor):
    assert allowed(verb, actor, owner=OWNER, visibility="private") == (False, lists.NOT_OWNER, None)


@pytest.mark.parametrize("verb", lists.VERBS)
def test_a_private_list_refuses_the_shared_panel(verb):
    assert allowed(verb, "anything", via="panel", owner=OWNER, visibility="private").reason == lists.NOT_OWNER


@pytest.mark.parametrize("verb", lists.ITEM_VERBS)
@pytest.mark.parametrize("actor", [OWNER, ADULT2, KID, VISITOR])
def test_any_member_may_change_the_items_of_a_household_list(verb, actor):
    assert allowed(verb, actor, owner=OWNER, visibility="household").allowed


@pytest.mark.parametrize("verb", lists.ITEM_VERBS)
def test_the_shared_panel_may_change_the_items_of_a_household_list(verb):
    assert allowed(verb, "kiosk", via="panel", owner=OWNER, visibility="household").allowed


@pytest.mark.parametrize("verb", lists.LIST_VERBS)
def test_an_adult_may_rename_delete_or_share_a_household_list_they_do_not_own(verb):
    assert allowed(verb, ADULT2, owner=OWNER, visibility="household").allowed


@pytest.mark.parametrize("verb", lists.LIST_VERBS)
@pytest.mark.parametrize("actor", [KID, VISITOR])
def test_a_child_or_guest_may_not_rename_delete_or_share_a_household_list(verb, actor):
    assert allowed(verb, actor, owner=OWNER, visibility="household").reason == lists.ADULTS_ONLY


@pytest.mark.parametrize("verb", lists.LIST_VERBS)
def test_the_shared_panel_may_not_rename_delete_or_share_a_household_list(verb):
    assert allowed(verb, "kiosk", via="panel", owner=OWNER, visibility="household").reason == lists.ADULTS_ONLY


@pytest.mark.parametrize("verb", lists.VERBS)
def test_a_child_is_allowed_on_their_own_lists_private_or_household(verb):
    assert allowed(verb, KID, owner=KID, visibility="private").allowed
    assert allowed(verb, KID, owner=KID, visibility="household").allowed


@pytest.mark.parametrize("verb", lists.VERBS)
@pytest.mark.parametrize("visibility", ["private", "household"])
def test_someone_not_on_the_roster_is_refused_everything(verb, visibility):
    assert allowed(verb, STRANGER, owner=STRANGER, visibility=visibility).reason == lists.UNKNOWN_PERSON


def test_a_member_may_start_a_household_list_and_only_their_own_private_one():
    assert allowed("list.create", KID, owner="house", visibility="household").allowed
    assert allowed("list.create", "kiosk", via="panel", owner="house", visibility="household").allowed
    assert allowed("list.create", ADULT2, owner=OWNER, visibility="private").reason == lists.NOT_OWNER


def test_a_list_whose_owner_or_visibility_was_not_told_is_treated_as_someone_elses_private_list():
    assert allowed("item.add", OWNER).reason == lists.NOT_OWNER


# ---- the record ----------------------------------------------------------------------------------------------------------------
def kernel(tmp_path, acts=lists.VERBS):
    k = Kernel(str(tmp_path / "k.db"), list(PEOPLE.values()))
    k.grants.install({"skill": "door", "acts": list(acts)})
    return k


def rows(tmp_path) -> dict:
    con = sqlite3.connect(str(tmp_path / "k.db"))
    try:
        return {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")}
    finally:
        con.close()


def test_an_allowed_ask_records_ids_only_and_the_report_closes_it(tmp_path):
    k = kernel(tmp_path)
    out = k.lists.ask("door", "item.add", KID, list_id="list-qov", items=["item-1"], owner=OWNER, visibility="household", turn="turn-7")
    asked = k.spine._get(out["seq"])
    assert asked.kind is Kind.ACTION_RECEIPT and asked.subject == KID
    assert asked.payload == {"action": "item.add", "skill": "door", "grant_id": None, "proof": None, "verified": False,
                             "outcome": "allowed", "reversible": False, "vis": "household", "list": "list-qov",
                             "items": ["item-1"], "owner": OWNER, "turn": "turn-7"}
    done = k.lists.report("door", out["ask"], "done")
    closed = k.spine._get(done["seq"])
    assert closed.payload["outcome"] == "done" and closed.payload["verified"] is False and closed.caused_by == (out["seq"],)
    with pytest.raises(ValueError):
        k.lists.report("door", out["ask"], "done")               # one report per ask
    k.close()


def test_a_refusal_is_recorded_with_codes_and_never_an_unknown_id(tmp_path):
    k = kernel(tmp_path)
    for person, reason in ((STRANGER, lists.UNKNOWN_PERSON), (ADULT2, lists.NOT_OWNER)):
        with pytest.raises(Refused) as exc:
            k.lists.ask("door", "item.add", person, list_id="list-qov", owner=OWNER, visibility="private")
        assert exc.value.wire == reason
    k.close()
    con = sqlite3.connect(str(tmp_path / "k.db"))
    stored = con.execute("SELECT subject, payload FROM events WHERE kind = 'refusal'").fetchall()
    assert [json.loads(p)["reason"] for _, p in stored] == [lists.UNKNOWN_PERSON, lists.NOT_OWNER]
    assert [s for s, _ in stored] == [None, ADULT2] and STRANGER not in json.dumps(stored)
    assert con.execute("SELECT COUNT(*) FROM intents").fetchone()[0] == 0     # a refused ask opens nothing


def test_a_skill_whose_manifest_does_not_name_the_verb_is_refused(tmp_path):
    k = kernel(tmp_path, acts=["item.add"])
    k.lists.ask("door", "item.add", OWNER, list_id="list-qov", owner=OWNER, visibility="private")
    with pytest.raises(Refused) as exc:
        k.lists.ask("door", "list.delete", OWNER, list_id="list-qov", owner=OWNER, visibility="private")
    assert exc.value.wire == lists.NOT_IN_MANIFEST
    k.close()


@pytest.mark.parametrize("bad", [{"verb": "list.burn"}, {"visibility": "public"}, {"via": "phone"}, {"list_id": "a list name"},
                                 {"items": ["x"] * 17}, {"items": ["two words"]}])
def test_a_malformed_ask_is_not_a_decision(tmp_path, bad):
    k = kernel(tmp_path)
    args = {"verb": "item.add", "list_id": "list-qov", "owner": OWNER, "visibility": "household", **bad}
    with pytest.raises(ValueError):
        k.lists.ask("door", args.pop("verb"), OWNER, **args)
    k.close()


def test_an_ask_never_reported_is_recovered_as_uncertain_after_a_restart(tmp_path):
    k = kernel(tmp_path)
    out = k.lists.ask("door", "item.remove", OWNER, list_id="list-qov", items=["item-9"], owner=OWNER, visibility="private")
    k.close()                                                    # no report: the kernel (or the caller) stopped in between
    k = kernel(tmp_path)
    assert [(e.payload["action"], e.payload["outcome"], e.subject, e.caused_by) for e in k.recovered] == \
        [("item.remove", "uncertain", OWNER, (out["seq"],))]
    with pytest.raises(ValueError):
        k.lists.report("door", out["ask"], "done")               # closed as uncertain: a late report cannot rewrite it
    k.close()
    assert kernel(tmp_path).recovered == []                      # once


def acts(tmp_path, n: int) -> dict:
    k = kernel(tmp_path)
    for i in range(n):
        out = k.lists.ask("door", "item.add", ADULT2, list_id="list-qov", items=[f"item-{i}"], owner=OWNER, visibility="household")
        k.lists.report("door", out["ask"], "done")
    k.close()
    return rows(tmp_path)


def test_four_hundred_adds_to_one_list_leave_rows_in_proportion_and_no_snapshot_rows(tmp_path):
    """The fault this replaces: 362 adds to one list left 33,330 ``snapshot_dependencies`` rows (each add indexed every earlier one)."""
    (tmp_path / "a").mkdir(), (tmp_path / "b").mkdir()
    small, big = acts(tmp_path / "a", 400), acts(tmp_path / "b", 800)
    assert small["snapshot_dependencies"] == 0 and big["snapshot_dependencies"] == 0 and small["opaque_snapshots"] == 0
    assert sum(small.values()) < 12 * 400 and sum(big.values()) < 12 * 800
    assert abs(sum(big.values()) - 2 * sum(small.values())) <= 16      # double the acts, double the rows (plus fixed rows)
    assert all(big[t] <= 2 * small[t] + 8 for t in big)                 # in EVERY table


def test_the_old_list_add_action_is_the_control_its_rows_grow_with_the_list(tmp_path):
    """Negative control for the growth test: the snapshotting ``list.add`` action does write a row per earlier item."""
    from zoe_os.brain.drivers import BRAIN, Lists
    k = Kernel(str(tmp_path / "k.db"), list(PEOPLE.values()))
    Lists().register(k)
    for m in ({"skill": "voice", "emits": ["utterance"], "origin": True}, BRAIN):
        k.grants.install(m)
    k.grants.grant(OWNER, "brain")
    for i in range(20):
        said = k.spine.publish(__import__("zoe_os.events", fromlist=["Event"]).Event("utterance", "voice", OWNER, payload={"text": f"w{i}"}))
        k.actions.act("brain", "list.add", OWNER, {"item": f"w{i}", "asked": said.seq}, caused_by=(said.seq,))
    k.close()
    assert rows(tmp_path)["snapshot_dependencies"] == sum(range(20))


# ---- the same verbs on the daemon's wire, with the credentials it already issues -------------------------------------------------
def test_the_daemon_serves_ask_and_report_on_its_existing_socket_and_recovers_an_unreported_ask(tmp_path):
    import tempfile
    from pathlib import Path

    from test_shadow_daemon import Shadow, settled
    from zoe_os import wire

    state = Path(tempfile.mkdtemp(prefix="zd"))                     # short: a socket path is <= 107 bytes
    state.chmod(0o700)
    settled(state)
    (state / "people.json").write_text(json.dumps({"people": [{"id": OWNER, "role": "adult"}, {"id": KID, "role": "child",
                                                                                                "guardians": [OWNER]}, VISITOR]}))
    d = Shadow(state)
    try:
        out = d.brain.call("list_ask", verb="item.tick", person=KID, list="list-qov", items=["item-3"], owner=OWNER,
                           visibility="household", turn="turn-1")
        assert d.brain.call("list_report", ask=out["ask"], outcome="done")["outcome"] == "done"
        for person, kw, reason in ((KID, {"verb": "list.delete", "visibility": "household"}, lists.ADULTS_ONLY),
                                   (VISITOR, {"verb": "item.add", "visibility": "private"}, lists.NOT_OWNER),   # a plain id = least
                                   (STRANGER, {"verb": "item.add", "visibility": "household"}, lists.UNKNOWN_PERSON)):
            with pytest.raises(Refused) as exc:
                d.brain.call("list_ask", person=person, list="list-qov", owner=OWNER, **kw)
            assert str(exc.value) == reason
        assert d.brain.call("list_ask", verb="item.add", person="kiosk-1", via="panel", list="list-qov", owner=OWNER,
                            visibility="household")["ask"]
        with pytest.raises(Refused, match=lists.NOT_IN_MANIFEST):
            d.voice.call("list_ask", verb="item.add", person=OWNER, list="list-qov", owner=OWNER, visibility="private")
        with pytest.raises(wire.WireError):
            d.brain.call("list_ask", verb="item.add", person=OWNER, list="list-qov", text="words")     # no field for text
        left = d.brain.call("list_ask", verb="item.add", person=OWNER, list="list-qov", owner=OWNER, visibility="private")
        assert d.add(OWNER, "synthetic words")["verified"] is True   # the shadow's own verbs still work beside it
    finally:
        d.stop()
    d = Shadow(state)                                               # killed with two asks open (the panel's and the owner's)
    try:
        with pytest.raises(wire.WireError):
            d.brain.call("list_report", ask=left["ask"], outcome="done")
    finally:
        d.stop()
    con = sqlite3.connect(str(state / "kernel" / "zoe-os.db"))
    assert con.execute("SELECT COUNT(*) FROM events WHERE kind = 'action_receipt' AND payload LIKE '%uncertain%'").fetchone()[0] == 2
    assert b"kiosk-1" not in (state / "kernel" / "zoe-os.db").read_bytes()      # the panel's device id is not kept
