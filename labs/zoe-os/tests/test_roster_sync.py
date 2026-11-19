"""The roster follows ``people.json`` with NO restart (``shadow_daemon.ShadowServer.sync_roster`` / ``apply_roster``).

Before this the kernel read the roster once, at start: a member added to Zoe afterwards was refused ``unknown_person`` for every
list change (as actor, and as the owner of a list anyone else touched) until somebody rewrote the file AND restarted the kernel,
and a role stated later (an account made for someone known only as a plain id) never arrived.

Each test names the negative control that was run against it (the code broken, the test seen to fail, the code restored).
Synthetic members only. The wire tests run the real daemon as its own process on a temp state directory.
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import types
from pathlib import Path

import pytest

from test_shadow_daemon import Shadow, known, settled
from zoe_os import lists
from zoe_os.spine import Refused

OWNER, KID, NEW, PLAIN = "member-ova", "member-kip", "member-nyx", "member-pud"
BRAIN = types.SimpleNamespace(name="brain")


def write(state: Path, people: list) -> None:
    """The roster as zoe-data writes it: a new file, moved into place in one step."""
    tmp = state / "people.json.new"
    tmp.write_text(json.dumps({"people": people}))
    os.replace(tmp, state / "people.json")


@pytest.fixture
def state():
    path = Path(tempfile.mkdtemp(prefix="zr"))           # short: a socket path is <= 107 bytes
    path.chmod(0o700)
    settled(path)
    write(path, [{"id": OWNER, "role": "adult"}, {"id": KID, "role": "child", "guardians": [OWNER]}])
    running: list = []
    yield path, running
    for d in running:
        if d.proc.poll() is None:
            d.stop()


def start(st) -> Shadow:
    d = Shadow(st[0])
    st[1].append(d)
    return d


def ask(d: Shadow, person: str, verb: str = "item.add", *, owner: str = OWNER, visibility: str = "household") -> dict:
    return d.brain.call("list_ask", verb=verb, person=person, list="list-qov", owner=owner, visibility=visibility)


def refused(d: Shadow, person: str, verb: str = "item.add", **kw) -> str:
    with pytest.raises(Refused) as exc:
        ask(d, person, verb, **kw)
    return str(exc.value)


def test_a_member_added_after_the_start_is_known_without_a_restart_and_has_the_role_stated(state):
    """Over the daemon's wire. Before the file changes the new member is nobody: refused as the actor, and a list they OWN is
    refused to everyone. After it: known, an adult's verbs are theirs (the role stated), their own private list is theirs, and
    the watch-only ``list.add`` is recorded for them (the record-only grant every member has from the start).

    Negative control: ``self.sync_roster()`` removed from ``ShadowServer._op_list_ask`` -> the ask after the rewrite is still
    refused ``unknown_person`` (this test fails at the first ask after ``write``)."""
    d = start(state)
    assert refused(d, NEW) == lists.UNKNOWN_PERSON
    assert refused(d, OWNER, owner=NEW) == lists.UNKNOWN_PERSON             # their list: refused whoever asks
    write(state[0], [{"id": OWNER, "role": "adult"}, {"id": KID, "role": "child", "guardians": [OWNER]},
                     {"id": NEW, "role": "adult"}])
    assert ask(d, NEW)["ask"]                                               # the SAME daemon, no restart
    assert ask(d, NEW, "list.delete")["ask"]                                # an adult, as stated: not least access
    assert ask(d, NEW, owner=NEW, visibility="private")["ask"]
    assert ask(d, OWNER, owner=NEW)["ask"]                                  # and a list they own is a known owner's
    assert known(state[0]) == sorted([OWNER, KID, NEW])                     # forgettable from now on
    assert d.proc.poll() is None


def test_only_the_lists_door_re_reads_the_file_the_watch_only_ops_keep_the_roster_as_loaded(built, monkeypatch):
    """The roster follows the file where the kernel DECIDES: ``list_ask`` and ``list_report``. ``publish`` and ``act`` (watch-only)
    behave as before the door existed: no ``stat``, and a file edit does not reach them, so with lists off nothing changes.

    Negative control: ``self.sync_roster()`` put back into ``_op_publish`` / ``_op_act`` -> the count after the watch-only ops is
    not 0."""
    daemon, k, srv, state = built
    looked = []
    real = srv.sync_roster
    monkeypatch.setattr(srv, "sync_roster", lambda: looked.append(1) or real())
    write(state, [{"id": OWNER, "role": "adult"}, {"id": NEW, "role": "adult"}])
    with k.spine._lock:
        for op, args in (("_op_publish", {"kind": "nothing", "payload": {}}), ("_op_act", {"action": "nothing", "on_behalf_of": OWNER})):
            try:
                getattr(srv, op)(BRAIN, args)
            except Exception:  # noqa: BLE001 - the op's own answer is not the point: whether it looked at the roster is
                pass
    assert looked == [] and NEW not in k.grants.people
    out = _ask(srv, NEW)                                                    # the door: looked, and NEW is known
    assert looked == [1] and NEW in k.grants.people
    with k.spine._lock:
        srv._op_list_report(BRAIN, {"ask": out["ask"], "outcome": "done"})
    assert looked == [1, 1]


def test_a_role_that_changes_in_the_file_takes_effect_both_ways(state):
    """A child restated as an adult may delete a household list; restated as a plain id (no role) they are least access again.

    Negative control: in ``apply_roster`` keep the person the kernel already has (``want = {**members, **dict(people)}``) -> the
    role never moves and the second block fails."""
    d = start(state)
    assert refused(d, KID, "list.delete") == lists.ADULTS_ONLY
    write(state[0], [{"id": OWNER, "role": "adult"}, {"id": KID, "role": "adult"}])
    assert ask(d, KID, "list.delete")["ask"]
    write(state[0], [{"id": OWNER, "role": "adult"}, KID])
    assert refused(d, KID, "list.delete") == lists.ADULTS_ONLY
    assert ask(d, KID)["ask"]                                               # still a member: items of a household list are theirs


@pytest.mark.parametrize("entry", [{"id": NEW, "role": "adult", "admin": True}, {"id": NEW, "role": "owner"},
                                   {"id": NEW, "role": "adult", "guardians": "member-ova"}, {"id": NEW}, NEW])
def test_an_entry_that_claims_more_than_the_reader_allows_is_least_access(state, entry):
    """The file is never trusted to grant more than ``capability.person_from_entry`` allows: an unknown key, an unknown role,
    guardians that are not a list, or no role at all join as LEAST access (known, so a household list's items are theirs; never
    an adult's verbs).

    Negative control: ``roster_people`` made to trust the entry (``Person(p["id"], p.get("role", "adult"))``, a plain id an
    adult) -> every case but the unknown role is an adult and the ``adults_only`` line fails (an unknown role is reduced by
    ``Person`` itself, whoever reads the entry)."""
    d = start(state)
    write(state[0], [{"id": OWNER, "role": "adult"}, entry])
    assert ask(d, NEW)["ask"]
    assert refused(d, NEW, "list.delete") == lists.ADULTS_ONLY
    assert refused(d, NEW, "list.share") == lists.ADULTS_ONLY


def test_a_member_taken_off_the_file_is_treated_exactly_as_a_restart_would_treat_them(state):
    """The existing rule, kept: a member taken off the roster stays a person the kernel KNOWS while it holds a record for them
    (so they can be forgotten), with least access and no grant; once forgotten they are nobody (``unknown_person``). The same
    daemon after the re-read and a fresh start on the same file give the same answers.

    Negative control: the ``revoke`` loop removed from ``apply_roster`` -> the removed member's watch-only act is still served
    (``no grant`` is not raised); and ``want`` built from the roster alone (no ``known``) -> they are ``unknown_person`` at once
    and cannot be forgotten, where a restart keeps them."""
    d = start(state)
    d.add(KID, "synthetic persimmon words")
    assert ask(d, KID, owner=KID, visibility="private")["ask"]
    write(state[0], [{"id": OWNER, "role": "adult"}])

    def answers(daemon) -> list:
        out = [refused(daemon, KID, "list.delete"),                       # least access: not an adult's verb
               bool(ask(daemon, KID)["ask"]),                             # known: the house rule for a guest
               bool(ask(daemon, OWNER, owner=KID)["ask"])]                # a list they still own has a known owner
        with pytest.raises(Refused, match="no grant"):
            daemon.add(KID, "synthetic medlar words")                     # never proposable
        return out

    live = answers(d)
    assert live == [lists.ADULTS_ONLY, True, True]
    assert known(state[0]) == sorted([OWNER, KID])
    d.stop()
    again = start(state)
    assert answers(again) == live                                         # a restart and a re-read agree
    assert again.panel.call("forget", subject=KID, actor=KID)["count"] >= 1
    assert known(state[0]) == [OWNER]
    assert refused(again, KID) == lists.UNKNOWN_PERSON                    # nothing held, off the roster: nobody
    assert refused(again, OWNER, owner=KID) == lists.UNKNOWN_PERSON


def test_a_removed_member_is_forgettable_in_the_same_daemon_and_then_gone(state):
    """Without the restart in between: taken off the file, forgotten, then no person at all, and ``known-people.json`` follows.

    Negative control: ``write_known`` removed from ``apply_roster`` is NOT caught here (the start had already written them);
    the control for this test is the ``known`` one above (``want`` from the roster alone): the forget is then refused
    "only a person can ask to forget"."""
    d = start(state)
    d.add(KID, "synthetic persimmon words")
    write(state[0], [{"id": OWNER, "role": "adult"}])
    assert ask(d, OWNER)["ask"]                                           # the file is read here
    assert d.panel.call("forget", subject=KID, actor=KID)["count"] >= 2
    assert known(state[0]) == [OWNER]
    assert refused(d, KID) == lists.UNKNOWN_PERSON
    assert b"synthetic persimmon" not in (state[0] / "kernel" / "zoe-os.db").read_bytes()


@pytest.mark.parametrize("damaged", ["not json at all", '{"people": [7]}', '{"members": []}', None])
def test_a_file_that_cannot_be_read_changes_nothing_and_a_good_one_is_applied_after_it(state, damaged):
    """A damaged or missing people.json states nothing: the roster in force stays (nobody is locked out, nobody gains anything),
    and the next readable file is applied.

    Negative control: ``sync_roster`` made to apply an unreadable file as an empty roster (``entries = load_roster(...) or []``)
    -> the owner is refused after the damage (their grant is revoked, the new member is nobody)."""
    d = start(state)
    if damaged is None:
        (state[0] / "people.json").unlink()
    else:
        (state[0] / "people.json").write_text(damaged)
    assert ask(d, OWNER, "list.delete")["ask"]
    assert refused(d, NEW) == lists.UNKNOWN_PERSON
    assert d.add(OWNER, "synthetic quince words")["verified"] is True
    write(state[0], [{"id": OWNER, "role": "adult"}, {"id": NEW, "role": "adult"}])
    assert ask(d, NEW, "list.delete")["ask"]


# ---- in process: what a re-read costs, and what it must not write ---------------------------------------------------------------
@pytest.fixture
def built(state):
    import shadow_daemon

    k, srv, _ = shadow_daemon.build(state[0])
    yield shadow_daemon, k, srv, state[0]
    k.close()


def _ask(srv, person: str, verb: str = "item.add") -> dict:
    with srv.kernel.spine._lock:
        return srv._op_list_ask(BRAIN, {"verb": verb, "person": person, "list": "list-qov", "owner": OWNER,
                                        "visibility": "household"})


def test_an_unchanged_file_is_never_read_again_and_a_changed_one_is_read_once(built, monkeypatch):
    """Every ask costs one ``stat``; the file is opened only when it is a different file than the one last read.

    Negative control: ``sync_roster``'s ``if seen == self.roster_seen: return`` removed -> the file is read on every ask (the
    first count is 20, not 0)."""
    daemon, k, srv, state = built
    reads = []
    real = daemon.load_roster
    monkeypatch.setattr(daemon, "load_roster", lambda s: reads.append(1) or real(s))
    for _ in range(20):
        _ask(srv, OWNER)
    assert reads == []
    write(state, [{"id": OWNER, "role": "adult"}, {"id": NEW, "role": "adult"}])
    for _ in range(20):
        _ask(srv, NEW)
    assert reads == [1]
    (state / "people.json").write_text("damaged")                           # read once, then left alone until it changes
    for _ in range(5):
        _ask(srv, NEW)
    assert reads == [1, 1]


def test_a_re_read_names_only_who_changed_and_never_a_forgotten_member(built):
    """What a re-read writes: one grant record for the member who joined. A member who is on the roster and unchanged gets no
    new record, and one whose whole record was forgotten (and who has not acted since) is NOT granted by a re-read: nothing may
    name them until their next act (``record_again``).

    Negative control: ``- self.forgotten`` removed from the grant loop in ``apply_roster`` -> the forgotten member is granted by
    the re-read and their id is back in the database (the ``not in`` line fails)."""
    daemon, k, srv, state = built
    assert k.spine.forget(KID, actor=KID)                                   # the whole person: the grant record goes with it
    srv.forgot(KID)
    db = state / "kernel" / "zoe-os.db"

    def grant_rows() -> int:
        k.spine._db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        con = sqlite3.connect(str(db))
        try:
            return con.execute("SELECT COUNT(*) FROM events WHERE kind = 'grant_change' AND tombstoned = 0").fetchone()[0]
        finally:
            con.close()

    before = grant_rows()
    write(state, [{"id": OWNER, "role": "adult"}, {"id": KID, "role": "child", "guardians": [OWNER]},
                  {"id": NEW, "role": "adult"}])
    assert _ask(srv, NEW)["ask"]
    assert grant_rows() == before + 1                                       # the one who joined; nobody else
    assert k.grants.of("brain", KID) is None and KID in srv.forgotten
    assert KID.encode() not in db.read_bytes()
    srv.record_again(KID)                                                   # their next act: recorded afresh, as before
    assert k.grants.of("brain", KID) is not None


def test_a_forgotten_member_taken_off_the_file_and_put_back_is_still_not_granted(built):
    """The forgotten mark outlives roster membership. Forgotten, then removed from the file, then added again: no re-read may
    write a grant that names them; only their own next act does (``record_again``).

    Negative control: ``self.forgotten &= self.roster`` back in ``apply_roster`` -> the removal drops the mark, the re-add
    grants them, and the ``is None`` line fails. A second control: the revoke on removal written whether or not a grant
    exists -> the removal itself writes a record naming the forgotten member, and the database scan fails."""
    daemon, k, srv, state = built
    assert k.spine.forget(KID, actor=KID)
    srv.forgot(KID)
    write(state, [{"id": OWNER, "role": "adult"}])                           # KID taken off the file
    assert _ask(srv, OWNER)["ask"]
    assert KID in srv.forgotten
    write(state, [{"id": OWNER, "role": "adult"}, {"id": KID, "role": "child", "guardians": [OWNER]}])     # ... and put back
    assert _ask(srv, OWNER)["ask"]
    assert k.grants.of("brain", KID) is None and KID in srv.forgotten
    k.spine._db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    assert KID.encode() not in (state / "kernel" / "zoe-os.db").read_bytes()
    srv.record_again(KID)                                                   # their next act: granted again, as designed
    assert k.grants.of("brain", KID) is not None


def test_a_backfill_that_fails_leaves_the_old_roster_in_memory_and_the_next_re_read_backfills(built, monkeypatch):
    """The new people are swapped in only if the index backfill committed. If it raises, memory goes back to the roster in
    force, so the next re-read still sees the member as NEW and backfills: their older records stay inside their forget.

    Negative control: without the roll-back in ``apply_roster`` the retry finds nobody added, never backfills, and the older
    row survives the new member's forget (the last line fails)."""
    daemon, k, srv, state = built
    from zoe_os.events import Event, Kind, Visibility
    from zoe_os.spine import KERNEL

    old = k.spine._kernel_append(Event(Kind.REFUSAL, KERNEL, visibility=Visibility.KERNEL,
                                       payload={"who": "brain", "attempted": "act item.add", "list": NEW}))
    real, calls = k.spine._backfill_nested, []

    def once_broken():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("backfill failed")
        return real()

    monkeypatch.setattr(k.spine, "_backfill_nested", once_broken)
    write(state, [{"id": OWNER, "role": "adult"}, {"id": NEW, "role": "adult"}])
    with pytest.raises(RuntimeError):
        _ask(srv, NEW)
    assert NEW not in k.grants.people and NEW not in k.spine._members       # memory is the roster that was in force
    assert _ask(srv, NEW)["ask"] and len(calls) == 2                        # the next ask re-reads, and backfills
    assert k.spine.forget(NEW, actor=NEW)
    assert k.spine._get(old.seq) is None


def test_a_new_member_named_inside_an_older_record_is_reached_by_their_forget(built):
    """The spine indexes member ids named INSIDE a payload only for people it knows. A member who joins by a re-read is one of
    them from then on, older rows included, so their forget reaches a record that named them before they joined.

    Negative control: the ``_members`` / ``_backfill_nested`` lines removed from ``apply_roster`` -> the older row survives the
    new member's forget (the last line fails)."""
    daemon, k, srv, state = built
    from zoe_os.events import Event, Kind, Visibility
    from zoe_os.spine import KERNEL

    old = k.spine._kernel_append(Event(Kind.REFUSAL, KERNEL, visibility=Visibility.KERNEL,
                                       payload={"who": "brain", "attempted": "act item.add", "list": NEW}))
    write(state, [{"id": OWNER, "role": "adult"}, {"id": NEW, "role": "adult"}])
    assert _ask(srv, NEW)["ask"]
    assert k.spine._get(old.seq) is not None
    assert k.spine.forget(NEW, actor=NEW)
    assert k.spine._get(old.seq) is None
