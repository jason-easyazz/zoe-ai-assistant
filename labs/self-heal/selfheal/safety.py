"""The safety envelope: kill switch, one-at-a-time lock, caps, quiet hours, busy checks,
and the post-agent diff policy (forbidden paths, size cap, must-carry-a-test).

Every function here is pure or touches only the self-heal state dir, so each rule is
unit-testable without an engine.
"""
from __future__ import annotations

import datetime as dt
import fcntl
import fnmatch
import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import binaries
from .config import (MAX_DAILY_CAP, MAX_DIFF_LINES, MAX_PER_FAULT_CAP, Config, HARD_QUIET,
                     window_minutes)


class Refused(Exception):
    """A safety rule said no. `.rule` names the rule (it goes in the audit log)."""

    def __init__(self, rule: str, detail: str = ""):
        super().__init__(f"{rule}: {detail}" if detail else rule)
        self.rule = rule
        self.detail = detail


# --- kill switch -------------------------------------------------------------------------

def check_kill_switch(cfg: Config) -> None:
    for p in cfg.all_kill_switches():
        if Path(p).expanduser().exists():
            raise Refused("kill_switch", Path(p).name)


# --- one heal at a time ------------------------------------------------------------------

class HealLock:
    def __init__(self, cfg: Config):
        Path(cfg.state_dir).mkdir(parents=True, exist_ok=True)
        self.path = Path(cfg.state_dir) / "heal.lock"
        self._fh = None

    def __enter__(self):
        self._fh = open(self.path, "w")
        try:
            fcntl.flock(self._fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self._fh.close()
            raise Refused("lock", "another heal is running")
        return self

    def __exit__(self, *exc):
        fcntl.flock(self._fh, fcntl.LOCK_UN)
        self._fh.close()


# --- persistent counters -----------------------------------------------------------------

class State:
    """`state.json`: today's heal count, per-fault attempts / not-healed counts / parked flag,
    and the tickets (so `verify` can find them later)."""

    def __init__(self, cfg: Config, today: dt.date):
        Path(cfg.state_dir).mkdir(parents=True, exist_ok=True)
        self.path = Path(cfg.state_dir) / "state.json"
        self.today = today.isoformat()
        self.data = json.loads(self.path.read_text()) if self.path.exists() else {}
        self.data.setdefault("faults", {})
        self.data.setdefault("tickets", {})
        if self.data.get("day") != self.today:
            self.data["day"], self.data["heals_today"] = self.today, 0

    def save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=1, sort_keys=True))
        tmp.replace(self.path)

    def fault(self, key: str) -> dict:
        return self.data["faults"].setdefault(key, {"attempts": 0, "not_healed": 0, "parked": False})

    def check_caps(self, cfg: Config, fault_key: str) -> None:
        if self.data["heals_today"] >= min(cfg.daily_cap, MAX_DAILY_CAP):
            raise Refused("daily_cap", f"{self.data['heals_today']}/{cfg.daily_cap} today")
        f = self.fault(fault_key)
        if f["parked"]:
            raise Refused("parked", fault_key)
        if f["attempts"] >= min(cfg.per_fault_cap, MAX_PER_FAULT_CAP):
            raise Refused("per_fault_cap", f"{f['attempts']}/{cfg.per_fault_cap} for {fault_key}")

    def count_attempt(self, fault_key: str) -> None:
        self.data["heals_today"] += 1
        self.fault(fault_key)["attempts"] += 1
        self.save()

    def refund_attempt(self, fault_key: str) -> None:
        """An interrupted heal (quiet hours / busy / kill switch mid-run) spends no cap."""
        self.data["heals_today"] = max(0, self.data["heals_today"] - 1)
        f = self.fault(fault_key)
        f["attempts"] = max(0, f["attempts"] - 1)
        self.save()


# --- when we may run ---------------------------------------------------------------------

def check_quiet_hours(cfg: Config, now: dt.datetime) -> None:
    """The built-in 01:30-05:00 window always applies; the config can only widen it."""
    m = now.hour * 60 + now.minute
    if m in window_minutes(*HARD_QUIET) or m in window_minutes(cfg.quiet_start, cfg.quiet_end):
        raise Refused("quiet_hours", f"{now:%H:%M}")


