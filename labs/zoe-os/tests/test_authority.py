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

from authority import digest, keyed_digest
from zoe_os import wire
from zoe_os.client import Client
from zoe_os.spine import Refused

HERE = Path(__file__).parent
A, B = "member-a", "member-b"
_COUNTER = __import__("itertools").count()


def _t(prefix: str) -> str:
    """A fresh utterance text: main's kernel shields forgotten words for EVERY publisher, so a text repeated across requests would
    be refused once one copy of it was forgotten."""
    return f"{prefix} {next(_COUNTER)}"


ASKED_FOR: dict = {}            # state dir -> {(member, item)} every add a test made there (survives the daemon's restarts)


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
        ident = json.loads((state / "identity-creds.json").read_text())
        self.speaker_key, self.generation = bytes.fromhex(ident["creds"]["speaker-verifier"]), ident["generation"]
        self.voice, self.brain = (Client(self.sock, s, self.creds[s], timeout=5) for s in ("voice", "brain"))
        self.panel = Client(self.sock, "panel", self.creds["panel"], timeout=5, speaker_key=self.speaker_key,
                            speaker_generation=self.generation)   # the test verified the person
        self.bare_panel = Client(self.sock, "panel", self.creds["panel"], timeout=5)                          # the surface's word alone
        erase = json.loads((state / "erase-creds.json").read_text())["creds"]["eraser"]
        self.eraser = Client(self.sock, "eraser", bytes.fromhex(erase), timeout=5)       # the member-delete path's own credential
        rec = json.loads((state / "reconcile-creds.json").read_text())
        self.digest_key = bytes.fromhex(rec["digest_key"])
        self.reconciler = Client(self.sock, "reconciler", bytes.fromhex(rec["creds"]["reconciler"]), timeout=5)

    def add(self, person: str, item: str) -> dict:
        ASKED_FOR.setdefault(str(self.state), set()).add((person, item))      # the "production rows" the reconciler matches against
        said = self.voice.publish("utterance", subject=person, payload={"text": _t("synthetic list add")})
        return self.brain.call("act", action="list.add", on_behalf_of=person,
                               args={"item": item, "list": "shopping", "asked": said.seq}, caused_by=[said.seq])

    def remind(self, person: str, action: str = "remind.set", *, mentions=(), **args) -> dict:
        said = self.voice.publish("utterance", subject=person, payload={"text": _t("synthetic reminder")}, mentions=list(mentions))
        out = self.brain.call("act", action=action, on_behalf_of=person, args={**args, "asked": said.seq}, caused_by=[said.seq])
        return {**out, "said": said.seq}

    def notes(self, person: str) -> dict:
        return {r["note"]: r for r in self.panel.call("reminders", person=person)["reminders"]}

    def keyed(self) -> list:
        return self.reconciler.call("list_digest")["digests"]

    def digests(self) -> list:
        """What the kernel holds, as the plain item digests the assertions read: each KEYED digest the reconciler gets is matched,
        the way the reconcile tool does, against the keyed digest of every row this state directory's test ever asked for."""
        known = {keyed_digest(self.digest_key, m, "shopping", i): digest(i) for m, i in ASKED_FOR.get(str(self.state), ())}
        return sorted(known.get(k, "unmatched") for k in self.keyed())

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


