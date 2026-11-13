#!/usr/bin/env python3
"""ONE health check on the canonical spine (architecture deep dive B9), run by zoe-health.timer every 5 minutes.

It replaces the two untracked watchdogs: ``~/bin/zoe-health-check.sh`` (zoe-health.service: still probed Hermes
``:8642``, never the live brain ``:3579`` or Kokoro ``:10201``, carried a literal database password, and its only
output was a log nobody read) and ``~/bin/zoe-watchdog.sh`` (a crontab line: zoe-data + llama only, pushed through
zoe-data's own API, so it could not report zoe-data being down).

Probes, all bounded (2 s HTTP, 5 s docker/systemctl):
  * the spine's real ``/health`` (never ``is-active``, which lies while a model loads): zoe-data :8000,
    the 4B llama-server :11434, the Flue 2.x brain :3579, Kokoro :10201, the FunctionGemma router :11436;
  * Postgres (``pg_isready`` in zoe-database), containers in ``(unhealthy)`` / ``Restarting``;
  * failed user units (a failed backup or verify job shows up here), disk on /, MemAvailable.
While the night window is open (``~/.zoe/night-window/WINDOW_OPEN``) the spine is stopped ON PURPOSE: its probes
are skipped, not failed.

Alerting is on TRANSITIONS only: a check must fail on two consecutive runs before it alerts (one restart window is
not an outage), then stays quiet until it recovers, which sends one "recovered" line. State:
``~/.local/state/zoe-health/state.json``. The alert goes over Telegram through the shared owner-alert helper
(``zoe_crash_loop_watch.deliver``: retries + a durable outbox) - a process separate from zoe-data, so it can report
zoe-data's death. The "owner alerts" check is red while that helper records alerting as broken (Telegram refused the
credential, or no recipient), so the health log says so even though its own Telegram line cannot get through.

    python3 scripts/maintenance/zoe_health.py            # one run (the unit)
    python3 scripts/maintenance/zoe_health.py --dry-run  # probe + print, no state, no alert

Exit 0 healthy, 1 a check is failing (whether or not it alerted this run).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

HOME = Path(os.environ.get("HOME", str(Path.home())))
REPO_ROOT = Path(__file__).resolve().parents[2]
STATE = Path(os.environ.get("ZOE_HEALTH_STATE", str(HOME / ".local" / "state" / "zoe-health" / "state.json")))
NIGHT_MARKER = Path(os.environ.get("NIGHT_DIR", str(HOME / ".zoe" / "night-window"))) / "WINDOW_OPEN"

# The canonical spine (docs/CANONICAL.md): name -> port. Retired lanes (Hermes :8642, OpenClaw :18789, Flue 1.x
# :3578) are deliberately absent; tests/unit/test_zoe_health.py fails if one comes back.
SPINE: dict[str, int] = {
    "zoe-data": 8000,
    "llama-server": 11434,
    "flue-zoe-brain-2x": 3579,
    "kokoro-tts": 10201,
    "functiongemma-router": 11436,
}
DISK_WARN_PCT = 90
MEM_WARN_MB = 700          # the remote voice-gate floor; below it the box is in trouble
FAILS_TO_ALERT = 2


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


def http_ok(port: int, path: str = "/health", timeout: float = 2.0) -> tuple[bool, str]:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=timeout) as r:
            return r.status == 200, f"HTTP {r.status}"
    except Exception as exc:  # noqa: BLE001
        return False, type(exc).__name__


def _run(argv: list[str], timeout: float = 5.0) -> tuple[int, str]:
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or r.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 124, type(exc).__name__


NIGHT_TIMER = "zoe-night-window.timer"


def night_timer_check(run: Callable = _run) -> Check:
    """The night window only happens if its timer is armed. A stopped/disabled timer used to be silent (one night the 12B simply
    never ran and nothing said so); ``failed units`` cannot see it because an inactive timer is not failed. Not skipped while the
    window is open: the timer stays active while its service runs."""
    _, active = run(["systemctl", "--user", "is-active", NIGHT_TIMER])
    _, enabled = run(["systemctl", "--user", "is-enabled", NIGHT_TIMER])
    active, enabled = (active or "").strip(), (enabled or "").strip()
    ok = active == "active" and enabled in ("enabled", "enabled-runtime")
    return Check("night-window timer", ok, f"{NIGHT_TIMER} is {active or '?'} / {enabled or '?'}" if not ok else "armed")


def probe(http: Callable[[int], tuple[bool, str]] = http_ok, run: Callable = _run,
          night_open: Optional[bool] = None) -> list[Check]:
    out: list[Check] = []
    night = NIGHT_MARKER.exists() if night_open is None else night_open
    for name, port in SPINE.items():
        if night:
            continue
        ok, why = http(port)
        out.append(Check(f"{name} :{port}", ok, why))
    rc, _ = run(["docker", "exec", "zoe-database", "pg_isready", "-U", "zoe", "-q"])
    out.append(Check("postgres", rc == 0, f"pg_isready rc={rc}"))
    rc, txt = run(["docker", "ps", "--format", "{{.Names}}\t{{.Status}}"])
    bad = [ln.split("\t")[0] for ln in txt.splitlines() if "(unhealthy)" in ln or "\tRestarting" in ln] if rc == 0 else []
    out.append(Check("containers", rc == 0 and not bad, ", ".join(bad) if bad else f"docker rc={rc}" if rc else "ok"))
    rc, txt = run(["systemctl", "--user", "--failed", "--plain", "--no-legend", "--type=service,timer"])
    failed = sorted(ln.split()[0] for ln in txt.splitlines() if ln.strip()) if rc == 0 else []
    out.append(Check("failed units", not failed, ", ".join(failed) if failed else "none"))
    out.append(night_timer_check(run))
    du = shutil.disk_usage("/")
    pct = round(100 * du.used / du.total)
    out.append(Check("disk /", pct < DISK_WARN_PCT, f"{pct}% used"))
    avail = 0
    try:
        for line in open("/proc/meminfo"):
            if line.startswith("MemAvailable:"):
                avail = int(line.split()[1]) // 1024
    except OSError:
        pass
    out.append(Check("memory", avail >= MEM_WARN_MB, f"MemAvailable {avail} MB"))
    return out


def transitions(checks: list[Check], state: dict) -> tuple[list[str], dict]:
    """New state + the alert lines this run sends. A check alerts on its FAILS_TO_ALERT-th consecutive failure
    and once on recovery after having alerted. Checks absent this run (night window) keep their state."""
    lines: list[str] = []
    new = dict(state)
    for c in checks:
        s = dict(new.get(c.name, {"fails": 0, "alerted": False}))
        if c.ok:
            if s["alerted"]:
                lines.append(f"recovered: {c.name} ({c.detail})")
            s = {"fails": 0, "alerted": False}
        else:
            s["fails"] += 1
            if s["fails"] >= FAILS_TO_ALERT and not s["alerted"]:
                lines.append(f"DOWN: {c.name} ({c.detail})")
                s["alerted"] = True
        new[c.name] = s
    return lines, new


def _ops():
    spec = importlib.util.spec_from_file_location("_clw", REPO_ROOT / "scripts" / "maintenance" / "zoe_crash_loop_watch.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def alerting_check(broken: "Callable[[], Optional[dict]] | None" = None) -> Check:
    """Red while owner alerts fail on a credential or a missing setting (the outbox's broken record)."""
    try:
        b = (broken or _ops().alerting_broken)()
    except Exception as exc:  # noqa: BLE001
        return Check("owner alerts", True, f"unreadable ({type(exc).__name__})")
    if not b:
        return Check("owner alerts", True, "ok")
    since = datetime.fromtimestamp(float(b.get("since") or 0)).isoformat(timespec="minutes")
    return Check("owner alerts", False, f"BROKEN since {since}: Telegram {b.get('reason', '?')} failure, alerts held in the outbox")


def send(lines: list[str]) -> bool:
    """True when the lines were delivered (or there was nothing to send)."""
    if not lines:
        return True
    if os.environ.get("ZOE_HEALTH_NO_ALERT"):
        return True
    try:
        outcome, _reason = _ops().deliver("health", "Zoe health: " + "; ".join(lines))
        return outcome in ("delivered", "uncertain", "queued")   # queued: the shared outbox retries it
    except Exception as exc:  # noqa: BLE001
        print(f"alert not delivered: {type(exc).__name__}", file=sys.stderr)
        return False


def undelivered(state_before: dict, state_after: dict) -> dict:
    """The state to persist when this run's alerts were NOT delivered: a DOWN that did not reach anyone stays
    pending (alerted=False) so the next run retries it, instead of being recorded as sent and never repeated."""
    out = dict(state_after)
    for name, s in state_after.items():
        if s.get("alerted") and not state_before.get(name, {}).get("alerted"):
            out[name] = {**s, "alerted": False}
    return out


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dry-run", action="store_true")
    ns = ap.parse_args(argv)
    checks = probe() + [alerting_check()]
    stamp = datetime.now().isoformat(timespec="seconds")
    if NIGHT_MARKER.exists():
        print(f"[{stamp}] night window open: spine probes skipped")
    for c in checks:
        print(f"[{stamp}] {'ok  ' if c.ok else 'FAIL'} {c.name}: {c.detail}")
    if ns.dry_run:
        return 0 if all(c.ok for c in checks) else 1
    try:
        state = json.loads(STATE.read_text())
    except (OSError, ValueError):
        state = {}
    lines, new = transitions(checks, state)
    for ln in lines:
        print(f"[{stamp}] ALERT {ln}")
    if not send(lines):          # deliver FIRST; persist "alerted" only for what actually went out
        new = undelivered(state, new)
        print(f"[{stamp}] alert NOT delivered - will retry next run", file=sys.stderr)
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(new))
    os.replace(tmp, STATE)
    return 0 if all(c.ok for c in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
