"""A kernel process, a brain bridge, and the trusted surfaces, for the brain-bridge tests."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from world import ALEX, KID, SAM

from zoe_os.brain.bridge import BrainBridge, Turn
from zoe_os.client import Client

HERE = Path(__file__).parent


class BrainBox:
    def __init__(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="zb-"))
        (self.base / "kernel").mkdir(mode=0o700)
        (self.base / "run").mkdir()
        self.socket = str(self.base / "run/k.sock")
        self.proc: subprocess.Popen | None = None
        self.start()

    def start(self) -> None:
        self.proc = subprocess.Popen([sys.executable, "-B", str(HERE / "brain_proc.py")],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        self.proc.stdin.write(json.dumps({"db_dir": str(self.base / "kernel"), "socket": self.socket}) + "\n")
        self.proc.stdin.flush()
        ready = json.loads(self.proc.stdout.readline())
        self.creds, self.recovered = ready["creds"], ready["recovered"]
        self.speaker_key = bytes.fromhex(ready["speaker_key"])     # the test plays the speaker-verifier
        self.speaker_generation = ready["speaker_generation"]
        self._clients: dict[str, Client] = {}

    def sup(self, cmd: str) -> dict:
        self.proc.stdin.write(json.dumps({"cmd": cmd}) + "\n")
        self.proc.stdin.flush()
        return json.loads(self.proc.stdout.readline())

    def client(self, name: str) -> Client:
        if name not in self._clients:
            panel = name == "panel"                                    # only the surface relays a person's word
            self._clients[name] = Client(self.socket, name, bytes.fromhex(self.creds[name]),
                                         speaker_key=self.speaker_key if panel else None,
                                         speaker_generation=self.speaker_generation if panel else None)
        return self._clients[name]

    def bridge(self, **kw) -> BrainBridge:
        return BrainBridge(self.socket, bytes.fromhex(self.creds["brain"]), **kw)

    def household(self) -> None:
        """Each person says yes to the brain through the panel: alex and sam to all of it, the
        child (with a guardian) to lists and reminders but not alarms."""
        panel = self.client("panel")
        panel.grant(ALEX, "brain")
        panel.grant(SAM, "brain")
        panel.grant(KID, "brain", guardian=ALEX, acts=["list.add", "remind.set"])

    def say(self, person: str, text: str) -> Turn:
        """The voice surface records an utterance; the turn is (who, which utterance)."""
        ev = self.client("voice").publish("utterance", subject=person, payload={"text": text})
        return Turn(person, ev.seq)

    def kill(self) -> None:
        self.proc.kill()
        self.proc.wait()

    def restart(self) -> None:
        self.kill()
        self.start()

    def close(self) -> None:
        for c in self._clients.values():
            c.close()
        self.proc.kill()
        self.proc.wait()
        shutil.rmtree(self.base, ignore_errors=True)
