"""Test rig for the process boundary: one kernel process and raw sockets to it."""
from __future__ import annotations

import glob
import json
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from zoe_os import wire
from zoe_os.skills.leaving_catch import LeavingCatch
from zoe_os.skills.malicious import Malicious

HERE = Path(__file__).parent
HELPER = Malicious.name
CARDS = {"skill": "cards", "reads": {"offer": "private"}}     # a surface that shows offers


class Box:
    """The kernel in its own process: database in one directory, socket in another."""

    def __init__(self, *, world_readable: bool = False, **server) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="zk-"))      # short: a socket path is <= 107 bytes
        self.base.chmod(0o711 if world_readable else 0o700)
        (self.base / "kernel").mkdir(mode=0o700)
        (self.base / "run").mkdir(mode=0o755)
        self.db, self.socket = str(self.base / "kernel/zoe-os.db"), str(self.base / "run/k.sock")
        self.cfg = {"db_dir": str(self.base / "kernel"), "socket": self.socket, "server": server,
                    "install": [Malicious.manifest, LeavingCatch.manifest, CARDS], "surfaces": ["panel"]}
        self.start()

    def start(self) -> None:
        self.proc = subprocess.Popen([sys.executable, "-B", str(HERE / "kernel_proc.py")],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        self.proc.stdin.write(json.dumps(self.cfg) + "\n")
        self.proc.stdin.flush()
        ready = json.loads(self.proc.stdout.readline())
        self.pid, self.creds = ready["pid"], ready["creds"]

    def sup(self, cmd: str, **more) -> dict:
        """The supervisor's pipe (not the socket)."""
        self.proc.stdin.write(json.dumps({"cmd": cmd, **more}) + "\n")
        self.proc.stdin.flush()
        return json.loads(self.proc.stdout.readline())

    def control(self, name: str) -> None:
        self.sup("control", name=name)

    def raw(self, name: str | None = None, cred: str | None = None) -> socket.socket:
        """A bare socket, past the kernel's hello. With ``name``: authenticated as that skill."""
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.connect(self.socket)
        self.nonce = bytes.fromhex(wire.read_frame(s, idle=5)["nonce"])    # this connection's
        if name:
            secret = bytes.fromhex(cred or self.creds[name])
            reply = ask(s, {"v": 1, "skill": name, "proof": wire.proof(secret, self.nonce)})
            assert reply.get("ok"), reply
        return s

    def audit(self, kind: str = "refusal") -> list[dict]:
        """Every event of one kernel kind, read the only way there is: through the socket."""
        s, out = self.raw("audit"), []
        sub = ask(s, req("subscribe", kinds=[kind], from_seq=0))["result"]["sub"]
        while batch := ask(s, req("poll", sub=sub, max=wire.MAX_POLL))["result"]["events"]:
            out += [e["payload"] for e in batch if e["kind"] == kind]   # FORGET reaches everyone
        s.close()
        return out

    def reasons(self, who: str | None = None) -> list[str]:
        return [r["reason"] for r in self.audit() if who in (None, r["who"])]

    def on_disk(self) -> bytes:
        return b"".join(open(p, "rb").read() for p in glob.glob(self.db + "*"))

    def close(self) -> None:
        self.proc.kill()
        self.proc.wait()
        shutil.rmtree(self.base, ignore_errors=True)


def frame(obj) -> bytes:
    body = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
    return struct.pack(">I", len(body)) + body


def ask(sock: socket.socket, obj, timeout: float = 5.0) -> dict | None:
    """Send one raw frame (an object or exact bytes), read one reply. None = closed on us."""
    try:
        sock.sendall(frame(obj))
        return wire.read_frame(sock, max_frame=wire.MAX_REPLY, idle=timeout)
    except (wire.WireError, OSError):
        return None


def req(op: str, **args) -> dict:
    return {"v": 1, "id": 1, "op": op, "args": args}


def until(check, timeout: float = 5.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if check():
            return True
        time.sleep(0.02)
    return bool(check())
