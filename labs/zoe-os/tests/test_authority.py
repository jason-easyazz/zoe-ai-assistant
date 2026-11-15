"""The kernel as the AUTHORITY for list add (``authority.py``): durable rows, erase reach over the wire, health.

Each test runs the real daemon as its own process (``authority_proc.py``) on a temp state dir and talks to it over its socket.
A kill is a real SIGKILL at one exact point of the write. Every guarantee has a negative control that removes it and shows the
test catches the loss. Synthetic members and items only.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from authority import digest
from zoe_os import wire
from zoe_os.client import Client
from zoe_os.spine import Refused

HERE = Path(__file__).parent
A, B = "member-a", "member-b"


class Daemon:
    def __init__(self, state: Path, *, die: str = "", control: str = "", db: str = "") -> None:
        env = {"PATH": os.environ["PATH"], "HOME": str(state), "OS_LAB_DIE_AT": die, "OS_LAB_CONTROL": control}
        self.state = state
        self.proc = subprocess.Popen([sys.executable, "-B", str(HERE / "authority_proc.py"), "--dir", str(state),
                                      *(["--db", db] if db else [])],
                                     stdout=subprocess.PIPE, text=True, env=env)
        assert self.proc.stdout.readline().startswith("zoe-os authority kernel up")
        raw = json.loads((state / "creds.json").read_text())
        self.creds = {k: bytes.fromhex(v) for k, v in raw["creds"].items()}
        self.sock = raw["socket"]
        self.voice, self.brain, self.panel = (Client(self.sock, s, self.creds[s], timeout=5) for s in ("voice", "brain", "panel"))
        erase = json.loads((state / "erase-creds.json").read_text())["creds"]["eraser"]
        self.eraser = Client(self.sock, "eraser", bytes.fromhex(erase), timeout=5)       # the member-delete path's own credential

    def add(self, person: str, item: str) -> dict:
        said = self.voice.publish("utterance", subject=person, payload={"text": "synthetic list add"})
        return self.brain.call("act", action="list.add", on_behalf_of=person,
                               args={"item": item, "list": "shopping", "asked": said.seq}, caused_by=[said.seq])

    def digests(self) -> list:
        return self.panel.call("list_digest")["digests"]

    def kill(self) -> None:
        self.proc.kill()
        self.proc.wait(timeout=10)


@pytest.fixture
def state():
    path = Path(tempfile.mkdtemp(prefix="za"))           # short: a socket path is <= 107 bytes
    (path / "people.json").write_text(json.dumps({"people": [A, B]}))
    procs: list[Daemon] = []
    yield path, procs
    for d in procs:
        if d.proc.poll() is None:
            d.kill()


def start(st, **kw) -> Daemon:
    d = Daemon(st[0], **kw)
    st[1].append(d)
    return d


def receipts(path: Path, outcome: str) -> int:
    db = sqlite3.connect(f"file:{path / 'kernel' / 'zoe-os.db'}?mode=ro", uri=True)
    try:
        return sum(1 for (raw,) in db.execute("SELECT payload FROM events WHERE kind = 'action_receipt' AND tombstoned = 0")
                   if json.loads(raw).get("action") == "list.add" and json.loads(raw).get("outcome") == outcome)
    finally:
        db.close()


# ---- durability: a kill at each stage of the write ------------------------------------------------------------------------------
@pytest.mark.parametrize("die,present,uncertain", [
    ("after_intent", False, 1),     # the intent was durable, the driver never ran: absent, and its receipt says uncertain
    ("after_do", False, 1),         # the rows changed in memory, no receipt: absent after the restart (never half there), uncertain
    ("after_commit", True, 0),      # the verified receipt is stored, only the reply was lost: present, and the record says done
])
def test_an_acknowledged_add_survives_a_kill_and_an_unacknowledged_one_is_absent_or_uncertain(state, die, present, uncertain):
    d = start(state, die=die)
    assert d.add(A, "kept")["verified"] is True                     # acknowledged
    with pytest.raises((ConnectionError, OSError, wire.WireError)):
        d.add(A, "die")
    assert d.proc.wait(timeout=10) == -9
    again = start(state)
    got = again.digests()
    assert digest("kept") in got
    assert (digest("die") in got) is present
    assert again.brain.call("health")["recovered"] == uncertain
    assert receipts(state[0], "uncertain") == uncertain and receipts(state[0], "verified") == 1 + present
    assert again.add(B, "after")["verified"] is True and digest("after") in again.digests()      # it serves on


def test_control_without_the_rebuild_an_acknowledged_add_is_lost_and_the_test_sees_it(state):
    d = start(state)
    assert d.add(A, "kept")["verified"] is True
    d.kill()
    assert digest("kept") not in start(state, control="no_rebuild").digests()


def test_undo_after_a_restart_reverses_the_add_and_stays_reversed(state):
    d = start(state)
    d.add(A, "bread")
    d.add(A, "jam")
    d.kill()
    again = start(state)
    undone = again.panel.call("undo", person=A, actor=A)
    assert undone["verified"] is True and undone["action"] == "undo"
    assert again.digests() == [digest("bread")]
    again.kill()
    assert start(state).digests() == [digest("bread")]                # the undo receipt is replayed too


# ---- erase reach -----------------------------------------------------------------------------------------------------------------
def _on_disk(path: Path, word: bytes) -> bool:
    return any(word in f.read_bytes() for f in (path / "kernel").iterdir() if f.is_file())


def test_erase_member_removes_everything_of_theirs_is_idempotent_and_holds_across_a_restart(state):
    d = start(state)
    d.add(A, "oat milk")
    d.add(A, "eggs")
    d.add(B, "rice")
    out = d.eraser.call("erase_member", subject=A)
    assert out["verified"] is True and out["erased"] >= 4 and isinstance(out["receipt_seq"], int)
    assert d.digests() == [digest("rice")]
    assert not _on_disk(state[0], b"oat milk")                       # the words left the database and its WAL
    twice = d.eraser.call("erase_member", subject=A)
    assert twice["erased"] == 0 and twice["verified"] is True        # idempotent
    with pytest.raises(Refused):
        d.add(A, "anything")                                         # the grant went with them
    with pytest.raises(Refused):
        d.brain.call("erase_member", subject=B)                      # a skill may not erase a person: only a surface relays it
    d.kill()
    again = start(state)
    assert again.digests() == [digest("rice")]
    with pytest.raises(Refused):
        again.add(A, "anything")                                     # not re-granted at the restart
    assert again.add(B, "beans")["verified"] is True
    out = again.eraser.call("erase_member", subject="member-qz-never")
    assert out == {"erased": 0, "receipt_seq": None, "verified": True, "known": False}   # never seen: checked, nothing held
    assert not _on_disk(state[0], b"member-qz-never")               # and no forget notice or ledger row was written for a stranger


def test_control_an_erase_that_skips_the_forget_is_caught_by_its_read_back(state):
    d = start(state, control="no_member_forget")
    d.add(A, "oat milk")
    assert d.eraser.call("erase_member", subject=A)["verified"] is False    # fail closed: the caller must not say "deleted"


def test_only_the_member_delete_credential_may_erase_a_member(state):
    d = start(state)
    d.add(A, "oat milk")
    for who in (d.panel, d.brain, d.voice):
        with pytest.raises(Refused, match="only the member-delete path"):
            who.call("erase_member", subject=A)
    assert d.digests() == [digest("oat milk")]                       # nothing was erased by the refused calls
    assert "eraser" not in json.loads((state[0] / "creds.json").read_text())["creds"]       # apart from the surfaces' file
    assert (state[0] / "erase-creds.json").stat().st_mode & 0o777 == 0o600
    assert d.eraser.call("erase_member", subject=A)["verified"] is True


def test_a_member_taken_off_the_roster_is_still_erased_by_id_and_then_leaves_the_kernel(state):
    d = start(state)
    d.add(A, "oat milk")
    d.add(B, "rice")
    d.kill()
    (state[0] / "people.json").write_text(json.dumps({"people": [B]}))      # A was taken off the roster
    again = start(state)
    out = again.eraser.call("erase_member", subject=A)
    assert out["known"] is True and out["verified"] is True and out["erased"] >= 2
    assert again.digests() == [digest("rice")] and not _on_disk(state[0], b"oat milk")
    assert again.eraser.call("erase_member", subject=A)["known"] is False    # forget first, then the roster: they are gone
    again.kill()
    assert start(state).eraser.call("erase_member", subject=A)["known"] is False   # and a restart does not bring them back


def test_a_member_off_the_roster_forgotten_through_the_forget_op_is_reached_too(state):
    d = start(state)
    d.add(A, "oat milk")
    d.kill()
    (state[0] / "people.json").write_text(json.dumps({"people": [B]}))
    again = start(state)
    assert again.panel.call("forget", subject=A, actor=A)["count"] >= 2
    assert again.digests() == [] and not _on_disk(state[0], b"oat milk")


def test_erase_item_forgets_one_members_item_by_digest_and_leaves_the_others(state):
    d = start(state)
    d.add(A, "oat milk")
    d.add(B, "Oat Milk")
    d.add(A, "eggs")
    with pytest.raises(Refused, match="name whose item"):
        d.panel.call("erase_item", digest=digest("oat milk"))        # no subject: refused, never household-wide
    out = d.panel.call("erase_item", digest=digest("oat milk"), subject=A)
    assert out["verified"] is True and out["erased"] >= 2
    assert d.digests() == sorted([digest("eggs"), digest("Oat Milk")])       # B's copy is still there
    assert not _on_disk(state[0], b"oat milk") and _on_disk(state[0], b"Oat Milk")
    assert d.panel.call("erase_item", digest=digest("oat milk"), subject=A)["erased"] == 0       # idempotent
    with pytest.raises(Refused):
        d.brain.call("erase_item", digest=digest("eggs"), subject=A)
    d.kill()
    assert start(state).digests() == sorted([digest("eggs"), digest("Oat Milk")])


def test_an_entity_forget_through_the_old_op_also_reaches_the_durable_rows(state):
    d = start(state)
    d.add(A, "gift for Dana")
    d.add(A, "eggs")
    d.panel.call("forget", subject=A, actor=A, entity="Dana")
    d.kill()                                                         # the rebuild, not the in-memory keeper, is what is checked
    assert start(state).digests() == [digest("eggs")]


# ---- health and consent -----------------------------------------------------------------------------------------------------------
def test_health_says_alive_the_schema_and_that_a_write_goes_through(state):
    d = start(state)
    h = d.brain.call("health")
    assert h["alive"] is True and h["schema"] == 1 and h["last_write_ok"] is True and h["recovered"] == 0


def test_a_revoke_is_remembered_across_a_restart(state):
    d = start(state)
    d.panel.call("revoke", person=A, skill="brain")
    with pytest.raises(Refused, match="no grant"):
        d.add(A, "x")
    d.kill()
    again = start(state)
    with pytest.raises(Refused, match="no grant"):
        again.add(A, "x")
    assert again.add(B, "y")["verified"] is True


# ---- the one database setting -----------------------------------------------------------------------------------------------------
def test_the_database_lives_where_db_says_and_an_old_record_is_moved_there_at_start(state):
    perm = state[0] / "perm" / "zoe-os.db"
    d = start(state, db=str(perm))
    d.add(A, "kept")
    d.kill()
    assert perm.is_file() and not (state[0] / "kernel" / "zoe-os.db").exists()
    assert start(state, db=str(perm)).digests() == [digest("kept")]
    old = start(state)                                               # the lab default: STATE/kernel/zoe-os.db
    old.add(A, "old")
    old.kill()
    moved = start(state, db=str(state[0] / "new.db"))                # the start FINISHES the move
    assert moved.digests() == [digest("old")]
    assert not [f for f in (state[0] / "kernel").iterdir() if f.name.startswith("zoe-os.db")]     # no old copy, no sidecar


# ---- the move, interrupted at every point (in-process: ``authority.migrate`` is what every start runs first) ---------------------
def _old_record(state: Path) -> Path:
    d = start(state)
    d.add(A, "moved item")
    d.kill()                                                         # killed: the old record still has its WAL
    return state[0] / "kernel" / "zoe-os.db"


def _old_left(old: Path) -> list:
    return sorted(f.name for f in old.parent.iterdir() if f.name.startswith(old.name))


@pytest.mark.parametrize("point", ["before_copy", "mid_copy", "after_copy", "after_removal"])
def test_a_move_interrupted_at_any_point_is_finished_and_leaves_no_old_copy(state, point):
    import authority

    old, new = _old_record(state), state[0] / "perm" / "zoe-os.db"
    want = authority.fingerprint(old) if point != "after_removal" else None
    if point == "mid_copy":
        new.parent.mkdir(mode=0o700)
        (new.parent / "zoe-os.db.moving").write_bytes(b"SQLite format 3\x00 half a copy")
    elif point in ("after_copy", "after_removal"):
        def die(_db):
            raise KeyboardInterrupt                                  # killed right after the rename, before the removal
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(authority, "_remove_copy", die)
            with pytest.raises(KeyboardInterrupt):
                authority.migrate(old, new)
        assert new.is_file() and old.is_file()
        want = authority.fingerprint(new)
        if point == "after_removal":
            old.unlink()                                             # the main file went; a sidecar was left behind
            (old.parent / (old.name + "-wal")).write_bytes(b"")
    authority.migrate(old, new)
    assert _old_left(old) == [] and not (new.parent / "zoe-os.db.moving").exists()
    assert authority.fingerprint(new) == want
    assert start(state, db=str(new)).digests() == [digest("moved item")]


def test_a_copy_that_does_not_match_the_old_record_refuses_the_start_and_keeps_both(state):
    import authority

    old, new = _old_record(state), state[0] / "perm" / "zoe-os.db"
    new.parent.mkdir(mode=0o700)
    con = sqlite3.connect(str(old))
    con.execute("PRAGMA journal_mode=DELETE")
    con.execute(f"VACUUM INTO '{new}'")
    con.close()
    con = sqlite3.connect(str(new))
    con.execute("DELETE FROM events WHERE seq = (SELECT MAX(seq) FROM events)")      # the copy lost a row
    con.commit()
    con.close()
    with pytest.raises(SystemExit, match="could not be verified"):
        authority.migrate(old, new)
    assert old.is_file() and new.is_file()                           # neither is thrown away: the owner decides
    p = subprocess.run([sys.executable, "-B", str(HERE / "authority_proc.py"), "--dir", str(state[0]), "--db", str(new)],
                       capture_output=True, text=True, timeout=30, env={"PATH": os.environ["PATH"], "HOME": str(state[0])})
    assert p.returncode != 0 and "could not be verified" in p.stderr