def test_a_member_is_erased_only_by_the_member_delete_credential_or_their_own_proven_word(state):
    from zoe_os import speaker
    d = start(state)
    d.add(A, "oat milk")
    d.add(B, "rice")
    for who in (d.brain, d.voice):
        with pytest.raises(Refused, match="only a person, through a trusted surface"):
            who.call("erase_member", subject=A)
    with pytest.raises(Refused, match="speaker unproven"):
        d.bare_panel.call("erase_member", subject=A)                 # the panel's credential alone: refused
    with pytest.raises(Refused, match="speaker unproven"):           # B's proven word cannot erase A
        d.bare_panel.call("erase_member", subject=A, speaker=speaker.mint(d.speaker_key, B, "forget.person", generation=d.generation))
    assert d.digests() == sorted([digest("oat milk"), digest("rice")])     # nothing was erased by the refused calls
    assert "eraser" not in json.loads((state[0] / "creds.json").read_text())["creds"]       # apart from the surfaces' file
    for name in ("erase-creds.json", "identity-creds.json"):
        assert (state[0] / name).stat().st_mode & 0o777 == 0o600
    assert "speaker-verifier" not in (state[0] / "creds.json").read_text()
    with pytest.raises(Refused, match="only a person, through a trusted surface"):
        d.eraser.call("grant", person=A, skill="brain")              # the eraser is the forget family, nothing else
    assert d.panel.call("erase_member", subject=B)["verified"] is True     # B, saying "forget everything about me"
    assert d.eraser.call("erase_member", subject=A)["verified"] is True    # member delete
    assert d.digests() == []


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


# ---- the roster at start: an empty one is a household, never a reason to stop serving forget ---------------------------------------
def _members_bytes(state: Path, word: bytes) -> list:
    """Every file of the state directory but the credential files, scanned for ``word`` (the contract test's byte-scan pattern)."""
    skip = {"creds.json", "erase-creds.json", "identity-creds.json", "reconcile-creds.json"}
    return [f.name for f in state.rglob("*") if f.is_file() and f.name not in skip and word in f.read_bytes()]


@pytest.mark.parametrize("roster", ['{"people": []}', "not json at all", '{"people": [7]}'])
def test_with_the_last_member_removed_or_an_unreadable_roster_the_kernel_starts_forgets_and_acts_for_nobody(state, roster):
    d = start(state)
    d.add(A, "synthetic quince marker")
    d.kill()
    (state[0] / "people.json").write_text(roster)                      # the last member left (or the file is damaged)
    again = start(state)
    assert again.brain.call("health")["roster"] == ("empty" if roster.startswith('{"people": []') else "unreadable")
    said = again.voice.publish("utterance", subject=A, payload={"text": _t("synthetic")})    # A is recorded: known, never granted
    with pytest.raises(Refused, match="no grant"):                     # nobody is granted anything: nothing can be acted
        again.brain.call("act", action="list.add", on_behalf_of=A, args={"item": "x", "asked": said.seq}, caused_by=[said.seq])
    # B never said anything, but B's grant record names them (main's kernel links it): known, for forget only, never granted
    said_b = again.voice.publish("utterance", subject=B, payload={"text": _t("synthetic")})
    with pytest.raises(Refused, match="no grant"):
        again.brain.call("act", action="list.add", on_behalf_of=B, args={"item": "x", "asked": said_b.seq}, caused_by=[said_b.seq])
    assert _members_bytes(state[0], b"synthetic quince marker")         # the control of the scan: the words are there
    assert again.panel.call("forget", subject=A, actor=A)["count"] >= 2  # A, saying "forget everything about me"
    assert _members_bytes(state[0], b"synthetic quince marker") == []
    assert again.digests() == []


def test_a_missing_roster_file_is_an_operator_error_and_refuses_the_start(state):
    (state[0] / "people.json").unlink()
    p = subprocess.run([sys.executable, "-B", str(HERE / "authority_proc.py"), "--dir", str(state[0])],
                       capture_output=True, text=True, timeout=30, env={"PATH": os.environ["PATH"], "HOME": str(state[0])})
    assert p.returncode != 0 and "people.json is missing" in p.stderr


# ---- undo of a remove survives a forget it does not touch -------------------------------------------------------------------------
def _remove(d: Daemon, person: str, item: str) -> dict:
    said = d.voice.publish("utterance", subject=person, payload={"text": _t("synthetic list remove")})
    return d.brain.call("act", action="list.remove", on_behalf_of=person,
                        args={"item": item, "list": "shopping", "asked": said.seq}, caused_by=[said.seq])


