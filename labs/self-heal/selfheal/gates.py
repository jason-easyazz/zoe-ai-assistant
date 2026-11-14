"""Local gates, run in the heal worktree BEFORE anything leaves it.

A gate returns (name, status, detail) with status pass | fail | skip. Any `fail` stops the heal.
`skip` is recorded honestly (e.g. no validate_structure.py in a scratch repo) and is never "pass".
"""
from __future__ import annotations

import fnmatch
from pathlib import Path

from .config import Config
from .detectors import Ticket, node_statuses
from .safety import is_test_path, rc_summary
from .sandbox import Sandbox
from .supervisor import gate_supervised_pytest


def _lock(cfg: Config) -> list:
    return ["flock", "-x", "-w", str(cfg.test_lock_wait_s), cfg.test_lock] if cfg.test_lock else []


def _sh(cfg: Config, argv: list, wt: str, lock: list = ()) -> tuple[bool, str]:
    """Every gate runs repo code: inside the sandbox, whole process group killed on timeout."""
    rc, out = Sandbox(cfg).run(argv, wt, cfg.gate_timeout_s, lock)
    return (False, out) if rc is None else (rc == 0, rc_summary(rc, out))


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


def gate_validate_structure(cfg, wt, changed, ticket, base_wt=None):
    script = Path(wt) / "tools/audit/validate_structure.py"
    if not script.exists():
        return "skip", "no_validator"
    ok, d = _sh(cfg, [cfg.python, str(script)], wt)
    return ("pass" if ok else "fail"), d


def gate_py_compile(cfg, wt, changed, ticket, base_wt=None):
    py = [f for f in changed if f.endswith(".py") and (Path(wt) / f).exists()]
    if not py:
        return "skip", "no_changed_python"
    ok, d = _sh(cfg, [cfg.python, "-m", "py_compile", *py], wt)
    return ("pass" if ok else "fail"), f"files={len(py)},{d}"


def _pytest_nodes(cfg, wt: str, tests: list) -> tuple[bool, str, dict | None]:
    """(ok, detail, {node: status}); no fresh, parseable report = NOT ok (detail `no_report`)."""
    rc, out, xml = Sandbox(cfg).run_report([cfg.python, "-m", "pytest", "-p", "no:cacheprovider", "-q", *tests],
                                           wt, cfg.gate_timeout_s, _lock(cfg))
    if rc is None:
        return False, out, None
    if xml is None:
        return False, f"{rc_summary(rc, out)},no_report", None
    return rc == 0, rc_summary(rc, out), node_statuses(xml)


def gate_relevant_pytest(cfg, wt, changed, ticket, base_wt=None):
    """The gate's test files must pass, AND, node by node against a pre-heal baseline of the same
    files at the base commit: no test that ran before (passing or failing) may now be skipped,
    xfailed, deselected or missing, and the reproduction node must go from failing to PASSED.
    This is the runtime check that no regex can be talked out of; new tests are fine."""
    # the baseline's file list comes from the BASE commit's tree (a test file the heal deleted is
    # still listed there); every one of them must still exist and run after the heal
    base_tests = relevant_tests(base_wt, changed, ticket) if base_wt else []
    gone = [t for t in base_tests if not (Path(wt) / t).is_file()]
    if gone:
        return "fail", f"test_weakened:files_missing={len(gone)}"
    tests = sorted(set(relevant_tests(wt, changed, ticket)) | set(base_tests))
    if not tests:
        return "fail", "no_relevant_test"   # a heal with nothing to prove it is not a heal
    ok, d, after = _pytest_nodes(cfg, wt, tests)
    if not ok:
        return "fail", f"files={len(tests)},{d}"
    before = _pytest_nodes(cfg, base_wt, base_tests)[2] if base_tests else ({} if base_wt else None)
    if after is None or before is None:
        return "fail", f"files={len(tests)},no_baseline"
    weakened = [n for n, st in before.items() if after.get(n) != "passed"]
    repro = ticket.evidence.get("nodeid", "")
    if repro and not (before.get(repro) in ("failure", "error") and after.get(repro) == "passed"):
        weakened.append(repro)
    if weakened:
        return "fail", f"test_weakened:nodes={len(set(weakened))}"
    return "pass", f"files={len(tests)},{d},baseline={len(before)}"


def gate_offline_lane(cfg, wt, changed, ticket, base_wt=None):
    if not cfg.offline_lane_cmd:
        return "fail", "offline_lane_unset"
    ok, d = _sh(cfg, list(cfg.offline_lane_cmd), wt, _lock(cfg))
    return ("pass" if ok else "fail"), d


def gate_voice_replay(cfg, wt, changed, ticket, base_wt=None):
    voice = [f for f in changed if any(fnmatch.fnmatch(f, p) for p in cfg.voice_paths)]
    if not voice:
        return "skip", "no_voice_path_change"
    if not cfg.voice_replay_cmd:
        return "fail", "voice_path_no_replay"
    ok, d = _sh(cfg, list(cfg.voice_replay_cmd), wt)
    return ("pass" if ok else "fail"), d


GATES = {
    "validate_structure": gate_validate_structure,
    "py_compile": gate_py_compile,
    "relevant_pytest": gate_relevant_pytest,
    "offline_lane": gate_offline_lane,
    "voice_replay": gate_voice_replay,
    "supervised_pytest": gate_supervised_pytest,   # the verdict from outside the test process
}


def run_gates(cfg: Config, names: list, wt: str, changed: list, ticket: Ticket, base_wt: str | None = None) -> list:
    results = []
    for name in names:
        if name not in GATES:
            results.append({"gate": name, "status": "fail", "detail": "unknown"})
            break
        status, detail = GATES[name](cfg, wt, changed, ticket, base_wt=base_wt)
        results.append({"gate": name, "status": status, "detail": detail})
        if status == "fail":
            break
    return results


def gates_green(results: list) -> bool:
    return bool(results) and all(r["status"] in ("pass", "skip") for r in results)
