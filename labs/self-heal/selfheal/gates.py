"""Local gates, run in the heal worktree BEFORE anything leaves it.

A gate returns (name, status, detail) with status pass | fail | skip. Any `fail` stops the heal.
`skip` is recorded honestly (e.g. no validate_structure.py in a scratch repo) and is never "pass".
"""
from __future__ import annotations

import fnmatch
import subprocess
from pathlib import Path

from .config import Config
from .detectors import Ticket
from .safety import is_test_path


def _lock(cfg: Config) -> list:
    return ["flock", "-x", "-w", str(cfg.test_lock_wait_s), cfg.test_lock] if cfg.test_lock else []


def _sh(argv: list, cwd: str, timeout: int) -> tuple[bool, str]:
    try:
        r = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, f"timeout after {timeout}s"
    tail = (r.stdout.strip() or r.stderr.strip()).splitlines()
    return r.returncode == 0, f"rc={r.returncode} {(tail[-1] if tail else '')[:200]}"


def relevant_tests(wt: str, changed: list, ticket: Ticket) -> list:
    """Changed test files + the ticket's reproduction file + test_<stem>.py for each changed module."""
    found = {f for f in changed if is_test_path(f) and (Path(wt) / f).exists()}
    found.update(f for f in ticket.repro_files if (Path(wt) / f).exists())
    stems = {Path(f).stem for f in changed if f.endswith(".py") and not is_test_path(f)}
    if stems:
        for p in Path(wt).rglob("test_*.py"):
            if ".git" in p.parts:
                continue
            if p.stem[len("test_"):] in stems:
                found.add(str(p.relative_to(wt)))
    return sorted(found)


def gate_validate_structure(cfg, wt, changed, ticket):
    script = Path(wt) / "tools/audit/validate_structure.py"
    if not script.exists():
        return "skip", "no tools/audit/validate_structure.py in this repo"
    ok, d = _sh([cfg.python, str(script)], wt, cfg.gate_timeout_s)
    return ("pass" if ok else "fail"), d


def gate_py_compile(cfg, wt, changed, ticket):
    py = [f for f in changed if f.endswith(".py") and (Path(wt) / f).exists()]
    if not py:
        return "skip", "no changed python"
    ok, d = _sh([cfg.python, "-m", "py_compile", *py], wt, cfg.gate_timeout_s)
    return ("pass" if ok else "fail"), f"{len(py)} files {d}"


def gate_relevant_pytest(cfg, wt, changed, ticket):
    tests = relevant_tests(wt, changed, ticket)
    if not tests:
        return "fail", "no relevant test found to run"   # a heal with nothing to prove it is not a heal
    ok, d = _sh([*_lock(cfg), cfg.python, "-m", "pytest", "-p", "no:cacheprovider", "-q", *tests],
                wt, cfg.gate_timeout_s)
    return ("pass" if ok else "fail"), f"{len(tests)} files {d}"


def gate_offline_lane(cfg, wt, changed, ticket):
    if not cfg.offline_lane_cmd:
        return "fail", "local landing needs offline_lane_cmd (fail closed)"
    ok, d = _sh([*_lock(cfg), *cfg.offline_lane_cmd], wt, cfg.gate_timeout_s)
    return ("pass" if ok else "fail"), d


def gate_voice_replay(cfg, wt, changed, ticket):
    voice = [f for f in changed if any(fnmatch.fnmatch(f, p) for p in cfg.voice_paths)]
    if not voice:
        return "skip", "no voice-path file changed"
    if not cfg.voice_replay_cmd:
        return "fail", f"voice-path change ({voice[0]}) and no voice_replay_cmd configured (fail closed)"
    ok, d = _sh(list(cfg.voice_replay_cmd), wt, cfg.gate_timeout_s)
    return ("pass" if ok else "fail"), d


GATES = {
    "validate_structure": gate_validate_structure,
    "py_compile": gate_py_compile,
    "relevant_pytest": gate_relevant_pytest,
    "offline_lane": gate_offline_lane,
    "voice_replay": gate_voice_replay,
}


def run_gates(cfg: Config, names: list, wt: str, changed: list, ticket: Ticket) -> list:
    results = []
    for name in names:
        if name not in GATES:
            results.append({"gate": name, "status": "fail", "detail": "unknown gate"})
            break
        status, detail = GATES[name](cfg, wt, changed, ticket)
        results.append({"gate": name, "status": status, "detail": detail})
        if status == "fail":
            break
    return results


def gates_green(results: list) -> bool:
    return bool(results) and all(r["status"] in ("pass", "skip") for r in results)