@pytest.mark.parametrize("forget", ["unrelated", "one_removed_row"])
def test_undo_of_a_remove_after_a_forget_restores_exactly_the_rows_the_forget_did_not_reach(state, forget):
    """A forget scrubs the remove's snapshot row by row (``provenance: 1``). Forgetting an UNRELATED item keeps the undo whole.
    Forgetting ONE of the removed rows leaves the other in the snapshot, but the undo is then refused by the kernel's forget shield
    (``forgotten_action_arguments``): the surviving row has the same words as the forgotten one, and forgotten words never come back."""
    d = start(state)
    first, second = d.add(A, "milk"), d.add(A, "milk")
    d.add(A, "eggs")
    removed = _remove(d, A, "milk")
    assert removed["verified"] is True and d.digests() == [digest("eggs")]
    if forget == "one_removed_row":
        assert d.panel.call("forget", subject=A, actor=A, seqs=[first["asked"]])["count"] >= 1
        db = sqlite3.connect(f"file:{state[0] / 'kernel' / 'zoe-os.db'}?mode=ro", uri=True)
        (raw,) = db.execute("SELECT payload FROM events WHERE seq = ?", (removed["seq"],)).fetchone()
        db.close()
        kept = json.loads(raw)["undo"]["before"]["rows"]
        assert [r["asked"] for r in kept] == [second["asked"]]        # the unaffected row survives in the snapshot
        with pytest.raises(Refused, match="forgotten_action_arguments"):
            d.panel.call("undo", person=A, actor=A)
        assert d.digests() == [digest("eggs")]                        # nothing forgotten was put back
        return
    assert d.panel.call("erase_item", digest=digest("eggs"), subject=A)["verified"] is True
    want = [digest("milk")] * 2                                       # both removed rows come back; eggs stays forgotten
    undone = d.panel.call("undo", person=A, actor=A)
    assert undone["verified"] is True and undone["action"] == "undo"
    assert d.digests() == want
    d.kill()
    assert start(state).digests() == want                             # the rebuild replays the scrubbed snapshot too


# ---- list_digest: keyed, and only for the reconciler ------------------------------------------------------------------------------
def test_a_surface_cannot_confirm_any_members_item_and_the_reconciler_gets_keyed_digests_it_can_match(state):
    import hashlib
    d = start(state)
    for who, item in ((A, "oat milk"), (B, "eggs"), (B, "oat milk")):
        d.add(who, item)
    for who in (d.panel, d.bare_panel, d.brain, d.voice, d.eraser):
        with pytest.raises(Refused, match="only the reconciler"):
            who.call("list_digest")                                   # a surface, even with a speaker key, a skill, the eraser
    got = d.keyed()
    assert len(got) == len(set(got)) == 3                             # the same item for two members: two different digests
    guesses = set()
    for item in ("oat milk", "eggs", "bread", "Oat Milk", " oat milk "):
        for text in {item, item.strip(), item.strip().lower()}:       # every way the old unkeyed digest could be guessed
            for msg in (text, f"{A}{text}", f"{B}{text}", f"{A}:shopping:{text}", json.dumps([A, "shopping", text]),
                        json.dumps([B, "shopping", text], separators=(",", ":"))):
                h = hashlib.sha256(msg.encode()).hexdigest()
                guesses.update({h, h[:16], h[:32]})
    assert guesses.isdisjoint(got)                                    # the dictionary attack confirms nothing
    assert sorted(keyed_digest(d.digest_key, m, "shopping", i) for m, i in ((A, "oat milk"), (B, "eggs"), (B, "Oat Milk"))) == \
        sorted(got)                                                   # the reconciler, holding the key, matches every row
    assert (state[0] / "reconcile-creds.json").stat().st_mode & 0o777 == 0o600
    others = "".join((state[0] / f).read_text() for f in ("creds.json", "erase-creds.json", "identity-creds.json"))
    assert d.digest_key.hex() not in others and "reconciler" not in others


