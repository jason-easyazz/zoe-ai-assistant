"""The lists door ordered against a forget, in the kernel (``zoe_os/lists.py``, "ORDER AGAINST A FORGET").

The caller's asks and reports run on their own connection, apart from the one its forgets use, so a list ask can reach the kernel
AFTER a later forget of a member it names. Every ask carries the caller's erase stamp ``[boot, generation]`` for each member it
names, and a whole-person forget carries the same stamp, kept as that person's erase mark. These tests drive the REAL kernel:

* an ask made before the forget (an older stamp) that arrives after it writes nothing that names anyone;
* a report after a forget writes no receipt that names the forgotten person, whether or not it carries stamps;
* an equal or newer stamp is recorded as before (a forgotten member who acts again is recorded afresh);
* the mark survives a kernel restart, is kept at the newest stamp, and never holds the member's id in clear;
* no stamp (an older caller): an ask for a member who has a mark is not recorded; a forget without one sets no mark.

Each test names the control that was run against it (the code broken, the test seen to fail, the code restored).
Invented people and ids only.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from zoe_os import lists
from zoe_os.capability import ADULT, CHILD, Person
from zoe_os.kernel import Kernel
from zoe_os.spine import Refused

OWNER, ADULT2, KID = "member-ova", "member-adu", "member-kip"
OLD, AT, NEW = [7, 2], [7, 3], [7, 4]                     # the stamp read before the forget, the forget's own, one read after
LATER_BOOT = [8, 0]                                       # the caller restarted: its generation starts again, its boot is newer


def kernel(tmp_path):
    k = Kernel(str(tmp_path / "k.db"), [Person(OWNER, ADULT), Person(ADULT2, ADULT), Person(KID, CHILD, guardians=(OWNER,))])
    k.grants.install({"skill": "door", "acts": list(lists.VERBS)})
    return k


def ask(k, actor, owner=OWNER, verb="item.add", stamps=None, visibility="household"):
    return k.lists.ask("door", verb, actor, list_id="list-qov", items=["item-1"], owner=owner, visibility=visibility, stamps=stamps)


def naming(tmp_path, member) -> dict:
    """Every table of the closed database -> how many of its rows hold ``member`` in clear, in any column."""
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


def in_file(tmp_path, member) -> list:
    return sorted(f.name for f in tmp_path.iterdir() if member.encode() in f.read_bytes())


# ---- race 1: the ask is recorded AFTER the forget -------------------------------------------------------------------------------
@pytest.mark.parametrize("actor, forgotten", [(OWNER, OWNER), (ADULT2, OWNER), (KID, KID)])
def test_an_ask_made_before_a_forget_that_arrives_after_it_writes_nothing_that_names_anyone(tmp_path, actor, forgotten):
    """The acting person OR the list's owner was forgotten after the caller read their stamp. Control run: with ``stale`` forced
    False in ``ListDoor.ask`` all three cases fail (the ask is recorded: a receipt and an open intent name the member); with the
    owner left out of the stale check the (member-adu, member-ova) case fails."""
    k = kernel(tmp_path)
    stamps = {m: OLD for m in {actor, OWNER}}
    assert ask(k, actor, stamps=stamps)["ask"]                    # before the forget: recorded
    k.spine.forget(forgotten, actor=forgotten, stamp=AT)
    before = k.spine.last_seq
    with pytest.raises(Refused) as exc:
        ask(k, actor, stamps=stamps)                              # the late one: the same stamps, read before the forget
    assert exc.value.wire == lists.ERASED
    late = k.spine._get(k.spine.last_seq)
    assert k.spine.last_seq == before + 1 and late.subject is None                # one bare refusal event and nothing else
    assert late.payload == {"who": "door", "attempted": "act item.add", "reason": "erased"}
    assert k.spine._db.execute("SELECT COUNT(*) FROM intents").fetchone()[0] == 0  # nothing open: a restart writes no receipt
    k.close()
    assert naming(tmp_path, forgotten) == {} and in_file(tmp_path, forgotten) == []
    k = kernel(tmp_path)
    assert k.recovered == []
    k.close()


def test_a_late_ask_the_kernel_would_refuse_keeps_its_reason_and_names_nobody(tmp_path):
    """A refusal stays a refusal when the ask raced a forget (a child may not delete a household list because its owner was being
    erased). Control run: with the stale check placed BEFORE the permission table this fails with ``erased``; with the refusal
    recorded under the acting person it fails on the subject."""
    k = kernel(tmp_path)
    k.spine.forget(OWNER, actor=OWNER, stamp=AT)
    with pytest.raises(Refused) as exc:
        ask(k, KID, verb="list.delete", stamps={KID: OLD, OWNER: OLD})
    refusal = k.spine._get(k.spine.last_seq)
    assert exc.value.wire == lists.ADULTS_ONLY and refusal.subject is None and "list" not in refusal.payload
    k.close()
    assert naming(tmp_path, OWNER) == {}


# ---- an equal or newer stamp is the member acting again ---------------------------------------------------------------------------
@pytest.mark.parametrize("stamp", [AT, NEW, LATER_BOOT])
def test_an_ask_stamped_at_or_after_the_forget_is_recorded_afresh(tmp_path, stamp):
    """Control run: with ``<`` turned into ``<=`` in ``Spine.erased_after`` the equal-stamp case fails (refused ``erased``); with
    the pair compared generation-first the later-boot case fails."""
    k = kernel(tmp_path)
    k.spine.forget(OWNER, actor=OWNER, stamp=AT)
    out = ask(k, OWNER, stamps={OWNER: stamp})
    assert k.spine._get(out["seq"]).subject == OWNER
    assert k.lists.report("door", out["ask"], "done", stamps={OWNER: stamp})["outcome"] == "done"
    k.close()


# ---- race 2: the report arrives after the forget ----------------------------------------------------------------------------------
@pytest.mark.parametrize("forgotten", [KID, OWNER])                # the acting person, then the list's owner
@pytest.mark.parametrize("stamped", [True, False])
def test_a_report_after_a_forget_writes_no_receipt_that_names_the_forgotten_person(tmp_path, forgotten, stamped):
    """The ask (a child on the owner's household list) was recorded, then one of the two was forgotten, then the queued report
    arrives. With stamps the report is refused ``erased``; without them (an older caller) there is no open ask left to close: the
    forget removed it with everything else that names the person. Either way nothing names them afterwards. Controls run: with
    the open-ask scrub by cause removed from ``Spine._scrub_intents`` both owner cases fail (the ask stays open: an unstamped
    report writes its receipt); with that AND the ``DELETE FROM intents WHERE person=?`` line of ``Spine.forget`` removed all four
    fail; with the stamp check removed from ``ListDoor.report`` the stamped cases fail (ValueError, not the ``erased`` refusal)."""
    k = kernel(tmp_path)
    stamps = {KID: OLD, OWNER: OLD}
    out = ask(k, KID, stamps=stamps if stamped else None)
    k.spine.forget(forgotten, actor=forgotten, stamp=AT if stamped else None)
    with pytest.raises(Refused if stamped else ValueError) as exc:
        k.lists.report("door", out["ask"], "done", stamps=stamps if stamped else None)
    assert not stamped or exc.value.wire == lists.ERASED
    assert k.spine._db.execute("SELECT COUNT(*) FROM intents").fetchone()[0] == 0
    k.close()
    assert naming(tmp_path, forgotten) == {} and in_file(tmp_path, forgotten) == []
    k = kernel(tmp_path)
    assert k.recovered == []                                      # and no "uncertain" receipt for it at the next start
    k.close()


def test_a_report_with_a_stale_stamp_is_refused_even_when_its_ask_is_still_open(tmp_path):
    """The report's own check, on its own: the ask names only the acting adult (a household list with no owner told), so a forget
    of someone else leaves it open; a report that carries a stale stamp for that someone is refused and the ask stays open.
    Control run: with the stamp check removed from ``ListDoor.report`` this fails (the report is written)."""
    k = kernel(tmp_path)
    out = ask(k, ADULT2, owner=None, stamps={ADULT2: OLD})
    k.spine.forget(KID, actor=KID, stamp=AT)
    with pytest.raises(Refused) as exc:
        k.lists.report("door", out["ask"], "done", stamps={ADULT2: OLD, KID: OLD})
    assert exc.value.wire == lists.ERASED
    assert k.spine._db.execute("SELECT COUNT(*) FROM intents WHERE receipt_seq IS NULL").fetchone()[0] == 1
    assert k.lists.report("door", out["ask"], "done", stamps={ADULT2: OLD})["outcome"] == "done"
    k.close()


# ---- the mark: durable, newest wins, names nobody ---------------------------------------------------------------------------------
def test_the_erase_mark_survives_a_restart_and_holds_no_member_id(tmp_path):
    """Control run: with the mark kept in a dict on the spine instead of the ``erase_stamps`` table the late ask after the restart
    is recorded and this fails; with the table keyed by the member id in clear the byte scan fails."""
    k = kernel(tmp_path)
    ask(k, OWNER, stamps={OWNER: OLD})
    assert in_file(tmp_path, OWNER)                               # the control of the scan: the id is in the file before the forget
    k.spine.forget(OWNER, actor=OWNER, stamp=AT)
    k.close()
    assert in_file(tmp_path, OWNER) == [] and naming(tmp_path, OWNER) == {}
    con = sqlite3.connect(str(tmp_path / "k.db"))
    marks = con.execute("SELECT owner, boot, gen FROM erase_stamps").fetchall()
    con.close()
    assert [(type(o), len(o), b, g) for o, b, g in marks] == [(bytes, 32, *AT)]
    k = kernel(tmp_path)                                          # the kernel restarted
    with pytest.raises(Refused) as exc:
        ask(k, OWNER, stamps={OWNER: OLD})
    assert exc.value.wire == lists.ERASED
    assert ask(k, OWNER, stamps={OWNER: NEW})["ask"]
    k.close()


def test_the_mark_keeps_the_newest_stamp_whatever_order_forgets_arrive_in(tmp_path):
    """A forget that carries an older stamp than the mark (a frame left over from a caller that has since restarted) still erases,
    and never moves the mark back. Control run: with the mark overwritten unconditionally the last ask is recorded and this fails."""
    k = kernel(tmp_path)
    k.spine.forget(OWNER, actor=OWNER, stamp=LATER_BOOT)
    k.spine.forget(OWNER, actor=OWNER, stamp=AT)                  # older: [7, 3] < [8, 0]
    with pytest.raises(Refused) as exc:
        ask(k, OWNER, stamps={OWNER: NEW})                        # [7, 4]: after the older forget, before the newer one
    assert exc.value.wire == lists.ERASED
    k.close()


# ---- no stamp: an older caller ----------------------------------------------------------------------------------------------------
def test_without_stamps_nothing_changes_until_a_stamped_forget_then_an_unstamped_ask_is_not_recorded(tmp_path):
    """An older caller sends no stamps at all: its forgets set no mark and its asks are recorded exactly as before. Once a member
    HAS a mark, an ask that gives no stamp for them cannot be ordered against it and is not recorded. Scoped forgets set no mark.
    Control run: with a missing stamp treated as newest in ``Spine.erased_after`` the last ask is recorded and this fails; with a
    mark written for a stampless forget the first ask after it is refused and this fails."""
    k = kernel(tmp_path)
    k.spine.forget(OWNER, actor=OWNER)                            # no stamp: no mark
    assert k.spine._get(ask(k, OWNER)["seq"]).subject == OWNER    # as before this change
    seq = ask(k, OWNER)["seq"]
    k.spine.forget(OWNER, actor=OWNER, seqs=[seq], stamp=NEW)     # a scoped forget: its stamp is ignored
    assert k.spine._db.execute("SELECT COUNT(*) FROM erase_stamps").fetchone()[0] == 0
    k.spine.forget(OWNER, actor=OWNER, stamp=AT)
    with pytest.raises(Refused) as exc:
        ask(k, OWNER)
    assert exc.value.wire == lists.ERASED
    assert ask(k, ADULT2, owner=None)["ask"]                      # a member with no mark is unaffected
    k.close()


@pytest.mark.parametrize("bad", [{"member-ova": [1]}, {"member-ova": [1, -1]}, {"member-ova": [1, True]}, {"member-ova": "7,2"},
                                 {"two words": [1, 2]}, {f"member-{i}": [1, 2] for i in range(5)}, [["member-ova", [1, 2]]]])
def test_malformed_stamps_are_not_a_decision(tmp_path, bad):
    """Control run: with ``_stamps`` returning the value unchecked these raise nothing (or a TypeError) and the test fails."""
    k = kernel(tmp_path)
    with pytest.raises(ValueError):
        ask(k, OWNER, stamps=bad)
    out = ask(k, OWNER)
    with pytest.raises(ValueError):
        k.lists.report("door", out["ask"], "done", stamps=bad)
    with pytest.raises(ValueError):
        k.spine.forget(OWNER, actor=OWNER, stamp=[1, 2, 3])
    assert k.spine._get(out["seq"]) is not None                   # the malformed forget erased nothing
    k.close()


# ---- the same on the daemon's wire ------------------------------------------------------------------------------------------------
def test_the_daemon_orders_asks_and_reports_against_a_forget_on_separate_connections(tmp_path):
    """The whole path production uses: the forget on the panel's connection, the ask and the report on the brain's, the daemon
    killed and started again in between. Control run: with ``stamp`` not passed on by ``KernelServer._op_forget`` the late ask is
    recorded and this fails; with ``stamps`` not passed on by ``_op_list_ask`` the newer ask is refused and this fails."""
    import tempfile
    from pathlib import Path

    from test_shadow_daemon import Shadow, holding, settled
    from zoe_os import wire

    state = Path(tempfile.mkdtemp(prefix="ze"))                   # short: a socket path is <= 107 bytes
    state.chmod(0o700)
    settled(state)
    (state / "people.json").write_text(json.dumps({"people": [{"id": OWNER, "role": "adult"}, {"id": KID, "role": "child",
                                                                                                "guardians": [OWNER]}]}))
    stamps = {KID: OLD, OWNER: OLD}
    args = {"verb": "item.tick", "person": KID, "list": "list-qov", "items": ["item-3"], "owner": OWNER, "visibility": "household"}
    d = Shadow(state)
    try:
        first = d.brain.call("list_ask", stamps=stamps, **args)
        assert d.panel.call("forget", subject=KID, actor=KID, stamp=AT)["count"] >= 1
        with pytest.raises(Refused, match=r"^erased$"):
            d.brain.call("list_report", ask=first["ask"], outcome="done", stamps=stamps)      # race 2
        with pytest.raises(Refused, match=r"^erased$"):
            d.brain.call("list_ask", stamps=stamps, **args)                                   # race 1
        with pytest.raises(wire.WireError):
            d.panel.call("forget", subject=KID, actor=KID, stamp=[1])                         # malformed: never a forget
    finally:
        d.stop()
    assert holding(state / "kernel", KID.encode()) == []          # nothing in the kernel's record names them
    d = Shadow(state)                                             # the kernel restarted: the mark is still there
    try:
        with pytest.raises(Refused, match=r"^erased$"):
            d.brain.call("list_ask", stamps=stamps, **args)
        again = d.brain.call("list_ask", stamps={KID: NEW, OWNER: OLD}, **args)               # the member acts again
        assert d.brain.call("list_report", ask=again["ask"], outcome="done", stamps={KID: NEW, OWNER: OLD})["outcome"] == "done"
    finally:
        d.stop()
