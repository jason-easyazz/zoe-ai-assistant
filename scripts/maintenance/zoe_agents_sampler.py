#!/usr/bin/env python3
"""Sample the engineering cgroups — one JSON line per run, appended to a JSONL.

Why this exists (docs/research/agent-sessions-off-box-2026-10-04.md, section 5): every
recorded RAM incident on this box since July is an agent-tooling incident, and each class fix
bounded a COMPONENT. This answers the question none of them did — how much does engineering
cost on the product box over a day, and is anything running OUTSIDE the bound? Install it
first (baseline week, nothing else installed), then the slice a week later: the two weeks
compare like for like. Runbook: docs/knowledge/engineering-off-box.md.

Records, every run:
  * MemAvailable and swap used (kB);
  * zoe-agents.slice: memory.current / swap.current / memory.events, and the same for each
    direct member (units and scopes);
  * the Omnigent container's cgroup (found through the `omnigent server` process, no docker
    CLI) — it cannot join the slice, so it is measured beside it;
  * for each engineering process kind (serena, jedi-language-server, codebase-memory-mcp,
    ccd-cli, codex, claude): how many run, how many are INSIDE zoe-agents.slice, how many are
    OUTSIDE it, how many live in a container (reported, never counted as outside);
  * the session lease: which pid /proc/locks says holds the lock file, and the holder file.

THE "OUTSIDE" RULE (the class this guards): a process is inside the slice iff `zoe-agents.slice`
is a whole path COMPONENT of its /proc/<pid>/cgroup — never a substring (`zoe-agents.slice.bak`
or an argv mentioning the name must not count). Processes are matched by argv[0] / the script
argv[1], never by a substring of the whole command line: a `ccd-cli` session's argv carries
prompts and tool lists that mention every tool name. Container processes share the host uid and
show up in the host /proc; they are bucketed `container`, not `outside`.

No PII by construction: only counts, byte figures, unit/scope names and pids. No command lines,
no paths from the home directory, no environment, no file contents. Stdlib only, Python 3.10.
A missing file is a null, not a crash — the sampler must never be the thing that fails.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

SLICE = "zoe-agents.slice"
LEASE_LOCK = "zoe-agent-session.lock"
LEASE_HOLDER = "zoe-agent-session.holder"
ROTATE_BYTES = 8 * 1024 * 1024

# Engineering process kinds, in report order. Matching is on argv[0]/argv[1] only.
KINDS = ("serena", "jedi-language-server", "codebase-memory-mcp", "ccd-cli", "codex", "claude")
_INTERPRETER = re.compile(r"(python[0-9.]*|node|bash|sh)")


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text()
    except (OSError, ValueError):
        return None


def _read_int(path: Path) -> int | None:
    """Integer cgroup/proc value; None when missing or the literal `max`."""
    text = _read_text(path)
    if text is None:
        return None
    text = text.strip()
    return int(text) if text.isdigit() else None


def _read_kv(path: Path) -> dict[str, int]:
    out: dict[str, int] = {}
    for line in (_read_text(path) or "").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].isdigit():
            out[parts[0]] = int(parts[1])
    return out


def meminfo(path: Path) -> dict[str, int | None]:
    vals: dict[str, int] = {}
    for line in (_read_text(path) or "").splitlines():
        parts = line.replace(":", " ").split()
        if len(parts) >= 2 and parts[1].isdigit():
            vals[parts[0]] = int(parts[1])
    swap = None
    if "SwapTotal" in vals and "SwapFree" in vals:
        swap = vals["SwapTotal"] - vals["SwapFree"]
    return {"mem_available_kb": vals.get("MemAvailable"), "swap_used_kb": swap}


# ---------------------------------------------------------------- process classification


def _base(arg: str) -> str:
    return arg.rsplit("/", 1)[-1]


def classify(argv: list[str]) -> str | None:
    """Engineering kind of a process from its argv, or None. argv[0] / script argv[1] only."""
    if not argv or not argv[0]:
        return None
    a0 = argv[0]
    b0 = _base(a0)
    script = _base(argv[1]) if len(argv) > 1 else ""
    if "ccd-cli" in a0.split("/"):
        return "ccd-cli"  # …/remote/ccd-cli/<version>: the version is the basename
    if b0 == "codebase-memory-mcp":
        return "codebase-memory-mcp"
    interp = _INTERPRETER.fullmatch(b0) is not None
    for kind in ("serena", "jedi-language-server", "codex", "claude"):
        if b0 == kind or (interp and script == kind):
            return kind
    return None


def cgroup_path(proc: Path) -> str | None:
    """The unified (cgroup v2) path from /proc/<pid>/cgroup, e.g. /user.slice/…/x.scope."""
    text = _read_text(proc / "cgroup")
    if text is None:
        return None
    for line in text.splitlines():
        if line.startswith("0::"):
            return line[3:].strip()
    return None


def in_slice(path: str | None) -> bool:
    """Whole-component match: `zoe-agents.slice` must be a path element, nothing looser."""
    return bool(path) and SLICE in path.strip("/").split("/")


def in_container(path: str | None) -> bool:
    if not path:
        return False
    for comp in path.strip("/").split("/"):
        if comp == "docker" or comp.startswith(("docker-", "libpod-", "cri-containerd-")):
            return True
    return False


def scan_processes(proc_root: Path) -> tuple[dict[str, dict[str, int]], dict]:
    """Walk /proc once. Returns (per-kind counts, extras: omnigent cgroup path, unreadable)."""
    counts = {k: {"total": 0, "in_slice": 0, "outside_slice": 0, "container": 0} for k in KINDS}
    omnigent_cg: str | None = None
    unreadable = 0
    try:
        entries = [p for p in proc_root.iterdir() if p.name.isdigit()]
    except OSError:
        return counts, {"omnigent_cgroup": None, "unreadable": 0}
    for p in entries:
        try:
            raw = (p / "cmdline").read_bytes()
        except OSError:
            unreadable += 1  # exited between listdir and read, or not ours
            continue
        argv = [a.decode("utf-8", "replace") for a in raw.split(b"\0") if a]
        kind = classify(argv)
        is_omnigent = (
            len(argv) > 2
            and _base(argv[0]).startswith("python")
            and _base(argv[1]) == "omnigent"
            and argv[2] == "server"
        )
        if kind is None and not is_omnigent:
            continue
        cg = cgroup_path(p)
        if is_omnigent and omnigent_cg is None and in_container(cg):
            omnigent_cg = cg
        if kind is None:
            continue
        c = counts[kind]
        if cg is None:
            unreadable += 1
            continue
        if in_container(cg):
            c["container"] += 1
            continue
        c["total"] += 1
        c["in_slice" if in_slice(cg) else "outside_slice"] += 1
    return counts, {"omnigent_cgroup": omnigent_cg, "unreadable": unreadable}


# ---------------------------------------------------------------------- cgroup figures


def _cg_figures(d: Path) -> dict:
    return {
        "memory_current": _read_int(d / "memory.current"),
        "swap_current": _read_int(d / "memory.swap.current"),
    }


def slice_dir(cgroup_root: Path, uid: int) -> Path:
    return cgroup_root / "user.slice" / f"user-{uid}.slice" / f"user@{uid}.service" / SLICE


def slice_report(cgroup_root: Path, uid: int) -> dict:
    d = slice_dir(cgroup_root, uid)
    if not d.is_dir():
        return {"present": False}
    rep: dict = {"present": True, **_cg_figures(d)}
    rep["memory_max"] = _read_int(d / "memory.max")
    rep["swap_max"] = _read_int(d / "memory.swap.max")
    events = _read_kv(d / "memory.events")
    rep["events"] = {k: events.get(k) for k in ("high", "max", "oom", "oom_kill")}
    members = []
    try:
        children = sorted(c for c in d.iterdir() if c.is_dir())
    except OSError:
        children = []
    for c in children:
        if (c / "memory.current").exists():
            members.append({"name": c.name, **_cg_figures(c)})
    rep["members"] = members
    return rep


def omnigent_report(cgroup_root: Path, cg_path: str | None) -> dict:
    if not cg_path:
        return {"found": False}
    d = cgroup_root / cg_path.strip("/")
    if not d.is_dir():
        return {"found": False}
    return {"found": True, **_cg_figures(d)}


# ------------------------------------------------------------------------------ lease


def lease_report(runtime_dir: Path, proc_locks: Path) -> dict:
    lock = runtime_dir / LEASE_LOCK
    rep: dict = {"lock_pid": None, "held": False, "holder_file": None}
    try:
        ino = lock.stat().st_ino
    except OSError:
        ino = None
    if ino is not None:
        for line in (_read_text(proc_locks) or "").splitlines():
            f = line.split()
            # "1: FLOCK ADVISORY WRITE 1234 08:02:5511 0 EOF"
            if len(f) >= 6 and f[1] in ("FLOCK", "OFDLCK") and f[5].rsplit(":", 1)[-1] == str(ino):
                rep["held"] = True
                rep["lock_pid"] = int(f[4]) if f[4].isdigit() else None
                break
    text = _read_text(runtime_dir / LEASE_HOLDER)
    if text:
        kv = dict(line.split("=", 1) for line in text.splitlines() if "=" in line)
        pid = int(kv["pid"]) if kv.get("pid", "").isdigit() else None
        since = int(kv["since"]) if kv.get("since", "").isdigit() else None
        alive = None
        if pid is not None:
            try:
                os.kill(pid, 0)
                alive = True
            except ProcessLookupError:
                alive = False
            except PermissionError:
                alive = True
        rep["holder_file"] = {"pid": pid, "since": since, "cli": kv.get("cli"), "alive": alive}
    return rep


# ----------------------------------------------------------------------------- record


def build_record(
    *,
    cgroup_root: Path,
    proc_root: Path,
    meminfo_path: Path,
    runtime_dir: Path,
    proc_locks: Path,
    uid: int,
    now: float | None = None,
) -> dict:
    counts, extras = scan_processes(proc_root)
    outside_total = sum(c["outside_slice"] for c in counts.values())
    rec = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now if now is not None else time.time())),
        **meminfo(meminfo_path),
        "slice": slice_report(cgroup_root, uid),
        "omnigent": omnigent_report(cgroup_root, extras["omnigent_cgroup"]),
        "procs": counts,
        "outside_slice_total": outside_total,
        "unreadable": extras["unreadable"],
        "lease": lease_report(runtime_dir, proc_locks),
    }
    return rec


def append_line(out: Path, line: str) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        if out.stat().st_size > ROTATE_BYTES:
            os.replace(out, out.with_name(out.name + ".1"))
    except OSError:
        pass
    fd = os.open(out, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(fd, (line + "\n").encode())
    finally:
        os.close(fd)


def main(argv: list[str] | None = None) -> int:
    uid = os.getuid()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, default=Path.home() / ".zoe-logs" / "agents-sampler.jsonl")
    ap.add_argument("--stdout", action="store_true", help="print the record instead of appending it")
    ap.add_argument("--cgroup-root", type=Path, default=Path("/sys/fs/cgroup"))
    ap.add_argument("--proc-root", type=Path, default=Path("/proc"))
    ap.add_argument("--meminfo", type=Path, default=Path("/proc/meminfo"))
    ap.add_argument("--proc-locks", type=Path, default=Path("/proc/locks"))
    ap.add_argument("--runtime-dir", type=Path, default=Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{uid}"))
    ap.add_argument("--uid", type=int, default=uid)
    args = ap.parse_args(argv)
    rec = build_record(
        cgroup_root=args.cgroup_root,
        proc_root=args.proc_root,
        meminfo_path=args.meminfo,
        runtime_dir=args.runtime_dir,
        proc_locks=args.proc_locks,
        uid=args.uid,
    )
    line = json.dumps(rec, separators=(",", ":"), sort_keys=True)
    if args.stdout:
        print(line)
    else:
        append_line(args.out, line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