@pytest.mark.parametrize("damage", ["unreadable_mode", "a_directory"])
def test_a_roster_file_that_is_there_but_cannot_be_read_starts_the_kernel_for_forget_only(state, damage):
    if damage == "unreadable_mode" and os.geteuid() == 0:
        pytest.skip("root reads a mode-000 file: the permission case cannot be shown as root")
    d = start(state)
    d.add(A, "synthetic quince marker")
    d.kill()
    roster = state[0] / "people.json"
    if damage == "unreadable_mode":
        roster.chmod(0o000)
    else:
        roster.unlink()
        roster.mkdir()
    try:
        again = start(state)
        assert again.brain.call("health")["roster"] == "unreadable"
        said = again.voice.publish("utterance", subject=A, payload={"text": _t("synthetic")})
        with pytest.raises(Refused, match="no grant"):
            again.brain.call("act", action="list.add", on_behalf_of=A, args={"item": "x", "asked": said.seq}, caused_by=[said.seq])
        assert again.panel.call("forget", subject=A, actor=A)["count"] >= 2
    finally:
        if roster.is_file():
            roster.chmod(0o600)                                       # (so the scan below can read every file)
    assert _members_bytes(state[0], b"synthetic quince marker") == []


def test_a_roster_symlink_to_nothing_is_a_missing_roster_and_refuses_the_start(state):
    roster = state[0] / "people.json"
    roster.unlink()
    roster.symlink_to(state[0] / "no-such-roster.json")
    p = subprocess.run([sys.executable, "-B", str(HERE / "authority_proc.py"), "--dir", str(state[0])],
                       capture_output=True, text=True, timeout=30, env={"PATH": os.environ["PATH"], "HOME": str(state[0])})
    assert p.returncode != 0 and "people.json is missing" in p.stderr


# ---- a forget reaches every row derived from what it erased, and a rebuild never brings forgotten words back -------------------------
def _say_add(d: Daemon, person: str, item: str, *, text: str = "", mentions=()) -> dict:
    ASKED_FOR.setdefault(str(d.state), set()).add((person, item))
    said = d.voice.publish("utterance", subject=person, payload={"text": text or _t("synthetic list add")}, mentions=list(mentions))
    out = d.brain.call("act", action="list.add", on_behalf_of=person,
                       args={"item": item, "list": "shopping", "asked": said.seq}, caused_by=[said.seq])
    return {**out, "said": said.seq}


def _gone_everywhere(d: Daemon, state, marker: str) -> bool:
    return digest(marker) not in d.digests() and _members_bytes(state[0], marker.encode()) == []


def test_a_forget_of_b_removes_as_row_that_derives_from_an_utterance_naming_b_and_keeps_as_others(state):
    marker, kept = "synthetic marker kumquat-b", "synthetic marker damson-a"
    d = start(state)
    _say_add(d, A, marker, text="synthetic words that name the other member", mentions=[B])     # A's row, from words naming B
    _say_add(d, A, kept)                                                                        # A's row, nothing to do with B
    assert sorted(d.digests()) == sorted([digest(marker), digest(kept)]) and _members_bytes(state[0], marker.encode())
    assert d.eraser.call("erase_member", subject=B)["verified"] is True                         # B: "forget everything about me"
    assert _gone_everywhere(d, state, marker) and d.digests() == [digest(kept)]
    d.kill()
    again = start(state)
    assert _gone_everywhere(again, state, marker) and again.digests() == [digest(kept)]         # and after a restart


@pytest.mark.parametrize("reversal", ["remove", "undo"])
def test_forgetting_the_action_that_reversed_an_add_also_forgets_the_add_and_the_word_never_returns(state, reversal):
    marker = f"synthetic marker quince-{reversal}"
    d = start(state)
    _say_add(d, A, marker)
    if reversal == "remove":
        said = d.voice.publish("utterance", subject=A, payload={"text": f"take {marker} off"})
        back = d.brain.call("act", action="list.remove", on_behalf_of=A,
                            args={"item": marker, "list": "shopping", "asked": said.seq}, caused_by=[said.seq])
        seq = said.seq                                                          # forget the removal's UTTERANCE
    else:
        back = d.panel.call("undo", person=A, actor=A)
        seq = back["seq"]                                                       # forget the undo receipt itself
    assert back["verified"] is True and d.digests() == []
    assert d.panel.call("forget", subject=A, actor=A, seqs=[seq])["count"] >= 2
    assert _gone_everywhere(d, state, marker)                                   # not replayed, not on disk
    d.kill()
    again = start(state)
    assert _gone_everywhere(again, state, marker)                               # nor after a restart (the rebuild from the log)


