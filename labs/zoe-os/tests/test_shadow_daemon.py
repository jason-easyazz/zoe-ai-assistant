"""The shadow daemon (``shadow_daemon.py``) proven on its own wire, with the lab's own ``Client``: no production client needed.

zoe-data's contract test (``services/zoe-data/tests/test_os_shadow_contract.py``) skips when ``os_shadow.py`` is absent; this file
proves the daemon's side in a plain checkout: a forget leaves none of the member's words in any file of the state directory (the
contract test's byte-scan pattern), ``known-people.json`` follows the record, and the roster at start never stops forget:
an EMPTY roster (the last member removed) or an unreadable one starts the daemon with nobody granted and forget still served; only
a MISSING roster refuses the start. Each daemon is its own process on a temp state directory. Synthetic members and words only.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from zoe_os.client import Client
from zoe_os.spine import Refused

HERE = Path(__file__).parent
DAEMON = HERE.parent / "shadow_daemon.py"
A, B = "member-sa", "member-sb"
MARK = b"synthetic persimmon marker"


class Shadow:
    def __init__(self, state: Path) -> None:
        self.proc = subprocess.Popen([sys.executable, "-B", str(DAEMON), "--dir", str(state)], stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True, env={"PATH": os.environ["PATH"], "HOME": str(state)})
        line = self.proc.stdout.readline()
        assert line.startswith("zoe-os shadow kernel up"), self.proc.stderr.read()
        raw = json.loads((state / "creds.json").read_text())
        self.voice, self.brain, self.panel = (Client(raw["socket"], s, bytes.fromhex(raw["creds"][s]), timeout=5)
                                              for s in ("voice", "brain", "panel"))

    def add(self, person: str, item: str) -> dict:
        said = self.voice.publish("utterance", subject=person, payload={"text": item})
        return self.brain.call("act", action="list.add", on_behalf_of=person,
                               args={"item": item, "list": "shopping", "asked": said.seq}, caused_by=[said.seq])

    def stop(self) -> str:
        self.proc.kill()
        _, err = self.proc.communicate(timeout=10)
        return err


def settled(path: Path) -> None:
    """A state directory as the install tool leaves it, a minute later: its settle marker is old enough (no real wait)."""
    from zoe_os import shadow_state
    wall, mono, boot = shadow_state.clock()
    shadow_state.write_marker(str(path), wall - 60, mono - 60, boot, shadow_state.SETTLE_S)


def test_the_daemon_refuses_a_directory_that_was_copied_in_by_hand():
    """Data with no settle marker (a raw copy, a restore outside the tool): the kernel says why and does not serve it."""
    path = Path(tempfile.mkdtemp(prefix="zd"))
    path.chmod(0o700)
    (path / "people.json").write_text(json.dumps({"people": [A, B]}))
    proc = subprocess.run([sys.executable, "-B", str(DAEMON), "--dir", str(path)], capture_output=True, text=True, timeout=30,
                          env={"PATH": os.environ["PATH"], "HOME": str(path)})
    assert proc.returncode != 0 and "refusing to start" in proc.stderr and "kernel up" not in proc.stdout
    assert not (path / "creds.json").exists() and not (path / "kernel").exists()


@pytest.fixture
def state():
    path = Path(tempfile.mkdtemp(prefix="zd"))           # short: a socket path is <= 107 bytes
    path.chmod(0o700)
    settled(path)
    (path / "people.json").write_text(json.dumps({"people": [A, B]}))
    running: list[Shadow] = []
    yield path, running
    for d in running:
        if d.proc.poll() is None:
            d.stop()


def start(st) -> Shadow:
    d = Shadow(st[0])
    st[1].append(d)
    return d


def holding(state: Path, word: bytes) -> list:
    """Every file of the state directory (the database, its WAL and SHM, the daemon's own files) that holds ``word``."""
    return sorted(f.name for f in state.rglob("*") if f.is_file() and f.name != "creds.json" and word in f.read_bytes())


def known(state: Path) -> list:
    return json.loads((state / "known-people.json").read_text())["people"]


def test_a_forget_on_the_wire_leaves_none_of_the_members_words_in_any_file_and_keeps_the_other_member(state):
    d = start(state)
    assert d.add(A, MARK.decode())["verified"] is True
    assert d.add(B, "synthetic other words")["verified"] is True
    assert holding(state[0], MARK)                                     # the control of the scan: the words are there
    assert d.panel.call("forget", subject=A, actor=A)["count"] >= 2
    assert holding(state[0], MARK) == []
    assert holding(state[0], b"synthetic other words")                 # B's record is untouched
    assert known(state[0]) == sorted([A, B])                           # A is still on the roster: still a member
    with pytest.raises(Refused, match="trusted surface"):
        d.brain.call("forget", subject=B, actor=B)                     # a skill cannot speak for a person


def test_a_forgotten_member_still_on_the_roster_acts_again_and_is_recorded_afresh_without_a_restart(state):
    """Forgetting a member's data must not lock them out. The forget erases their record-only grant with the rest; their NEXT act
    gets it back (not before: until then nothing names them), in the SAME daemon. The kernel's own content shield still holds:
    the very words they had forgotten are refused, new ones are recorded. The control: a member who is NOT on the roster gets
    nothing back."""
    d = start(state)
    assert d.add(A, MARK.decode())["verified"] is True
    assert d.panel.call("forget", subject=A, actor=A)["count"] >= 2
    assert holding(state[0], A.encode()) in ([], ["known-people.json", "people.json"], ["people.json"])    # nothing in the record
    again = d.add(A, "synthetic quince words")
    assert again["verified"] is True and again["on_behalf_of"] == A        # recorded afresh, no restart
    with pytest.raises(Refused):
        d.add(A, MARK.decode())                                            # the forgotten words themselves stay forgotten
    assert d.panel.call("forget", subject=A, actor=A)["count"] >= 2        # ... and what they did since is forgettable again
    assert d.add(A, "synthetic medlar words")["verified"] is True
    assert d.add(B, "synthetic other words")["verified"] is True           # B was never touched
    from zoe_os.wire import WireError
    with pytest.raises(WireError):
        d.add("member-nobody", "synthetic stranger words")                 # control: not a member: not even a person here


def test_a_member_taken_off_the_roster_stays_forgettable_until_forgotten_then_leaves_known_people(state):
    d = start(state)
    d.add(A, MARK.decode())
    d.stop()
    (state[0] / "people.json").write_text(json.dumps({"people": [B]}))
    again = start(state)
    assert known(state[0]) == sorted([A, B])                           # the record still names A
    with pytest.raises(Refused, match="no grant"):
        again.add(A, "anything")                                       # forgettable, never proposable
    assert again.panel.call("forget", subject=A, actor=A)["count"] >= 2
    assert known(state[0]) == [B] and holding(state[0], MARK) == []
    with pytest.raises(Refused, match="only a person can ask to forget"):
        again.panel.call("forget", subject=A, actor=A)                 # the kernel no longer knows them (zoe-data reads: none held)
    again.stop()
    start(state)
    assert known(state[0]) == [B]                                      # a restart does not bring them back


@pytest.mark.parametrize("roster", ['{"people": []}', "not json at all", '{"people": [7]}', '{"members": []}'])
def test_with_the_last_member_removed_or_an_unreadable_roster_the_daemon_starts_forgets_and_acts_for_nobody(state, roster):
    d = start(state)
    d.add(A, MARK.decode())
    d.stop()
    (state[0] / "people.json").write_text(roster)                      # the last member left (or the file is damaged)
    again = start(state)
    # loaded from the record, for forget only: A (their words) and B (B's grant record names them; main's forget reaches it)
    assert known(state[0]) == sorted([A, B])
    said = again.voice.publish("utterance", subject=A, payload={"text": "synthetic"})
    with pytest.raises(Refused, match="no grant"):                     # nothing can be proposed or acted for anyone
        again.brain.call("act", action="list.add", on_behalf_of=A, args={"item": "x", "asked": said.seq},
                         caused_by=[said.seq])
    assert holding(state[0], MARK)
    assert again.panel.call("forget", subject=A, actor=A)["count"] >= 2
    assert holding(state[0], MARK) == [] and known(state[0]) == [B]
    assert again.panel.call("forget", subject=B, actor=B)["count"] >= 1  # B's grant record: reached, and B leaves too
    assert known(state[0]) == []
    err = again.stop()
    unreadable = "zoe-os shadow kernel: people.json is unreadable: nobody is granted anything; forget of recorded people is served"
    assert (unreadable in err) is (roster != '{"people": []}')         # one fixed line, and only for a damaged roster
    assert MARK.decode() not in err


def test_a_missing_roster_file_is_an_operator_error_and_refuses_the_start(state):
    (state[0] / "people.json").unlink()
    p = subprocess.run([sys.executable, "-B", str(DAEMON), "--dir", str(state[0])], capture_output=True, text=True, timeout=30,
                       env={"PATH": os.environ["PATH"], "HOME": str(state[0])})
    assert p.returncode != 0 and "people.json is missing" in p.stderr
