"""Shared lab helpers: run as a lab uid, the members' own bw clients (measured), cells, the witness, canary scans."""

from __future__ import annotations

import json
import os
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, "/opt/cvlab/lab")
from hosts import VAULT_URL, WITNESS  # noqa: E402

STATE = Path("/run/cvlab/state.json")
SECRETS = Path("/run/cvlab-secrets")
CA = "/pki/ca.crt"
RESULTS: list[dict] = []
MEASURE: dict = {}


def cell(name: str, ok: bool, detail: str = "", *, nc: str | None = None) -> bool:
    """One CELL line. `nc` is the negative-control verdict text. A cell whose control did NOT go red is a FAIL,
    whatever its positive half says: a protection we cannot break on purpose is not a proven protection."""
    if nc and ("NOT red" in nc or "did NOT go red" in nc):
        ok = False
    RESULTS.append({"cell": name, "pass": bool(ok), "detail": detail, "nc": nc})
    print(f"CELL {'PASS' if ok else 'FAIL'} {name} :: {detail}" + (f" || NC: {nc}" if nc else ""), flush=True)
    return ok


def emit_result(phase: str) -> None:
    out = {"phase": phase, "cells": RESULTS, "measure": MEASURE,
           "pass": sum(1 for r in RESULTS if r["pass"]), "total": len(RESULTS)}
    print("CVLAB-RESULT " + json.dumps(out), flush=True)


def as_user(user: str, argv: list[str], *, env: dict | None = None, input: str | None = None,
            timeout: float = 180) -> subprocess.CompletedProcess:
    base_env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8"}
    base_env.update(env or {})
    cmd = ["setpriv", f"--reuid={user}", f"--regid={user}", "--init-groups", "--",
           "env", "-i", *[f"{k}={v}" for k, v in base_env.items()], *argv]
    return subprocess.run(cmd, input=input, capture_output=True, text=True, timeout=timeout)


def spawn_as(user: str, argv: list[str], *, env: dict | None = None, log: str | None = None) -> subprocess.Popen:
    base_env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8", "PYTHONPATH": "/opt/zoe-vaultd"}
    base_env.update(env or {})
    out = open(log, "ab") if log else subprocess.DEVNULL
    cmd = ["setpriv", f"--reuid={user}", f"--regid={user}", "--init-groups", "--",
           "env", "-i", *[f"{k}={v}" for k, v in base_env.items()], *argv]
    return subprocess.Popen(cmd, stdout=out, stderr=out, start_new_session=True)


def write_secret(path: Path, value: str, owner: str) -> None:
    path.parent.mkdir(mode=0o711, parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(value + "\n")
    import pwd
    pw = pwd.getpwnam(owner)
    os.chown(path, pw.pw_uid, pw.pw_gid)
    os.chmod(path, 0o600)


class Bw:
    """A `bw` CLI client as a given uid with its own data dir. Every call is timed and its peak RSS recorded."""

    def __init__(self, name: str, user: str, appdata: str, email: str, pwfile: str) -> None:
        self.name, self.user, self.appdata, self.email, self.pwfile = name, user, appdata, email, pwfile
        self.session: str | None = None
        self.calls: list[dict] = []
        self.stderr_log: list[str] = []

    def run(self, *args: str, stdin: str | None = None, session: bool = True, label: str | None = None
            ) -> subprocess.CompletedProcess:
        env = {"HOME": self.appdata, "BITWARDENCLI_APPDATA_DIR": self.appdata, "NODE_EXTRA_CA_CERTS": CA,
               "BW_NOINTERACTION": "true"}
        if session and self.session:
            env["BW_SESSION"] = self.session
        tf = tempfile.NamedTemporaryFile(prefix="t-", dir="/run/cvlab-time", delete=False)
        tf.close()
        os.chmod(tf.name, 0o666)
        r = as_user(self.user, ["/usr/bin/time", "-f", "%e %M", "-o", tf.name, "bw", *args, "--nointeraction"],
                    env=env, input=stdin)
        try:
            secs, kb = Path(tf.name).read_text().strip().splitlines()[-1].split()
            self.calls.append({"cmd": label or args[0], "s": float(secs), "rss_mb": round(int(kb) / 1024, 1),
                               "rc": r.returncode})
        except (ValueError, IndexError):
            self.calls.append({"cmd": label or args[0], "rc": r.returncode})
        os.unlink(tf.name)
        self.stderr_log.append(r.stderr)
        return r

    def login(self) -> None:
        if self.run("config", "server", VAULT_URL, session=False, label="config").returncode != 0:
            raise RuntimeError(f"{self.name}: bw config server failed")
        r = self.run("login", self.email, "--passwordfile", self.pwfile, "--raw", session=False, label="login")
        if r.returncode != 0 or not r.stdout.strip():
            raise RuntimeError(f"{self.name}: bw login failed rc={r.returncode} {r.stderr[-200:]}")
        self.session = r.stdout.strip()

    def unlock(self) -> bool:
        r = self.run("unlock", "--passwordfile", self.pwfile, "--raw", session=False, label="unlock")
        if r.returncode == 0 and r.stdout.strip():
            self.session = r.stdout.strip()
            return True
        return False

    def sync(self) -> bool:
        return self.run("sync", label="sync").returncode == 0

    def items(self, *flags: str) -> list[dict]:
        r = self.run("list", "items", *flags, label="list items")
        if r.returncode != 0:
            return []
        return json.loads(r.stdout or "[]")

    def get(self, item_id: str) -> dict | None:
        r = self.run("get", "item", item_id, label="get item")
        return json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else None

    def encode(self, obj) -> str:
        import base64
        return base64.b64encode(json.dumps(obj).encode()).decode()


def https_json(method: str, url: str, body=None):
    ctx = ssl.create_default_context(cafile=CA)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, context=ctx, timeout=30) as r:
        return json.loads(r.read() or b"null")


def witness_records() -> list[dict]:
    return https_json("GET", f"https://{WITNESS}/__lab/records")


def witness_reset() -> None:
    https_json("POST", f"https://{WITNESS}/__lab/reset", {})


def scan_paths(paths: list[str], needles: list[str]) -> list[str]:
    """Every file under `paths` that contains any needle (bytes match)."""
    hits = []
    bneedles = [n.encode() for n in needles if n]
    for root in paths:
        p = Path(root)
        files = [p] if p.is_file() else [f for f in p.rglob("*") if f.is_file()] if p.exists() else []
        for f in files:
            try:
                data = f.read_bytes()
            except OSError:
                continue
            if any(n in data for n in bneedles):
                hits.append(str(f))
    return hits


def wait_for(fn, timeout: float, step: float = 0.5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        v = fn()
        if v:
            return v
        time.sleep(step)
    return None


def load_state() -> dict:
    return json.loads(STATE.read_text())


def save_state(state: dict) -> None:
    STATE.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(STATE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(state, fh)