def check_busy(cfg: Config, repo_dir: str) -> None:
    """No heal while a voice PR is landing or while the night window owns the box."""
    r = subprocess.run([binaries.which("pgrep"), "-f", cfg.voice_lander_pattern], capture_output=True, text=True)
    if r.returncode == 0:
        raise Refused("voice_lander_running", "pgrep matched")
    if cfg.night_busy_script:
        script = Path(cfg.night_busy_script)
        if not script.is_absolute():
            script = Path(repo_dir) / script
        if not script.exists():
            raise Refused("night_busy_missing", "fail closed")
        r = subprocess.run([binaries.which("bash"), str(script), "deploy"], capture_output=True, text=True, timeout=60)
        if r.returncode == 0:   # night_busy.sh: exit 0 = must pause
            raise Refused("night_busy", "night_busy.sh deploy says pause")


# --- the agent's diff --------------------------------------------------------------------

@dataclass
class DiffReport:
    files: list = field(default_factory=list)   # repo-relative paths, added/changed/deleted
    added: int = 0
    deleted: int = 0
    escaping_symlinks: list = field(default_factory=list)
    weakened_tests: list = field(default_factory=list)
    engine_committed: bool = False
    index_dirty: bool = False

    @property
    def lines(self) -> int:
        return self.added + self.deleted


def is_test_path(p: str) -> bool:
    name = p.rsplit("/", 1)[-1]
    if not name.endswith(".py"):
        return False
    return "/tests/" in f"/{p}" or bool(re.fullmatch(r"test_.*\.py|.*_test\.py", name))


# A skip / xfail added to a test file is test weakening, never a fix.
WEAKEN = re.compile(r"\bpytest\.(skip|xfail|importorskip)\b|\bmark\.(skip|skipif|xfail)\b|"
                    r"\bunittest\.(skip|expectedFailure)\b|\b(skipTest|skipIf|skipUnless)\b|__test__\s*=")


def forbidden_hits(cfg: Config, files: list) -> list:
    hits = []
    for f in files:
        if f.startswith("/") or f.startswith("../") or "/../" in f:
            hits.append(f)
            continue
        if any(fnmatch.fnmatch(f, pat) for pat in cfg.effective_forbidden()):
            hits.append(f)
    return hits


def check_diff(cfg: Config, report: DiffReport, *, ticket_has_repro_test: bool) -> None:
    """Raise Refused when the agent's diff breaks policy; the caller discards the attempt."""
    if report.engine_committed:
        raise Refused("engine_committed", "")
    if report.index_dirty:
        raise Refused("engine_staged", "")
    if not report.files:
        raise Refused("empty_diff", "the engine changed nothing")
    hits = forbidden_hits(cfg, report.files)
    if hits:
        raise Refused("forbidden_path", ", ".join(sorted(hits)[:10]))
    if report.weakened_tests:
        raise Refused("test_weakened", ", ".join(report.weakened_tests[:10]))
    if report.escaping_symlinks:
        raise Refused("outside_repo", ", ".join(report.escaping_symlinks[:10]))
    cap = min(cfg.diff_line_cap, MAX_DIFF_LINES)
    if report.lines > cap:
        raise Refused("diff_cap", f"{report.lines}>{cap}")
    # A failing-test ticket already carries its regression test (the reproduction, which the
    # verify step re-runs from the ORIGINAL file). Any other ticket must add or change a test.
    if not ticket_has_repro_test and not any(is_test_path(f) for f in report.files):
        raise Refused("missing_test", "the diff adds or updates no test")


def live_fingerprint(cfg: Config) -> str:
    """Read the live checkout's HEAD WITHOUT running git in it (git status would write its index)."""
    git = Path(cfg.live_checkout) / ".git"
    head = git / "HEAD"
    if not head.is_file():
        return "absent"
    text = head.read_text().strip()
    if text.startswith("ref: "):
        ref = git / text[5:]
        if ref.is_file():
            text += " " + ref.read_text().strip()
    return text



_COUNTS = re.compile(r"\b(\d+) (passed|failed|errors?|skipped|xfailed|xpassed|deselected)\b")


def rc_summary(rc: int, output: str = "") -> str:
    """Exit code + pytest-style counts ONLY. Never assertion text, tracebacks or model output."""
    counts = ",".join(f"{w}={n}" for n, w in _COUNTS.findall(output or ""))
    return f"rc={rc}" + (f",{counts}" if counts else "")