def test_forgetting_the_add_by_seq_removes_its_row_and_a_later_undo_of_its_removal_does_not_bring_it_back(state):
    marker = "synthetic marker medlar"
    d = start(state)
    first = _say_add(d, A, marker)
    assert d.panel.call("forget", subject=A, actor=A, seqs=[first["said"]])["count"] >= 2   # the ADD, by seq
    assert _gone_everywhere(d, state, marker)
    other = "synthetic marker loquat"
    _say_add(d, A, other)
    said = d.voice.publish("utterance", subject=A, payload={"text": _t("synthetic list remove")})
    d.brain.call("act", action="list.remove", on_behalf_of=A, args={"item": other, "list": "shopping", "asked": said.seq},
                 caused_by=[said.seq])
    assert d.digests() == []                                                     # an ordinary add and remove still work after it
    d.kill()
    again = start(state)
    assert _gone_everywhere(again, state, marker) and again.digests() == []


def test_undoing_a_removal_whose_add_was_forgotten_does_not_bring_the_word_back(state):
    """Forgetting the ADD also erases the removal that took that row (it named only that row), and the removal's request: its words
    are X's words. So nothing holds X afterwards, not the row, not the removal, not the bytes on disk."""
    marker = "synthetic marker sloe"
    d = start(state)
    first = _say_add(d, A, marker)
    said = d.voice.publish("utterance", subject=A, payload={"text": f"take {marker} off the list"})
    d.brain.call("act", action="list.remove", on_behalf_of=A, args={"item": marker, "list": "shopping", "asked": said.seq},
                 caused_by=[said.seq])
    assert d.panel.call("forget", subject=A, actor=A, seqs=[first["said"]])["count"] >= 4   # the add, its receipt, the removal, its request
    try:
        d.panel.call("undo", person=A, actor=A)                                 # nothing of A's is left to undo, or it is refused
    except Refused:
        pass
    assert _gone_everywhere(d, state, marker)
    d.kill()
    assert _gone_everywhere(start(state), state, marker)                        # the bytes are ABSENT, also after a restart


def test_a_removal_of_two_rows_loses_only_the_forgotten_one_and_goes_whole_when_both_are_forgotten(state):
    item, said_1, said_2 = "synthetic marker rowan", "synthetic marker first-request", "synthetic marker second-request"
    d = start(state)
    one = _say_add(d, A, item, text=said_1)
    two = _say_add(d, A, item, text=said_2)
    rm = d.voice.publish("utterance", subject=A, payload={"text": _t("synthetic list remove")})
    removal = d.brain.call("act", action="list.remove", on_behalf_of=A,
                           args={"item": item, "list": "shopping", "asked": rm.seq}, caused_by=[rm.seq])
    assert removal["verified"] is True and d.digests() == []                     # one removal took both rows
    d.panel.call("forget", subject=A, actor=A, seqs=[one["said"]])
    db = sqlite3.connect(f"file:{state[0] / 'kernel' / 'zoe-os.db'}?mode=ro", uri=True)
    (raw,) = db.execute("SELECT payload FROM events WHERE seq = ?", (removal["seq"],)).fetchone()
    db.close()
    assert [r["asked"] for r in json.loads(raw)["undo"]["before"]["rows"]] == [two["said"]]   # kept, scrubbed of the first row
    assert _members_bytes(state[0], said_1.encode()) == []                         # the forgotten request is nowhere
    assert _members_bytes(state[0], item.encode())                                 # the item's words: still the second row's own
    d.panel.call("forget", subject=A, actor=A, seqs=[two["said"]])                 # now every row it removed is forgotten
    for word in (item, said_1, said_2):
        assert _members_bytes(state[0], word.encode()) == []
    d.kill()
    again = start(state)
    assert again.digests() == [] and all(_members_bytes(state[0], w.encode()) == [] for w in (item, said_1, said_2))


