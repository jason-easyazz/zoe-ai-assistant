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

    def add(self, person: str, item: str) -> dict:
        said = self.voice.publish("utterance", subject=person, payload={"text": "synthetic list add"})
        return self.brain.call("act", action="list.add", on_behalf_of=person,
                               args={"item": item, "list": "shopping", "asked": said.seq}, caused_by=[said.seq])

    def remind(self, person: str, action: str = "remind.set", **args) -> dict:
        said = self.voice.publish("utterance", subject=person, payload={"text": "synthetic reminder"})
        return self.brain.call("act", action=action, on_behalf_of=person, args={**args, "asked": said.seq}, caused_by=[said.seq])

    def notes(self, person: str) -> dict:
        return {r["note"]: r for r in self.panel.call("reminders", person=person)["reminders"]}

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
    out = d.panel.call("erase_member", subject=A)
    assert out["verified"] is True and out["erased"] >= 4 and isinstance(out["receipt_seq"], int)
    assert d.digests() == [digest("rice")]
    assert not _on_disk(state[0], b"oat milk")                       # the words left the database and its WAL
    twice = d.panel.call("erase_member", subject=A)
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
    assert again.panel.call("erase_member", subject="nobody")["verified"] is True     # never acted for: nothing held, nothing to do


def test_control_an_erase_that_skips_the_forget_is_caught_by_its_read_back(state):
    d = start(state, control="no_member_forget")
    d.add(A, "oat milk")
    assert d.panel.call("erase_member", subject=A)["verified"] is False     # fail closed: the caller must not say "deleted"


def test_erase_item_forgets_one_item_for_every_member_by_digest_only(state):
    d = start(state)
    d.add(A, "oat milk")
    d.add(B, "Oat Milk")
    d.add(A, "eggs")
    out = d.panel.call("erase_item", digest=digest("oat milk"))
    assert out["verified"] is True and out["erased"] >= 4
    assert d.digests() == [digest("eggs")]
    assert not _on_disk(state[0], b"oat milk") and not _on_disk(state[0], b"Oat Milk")
    assert d.panel.call("erase_item", digest=digest("oat milk"))["erased"] == 0          # idempotent
    with pytest.raises(Refused):
        d.brain.call("erase_item", digest=digest("eggs"))
    d.kill()
    assert start(state).digests() == [digest("eggs")]


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
def test_the_database_lives_where_db_says_and_an_unmoved_old_record_stops_the_start(state):
    perm = state[0] / "perm" / "zoe-os.db"
    d = start(state, db=str(perm))
    d.add(A, "kept")
    d.kill()
    assert perm.is_file() and not (state[0] / "kernel" / "zoe-os.db").exists()
    assert start(state, db=str(perm)).digests() == [digest("kept")]
    old = start(state)                                               # the lab default: STATE/kernel/zoe-os.db
    old.add(A, "old")
    old.kill()
    p = subprocess.run([sys.executable, "-B", str(HERE / "authority_proc.py"), "--dir", str(state[0]), "--db", str(state[0] / "new.db")],
                       capture_output=True, text=True, timeout=30)
    assert p.returncode != 0 and "was not moved" in p.stderr and not (state[0] / "new.db").exists()


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
