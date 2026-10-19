#!/usr/bin/env python3
"""Verify the model files against ``models.lock`` (read-only).

``models.lock`` (repo root) pins every model file Zoe runs on by sha256 - the brain, STT, TTS and
VAD on the Jetson, and the panel's wake word, wake features, VAD and speaker model. This checks
the files that are actually on disk: the Jetson's locally, the panel's over ``ssh`` with
``sha256sum`` (nothing is copied, nothing is written). Hashing the 4 GB brain GGUF takes ~20 s;
``--host panel`` skips the Jetson.

Exit 0 = every checked file matches, 1 = a mismatch or a missing file, 2 = the panel was unreachable.

    python3 scripts/maintenance/models_lock_verify.py               # both hosts
    python3 scripts/maintenance/models_lock_verify.py --host panel  # the panel only
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
LOCK = REPO / "models.lock"


def load_lock(path: Path = LOCK) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return list(data["models"])


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def check_local(entries: list[dict], home: Path) -> list[tuple[str, str]]:
    out = []
    for e in entries:
        p = home / e["path"]
        if not p.is_file():
            out.append((e["role"], "MISSING"))
            continue
        out.append((e["role"], "ok" if sha256_file(p) == e["sha256"] else "MISMATCH"))
    return out


def check_remote(entries: list[dict], host: str) -> list[tuple[str, str]]:
    paths = " ".join(shlex.quote(e["path"]) for e in entries)
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", host,
                        f"sha256sum {paths} 2>/dev/null; true"],
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(f"ssh {host} failed (exit {r.returncode})")
    got = {}
    for line in r.stdout.splitlines():
        digest, _, path = line.partition("  ")
        got[path.strip()] = digest.strip()
    return [(e["role"], "MISSING" if e["path"] not in got else ("ok" if got[e["path"]] == e["sha256"] else "MISMATCH"))
            for e in entries]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", choices=("all", "jetson", "panel"), default="all")
    ap.add_argument("--panel-ssh", default="zoe-pi", help="ssh target for the panel (default zoe-pi)")
    ap.add_argument("--lock", type=Path, default=LOCK)
    args = ap.parse_args(argv)
    entries = load_lock(args.lock)
    results: list[tuple[str, str, str]] = []
    if args.host in ("all", "jetson"):
        results += [("jetson", r, s) for r, s in check_local([e for e in entries if e["host"] == "jetson"], Path.home())]
    if args.host in ("all", "panel"):
        try:
            results += [("panel", r, s) for r, s in check_remote([e for e in entries if e["host"] == "panel"],
                                                                 args.panel_ssh)]
        except (RuntimeError, subprocess.TimeoutExpired) as exc:
            print(f"panel unreachable: {exc}", file=sys.stderr)
            return 2
    bad = 0
    for host, role, status in results:
        bad += status != "ok"
        print(f"{status:9} {host:7} {role}")
    print("all model files match models.lock" if not bad else f"{bad} model file(s) differ from models.lock")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