def _undone_item(state, undo_seq: int):
    """The item of the action an undo receipt undid, read from the record (read-only)."""
    db = sqlite3.connect(f"file:{state[0] / 'kernel' / 'zoe-os.db'}?mode=ro", uri=True)
    try:
        (raw,) = db.execute("SELECT payload FROM events WHERE seq = ?", (undo_seq,)).fetchone()
        row = db.execute("SELECT payload FROM events WHERE seq = ?", (json.loads(raw)["undoes"],)).fetchone()
        return ((json.loads(row[0]) if row and row[0] else {}).get("undo") or {}).get("args", {}).get("item")
    finally:
        db.close()


@pytest.mark.parametrize("seed", [20261120, 7, 4242, 99, 31337])
def test_property_after_any_history_no_live_record_holds_forgotten_content_and_every_surviving_row_is_there(state, seed):
    """Random add / remove / undo / forget-add / forget-remove / forget-undo moves over two members (an add sometimes names the
    other member; a removal's request repeats the item's words), then a whole-person erase of B and a restart. Every add has its
    own marker. Afterwards: no forgotten marker in ANY file of the state directory or any row, every surviving row present, every
    removed row absent, and the rebuilt rows equal the running kernel's."""
    import random
    rnd = random.Random(seed)
    d = start(state)
    items: dict = {}                                   # item -> {"owner", "said", "names_b", "status"}
    removals: dict = {}                                # item -> its removal's request seq
    undos: list = []                                   # (undo receipt seq, item it reached)

    def forgotten(item):
        if item in items:
            items[item]["status"] = "forgotten"

    for n in range(40):
        person = rnd.choice((A, B))
        move = rnd.choice(("add", "add", "add", "remove", "remove", "undo", "forget_add", "forget_remove", "forget_undo"))
        mine = sorted(i for i, v in items.items() if v["owner"] == person and v["status"] == "present")
        try:
            if move == "add":
                item = f"synthetic marker <q{n}>"                    # delimited: no marker is a substring of another
                names_b = person == A and rnd.random() < 0.3
                out = _say_add(d, person, item, mentions=[B] if names_b else ())
                items[item] = {"owner": person, "said": out["said"], "names_b": names_b, "status": "present"}
            elif move == "remove" and mine:
                item = rnd.choice(mine)
                said = d.voice.publish("utterance", subject=person, payload={"text": f"take {item} off"})
                d.brain.call("act", action="list.remove", on_behalf_of=person,
                             args={"item": item, "list": "shopping", "asked": said.seq}, caused_by=[said.seq])
                items[item]["status"], removals[item] = "removed", said.seq
            elif move == "undo":
                out = d.panel.call("undo", person=person, actor=person)
                item = _undone_item(state, out["seq"])                 # read from the record: which action it undid
                if item in items and items[item]["status"] != "forgotten":
                    items[item]["status"] = "unknown"                  # undone: present or not, the comparison below decides
                undos.append((out["seq"], person, item))
            elif move == "forget_add":
                pick = sorted(i for i, v in items.items() if v["owner"] == person and v["status"] != "forgotten")
                if pick:
                    item = rnd.choice(pick)
                    d.panel.call("forget", subject=person, actor=person, seqs=[items[item]["said"]])
                    forgotten(item)
            elif move == "forget_remove":
                pick = sorted(i for i in removals if items[i]["owner"] == person and items[i]["status"] != "forgotten")
                if pick:
                    item = rnd.choice(pick)
                    d.panel.call("forget", subject=person, actor=person, seqs=[removals.pop(item)])
                    forgotten(item)
            elif move == "forget_undo" and undos:
                seq, who, item = undos.pop(rnd.randrange(len(undos)))
                d.panel.call("forget", subject=who, actor=who, seqs=[seq])
                forgotten(item)
        except Refused:
            pass
    d.eraser.call("erase_member", subject=B)
    for item, v in items.items():
        if v["owner"] == B or v["names_b"]:
            forgotten(item)
    running = d.digests()
    d.kill()
    again = start(state)
    held = again.digests()
    assert held == running and "unmatched" not in held
    gone = [i for i, v in items.items() if v["status"] == "forgotten"]
    for item in gone:
        assert digest(item) not in held and _members_bytes(state[0], item.encode()) == [], item
    for item, v in items.items():
        if v["status"] == "present":
            assert digest(item) in held, item
        elif v["status"] == "removed":
            assert digest(item) not in held, item
    assert len(gone) >= 3 and sum(v["status"] in ("present", "removed") for v in items.values()) >= 1   # both sides exercised


