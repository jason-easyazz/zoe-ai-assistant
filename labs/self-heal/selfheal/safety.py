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

from .config import Config


class Refused(Exception):
    """A safety rule said no. `.rule` names the rule (it goes in the audit log)."""

    def __init__(self, rule: str, detail: str = ""):
        super().__init__(f"{rule}: {detail}" if detail else rule)
        self.rule = rule
        self.detail = detail


# --- kill switch -------------------------------------------------------------------------

def check_kill_switch(cfg: Config) -> None:
    for p in cfg.kill_switches:
        if Path(p).expanduser().exists():
            raise Refused("kill_switch", str(p))


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
        if self.data["heals_today"] >= cfg.daily_cap:
            raise Refused("daily_cap", f"{self.data['heals_today']}/{cfg.daily_cap} today")
        f = self.fault(fault_key)
        if f["parked"]:
            raise Refused("parked", fault_key)
        if f["attempts"] >= cfg.per_fault_cap:
            raise Refused("per_fault_cap", f"{f['attempts']}/{cfg.per_fault_cap} for {fault_key}")

    def count_attempt(self, fault_key: str) -> None:
        self.data["heals_today"] += 1
        self.fault(fault_key)["attempts"] += 1
        self.save()


# --- when we may run ---------------------------------------------------------------------

def _hm(s: str) -> dt.time:
    h, m = s.split(":")
    return dt.time(int(h), int(m))


def check_quiet_hours(cfg: Config, now: dt.datetime) -> None:
    start, end, t = _hm(cfg.quiet_start), _hm(cfg.quiet_end), now.time()
    inside = (start <= t < end) if start <= end else (t >= start or t < end)
    if inside:
        raise Refused("quiet_hours", f"{now:%H:%M} is inside {cfg.quiet_start}-{cfg.quiet_end}")


def check_busy(cfg: Config, repo_dir: str) -> None:
    """No heal while a voice PR is landing or while the night window owns the box."""
    r = subprocess.run(["pgrep", "-f", cfg.voice_lander_pattern], capture_output=True, text=True)
    if r.returncode == 0:
        raise Refused("voice_lander_running", r.stdout.strip().replace("\n", ","))
    if cfg.night_busy_script:
        script = Path(cfg.night_busy_script)
        if not script.is_absolute():
            script = Path(repo_dir) / script
        if not script.exists():
            raise Refused("night_busy_missing", f"{script} not found (fail closed)")
        r = subprocess.run(["bash", str(script), "deploy"], capture_output=True, text=True, timeout=60)
        if r.returncode == 0:   # night_busy.sh: exit 0 = must pause
            raise Refused("night_busy", "night_busy.sh deploy says pause")


# --- the agent's diff --------------------------------------------------------------------

@dataclass
class DiffReport:
    files: list = field(default_factory=list)   # repo-relative paths, added/changed/deleted
    added: int = 0
    deleted: int = 0
    escaping_symlinks: list = field(default_factory=list)

    @property
    def lines(self) -> int:
        return self.added + self.deleted


def is_test_path(p: str) -> bool:
    name = p.rsplit("/", 1)[-1]
    if not name.endswith(".py"):
        return False
    return "/tests/" in f"/{p}" or bool(re.fullmatch(r"test_.*\.py|.*_test\.py", name))


def forbidden_hits(cfg: Config, files: list) -> list:
    hits = []
    for f in files:
        if f.startswith("/") or f.startswith("../") or "/../" in f:
            hits.append(f)
            continue
        if any(fnmatch.fnmatch(f, pat) for pat in cfg.forbidden):
            hits.append(f)
    return hits


def check_diff(cfg: Config, report: DiffReport, *, ticket_has_repro_test: bool) -> None:
    """Raise Refused when the agent's diff breaks policy; the caller discards the attempt."""
    if not report.files:
        raise Refused("empty_diff", "the engine changed nothing")
    hits = forbidden_hits(cfg, report.files)
    if hits:
        raise Refused("forbidden_path", ", ".join(sorted(hits)[:10]))
    if report.escaping_symlinks:
        raise Refused("outside_repo", ", ".join(report.escaping_symlinks[:10]))
    if report.lines > cfg.diff_line_cap:
        raise Refused("diff_cap", f"{report.lines} changed lines > {cfg.diff_line_cap}")
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

