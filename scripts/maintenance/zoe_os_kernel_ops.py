"""Stdlib operations for the optional kernel; never import a lab or print private data."""
from __future__ import annotations

import gzip
import hashlib
import hmac
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import struct
import sys
import tempfile
import time


def health(state: Path, read_only: bool = False) -> bool:
    # Wire v1: bounded frames and a nonce proof; credentials never leave the host.
    creds = json.loads((state / "creds.json").read_text())
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        sock.connect(str(state / "k.sock"))

        def receive():
            def exact(n):
                out = b""
                while len(out) < n:
                    chunk = sock.recv(n - len(out))
                    if not chunk:
                        raise ConnectionError
                    out += chunk
                return out
            size = struct.unpack(">I", exact(4))[0]
            if size > 65536:
                raise ValueError("oversized frame")
            return json.loads(exact(size))

        def send(obj):
            body = json.dumps(obj).encode()
            sock.sendall(struct.pack(">I", len(body)) + body)

        hello = receive()
        if hello.get("v") != 1:
            return False
        proof = hmac.new(bytes.fromhex(creds["creds"]["voice"]), bytes.fromhex(hello["nonce"]), hashlib.sha256).hexdigest()
        send({"v": 1, "skill": "voice", "proof": proof})
        if receive().get("ok") is not True:
            return False
        send({"v": 1, "id": 1, "op": "health", "args": {"read_only": 1} if read_only else {}})
        reply = receive()
        result = reply.get("result", {})
        return (reply.get("v") == 1 and reply.get("id") == 1 and reply.get("ok") is True
                and result.get("alive") is True and result.get("schema") == 1
                and (read_only or result.get("last_write_ok") is True))


def check_database(path: Path) -> None:
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
        if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise ValueError("integrity")
        if not db.execute("SELECT value FROM meta WHERE key = 'shield_key'").fetchone():
            raise ValueError("not a kernel database")


def snapshot(source: Path, destination: Path, progress=None) -> None:
    # mode=ro includes committed WAL frames; immutable=1 would silently omit them.
    with sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True) as src:
        with sqlite3.connect(destination) as dst:
            src.backup(dst, pages=64, progress=progress)
    check_database(destination)


def backup(source: Path, root: Path) -> None:
    if not source.is_file():
        return
    os.umask(0o077)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    root.chmod(0o700)
    # Unique work dir, atomic publish; failures never rotate a last good copy.
    with tempfile.TemporaryDirectory(prefix=".kernel-", dir=root) as tmp:
        snap = Path(tmp) / "snapshot.db"
        snapshot(source, snap)
        packed = Path(tmp) / "snapshot.db.gz"
        with snap.open("rb") as src, gzip.open(packed, "wb", compresslevel=9) as dst:
            shutil.copyfileobj(src, dst)
        stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
        out = root / f"zoe-os-{stamp}-{time.time_ns()}.db.gz"
        os.replace(packed, out)
    copies = sorted(root.glob("zoe-os-*.db.gz"), key=lambda p: p.stat().st_mtime, reverse=True)
    for i, old in enumerate(copies):
        if i >= 7 or time.time() - old.stat().st_mtime > 35 * 86400:
            old.unlink()
    print("kernel backup: snapshot verified, compressed; keeping 7 copies, maximum age 35 days")


def drill(root: Path, scratch: Path, required: bool, now: float) -> tuple[list[str], list[str]]:
    copies = list(root.glob("zoe-os-*.db.gz"))
    if not copies:
        return (["kernel: no backup"] if required else [], [])
    latest = max(copies, key=lambda p: p.stat().st_mtime)
    findings = ["kernel: newest backup older than 26 h"] if now - latest.stat().st_mtime > 26 * 3600 else []
    try:
        with tempfile.TemporaryDirectory(prefix="kernel-", dir=scratch) as tmp:
            restored = Path(tmp) / "restored.db"
            with gzip.open(latest, "rb") as src, restored.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            check_database(restored)
    except (OSError, EOFError, sqlite3.Error, ValueError):
        return findings + ["kernel: backup restore or integrity check failed"], []
    return findings, ["kernel: backup restored to scratch; integrity and kernel marker verified"]


if __name__ == "__main__":
    try:
        if sys.argv[1] == "health":
            raise SystemExit(0 if health(Path(sys.argv[2]), sys.argv[3] == "read") else 1)
        if sys.argv[1] == "backup":
            backup(Path(sys.argv[2]), Path(sys.argv[3]))
        elif sys.argv[1] == "snapshot":
            os.umask(0o077)
            snapshot(Path(sys.argv[2]), Path(sys.argv[3]))
        else:
            raise ValueError("operation")
    except Exception:
        print("kernel operation failed", file=sys.stderr)
        raise SystemExit(1)