# ---- the durable reminder rows: the same guarantees ----------------------------------------------------------------------------
@pytest.mark.parametrize("die,present,uncertain", [("after_intent", False, 1), ("after_do", False, 1), ("after_commit", True, 0)])
def test_an_acknowledged_reminder_survives_a_kill_and_an_unacknowledged_one_is_absent_or_uncertain(state, die, present, uncertain):
    d = start(state, die=die)
    assert d.remind(A, note="kept", in_minutes=30)["verified"] is True
    with pytest.raises((ConnectionError, OSError, wire.WireError)):
        d.remind(A, note="die", time="20:00")
    assert d.proc.wait(timeout=10) == -9
    again = start(state)
    got = again.notes(A)
    assert "kept" in got and ("die" in got) is present
    assert again.brain.call("health")["recovered"] == uncertain
    assert again.remind(B, note="after", repeat="FREQ=DAILY")["verified"] is True and "after" in again.notes(B)


@pytest.mark.parametrize("die", ["after_intent", "after_do"])
def test_a_snooze_killed_before_its_receipt_is_not_half_applied(state, die):
    d = start(state, die=die)
    rid = d.remind(A, note="tea", time="20:00")["proof"]["id"]
    with pytest.raises((ConnectionError, OSError, wire.WireError)):
        d.remind(A, "remind.snooze", id=rid, minutes=13)
    d.proc.wait(timeout=10)
    row = start(state).notes(A)["tea"]
    assert row["snoozed_until"] is None and row["after"] < row["next_fire"]


def test_control_without_the_reminder_rebuild_an_acknowledged_reminder_is_lost_and_the_test_sees_it(state):
    d = start(state)
    assert d.remind(A, note="kept", in_minutes=30)["verified"] is True
    d.kill()
    assert "kept" not in start(state, control="no_rem_rebuild").notes(A)


def test_a_reminder_undo_after_a_restart_reverses_it_and_stays_reversed(state):
    d = start(state)
    rid = d.remind(A, note="bins", time="19:00")["proof"]["id"]
    d.remind(A, "remind.cancel", id=rid)
    d.kill()
    again = start(state)
    assert "bins" not in again.notes(A)
    assert again.panel.call("undo", person=A, actor=A)["verified"] is True and "bins" in again.notes(A)
    again.kill()
    assert "bins" in start(state).notes(A)


def test_erase_member_takes_their_reminders_and_another_members_edit_of_one_and_they_stay_gone(state):
    d = start(state)
    rid = d.remind(A, note="oat milk run", time="19:00", visibility="family")["proof"]["id"]
    d.remind(B, "remind.edit", id=rid, note="oat milk run and bread")          # B's receipt now carries A's words
    d.remind(B, note="rice", time="19:00")
    out = d.panel.call("erase_member", subject=A)
    assert out["verified"] is True
    assert set(d.notes(B)) == {"rice"} and not _on_disk(state[0], b"oat milk")
    d.kill()
    assert set(start(state).notes(B)) == {"rice"}


def test_an_entity_forget_through_the_old_op_also_reaches_the_reminder_rows(state):
    d = start(state)
    d.remind(A, note="gift for Dana", time="19:00")
    d.remind(A, note="eggs", time="19:00")
    d.panel.call("forget", subject=A, actor=A, entity="Dana")
    d.kill()
    assert set(start(state).notes(A)) == {"eggs"}


# ---- the forget rule over reminder rows: a reminder takes its later records; a cancel takes the reminder -----------------------
def test_forgetting_a_reminder_takes_its_edits_snoozes_and_cancel_and_their_bytes(state):
    note, edit = "synthetic marker <rem-a>", "synthetic marker <rem-a-edit>"
    d = start(state)
    made = d.remind(A, note=note, time="19:00")
    rid = made["proof"]["id"]
    d.remind(A, "remind.edit", id=rid, note=edit)
    d.remind(A, "remind.snooze", id=rid, minutes=10)
    assert _members_bytes(state[0], edit.encode())
    assert d.panel.call("forget", subject=A, actor=A, seqs=[made["said"]])["count"] >= 6    # the create, the edit, the snooze, requests
    for word in (note, edit):
        assert _members_bytes(state[0], word.encode()) == []
    assert d.notes(A) == {}
    d.kill()
    assert start(state).notes(A) == {}


def test_forgetting_a_cancel_takes_the_reminder_it_cancelled_and_no_rebuild_brings_it_back(state):
    note = "synthetic marker <rem-c>"
    d = start(state)
    rid = d.remind(A, note=note, time="19:00")["proof"]["id"]
    cancel = d.remind(A, "remind.cancel", id=rid)
    assert d.notes(A) == {}
    d.panel.call("forget", subject=A, actor=A, seqs=[cancel["said"]])                         # "forget that I cancelled it"
    assert d.notes(A) == {} and _members_bytes(state[0], note.encode()) == []
    d.kill()
    assert start(state).notes(A) == {} and _members_bytes(state[0], note.encode()) == []


def test_the_reminders_read_is_for_the_proven_person_only(state):
    from zoe_os import speaker
    d = start(state)
    d.remind(A, note="synthetic marker <a-own>", time="19:00")
    with pytest.raises(Refused, match="speaker unproven"):
        d.bare_panel.call("reminders", person=A)                                             # the panel's word alone
    with pytest.raises(Refused, match="speaker unproven"):                                    # B's proven word cannot read A's
        d.bare_panel.call("reminders", person=A, speaker=speaker.mint(d.speaker_key, B, "reminders", generation=d.generation))
    assert set(d.notes(A)) == {"synthetic marker <a-own>"} and d.notes(B) == {}


def test_a_carer_manages_an_adult_in_cares_reminders_except_private_ones_and_never_forgets_for_them(state):
    (state[0] / "people.json").write_text(json.dumps({"people": [A, {"id": B, "guardians": [A]}]}))   # B: an adult in A's care
    d = start(state)
    set_for = d.remind(A, note="synthetic marker <b-pills>", time="08:00", mentions=[B], **{"for": B})
    rid = set_for["proof"]["id"]
    assert set(d.notes(B)) == {"synthetic marker <b-pills>"} and set(d.notes(A)) == {"synthetic marker <b-pills>"}
    assert d.remind(A, "remind.snooze", id=rid, minutes=5)["verified"] is True              # the carer manages it
    own = d.remind(B, note="synthetic marker <b-private>", time="21:00", visibility="private")["proof"]["id"]
    assert "synthetic marker <b-private>" not in d.notes(A)                                   # private: not even the carer
    with pytest.raises(Refused, match="no such reminder"):
        d.remind(A, "remind.cancel", id=own)
    with pytest.raises(Refused, match="not theirs to forget"):                                # managing, never erasing
        d.panel.call("forget", subject=B, actor=A)
    with pytest.raises(Refused, match="not theirs to undo"):
        d.panel.call("undo", person=B, actor=A)
