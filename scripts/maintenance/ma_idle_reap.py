#!/usr/bin/env python3
"""Stop Music Assistant when nobody has played anything for a while.

The STOP half of the Music Assistant idle reap (owner decision Q2, 2026-10-04;
flag-dark behind ``ZOE_MA_IDLE_REAP=1``). The START half is
``services/zoe-data/ma_ondemand.py``: the first music request after a reap
``docker start``s the container again. Run every 10 min by
``scripts/setup/systemd/zoe-ma-idle-reap.timer``. Record:
``docs/knowledge/music-assistant-idle-reap.md``.

The container is kept unless EVERY guard passes (``decide()`` is the pure
function; each guard has its own negative control in
``tests/unit/test_ma_idle_reap.py``):

  1. the flag is on;
  2. the container is running (nothing to do otherwise);
  3. MA's API answered (an unreadable MA is UNKNOWN, never idle);
  4. no request is in flight (``inflight`` stamp older than the grace);
  5. no player is ``playing``, and no ``paused`` player still holds a queue;
  6. the last activity — the newest of the zoe-data ``activity`` stamp and MA's
     own per-queue ``elapsed_time_last_updated`` — is older than ``--idle-min``;
  7. the local hour is outside ``ZOE_MA_REAP_QUIET_HOURS`` (``"HH-HH"``, optional).

Dry-run by default (prints the decision). ``--execute`` performs the stop,
under the same ``flock`` ``ensure_running`` holds while starting, so the two can
never interleave. Stdlib only — runs on ``/usr/bin/python3`` from a user timer.
Secrets: ``MUSIC_ASSISTANT_TOKEN`` is read from the environment and never printed.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Optional

TRUTHY = ("1", "true", "on", "yes")
DEFAULT_IDLE_MIN = 45
DEFAULT_INFLIGHT_GRACE_S = 120
ACTIVE_STATES = ("playing",)
PAUSED_STATES = ("paused",)


def parse_quiet_hours(spec: Optional[str]) -> Optional[tuple[int, int]]:
    """``"22-07"`` → (22, 7); empty/invalid → None (no protected window)."""
    if not spec or "-" not in spec:
        return None
    try:
        a, b = (int(x) for x in spec.strip().split("-", 1))
    except ValueError:
        return None
    if not (0 <= a <= 23 and 0 <= b <= 23):
        return None
    return a, b


def in_quiet_hours(hour: int, window: Optional[tuple[int, int]]) -> bool:
    """Start inclusive, end exclusive; a window may wrap midnight (22-07)."""
    if window is None:
        return False
    start, end = window
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


def _state(p: dict[str, Any]) -> str:
    return str(p.get("playback_state") or p.get("state") or "idle").lower()


def last_activity_epoch(queues: list[dict[str, Any]], stamp_epoch: float) -> float:
    """Newest of the zoe-data activity stamp and MA's per-queue state-change time."""
    latest = stamp_epoch
    for q in queues:
        try:
            latest = max(latest, float(q.get("elapsed_time_last_updated") or 0))
        except (TypeError, ValueError):
            continue
    return latest


def decide(*, flag_on: bool, running: bool, ma_readable: bool,
           players: list[dict[str, Any]], queues: list[dict[str, Any]],
           now: float, activity_epoch: float, inflight_epoch: float,
           idle_min: float, inflight_grace_s: float,
           quiet_window: Optional[tuple[int, int]], local_hour: int) -> tuple[str, str]:
    """('stop' | 'keep', reason). Pure: no I/O, no clock, no env."""
    if not flag_on:
        return "keep", "flag off (ZOE_MA_IDLE_REAP)"
    if not running:
        return "keep", "container already stopped"
    if not ma_readable:
        return "keep", "MA API unreadable — state unknown, never idle"
    if now - inflight_epoch < inflight_grace_s:
        return "keep", "music request in flight"
    queue_items = {str(q.get("queue_id")): int(q.get("items") or 0) for q in queues}
    for p in players:
        st = _state(p)
        name = p.get("display_name") or p.get("name") or p.get("player_id")
        if st in ACTIVE_STATES:
            return "keep", f"{name} is playing"
        if st in PAUSED_STATES and queue_items.get(str(p.get("player_id")), 1) > 0:
            return "keep", f"{name} is paused with a queue"
    idle_s = now - last_activity_epoch(queues, activity_epoch)
    if idle_s < idle_min * 60:
        return "keep", f"active {int(idle_s // 60)} min ago (< {int(idle_min)})"
    if in_quiet_hours(local_hour, quiet_window):
        return "keep", f"inside quiet hours {quiet_window[0]:02d}-{quiet_window[1]:02d}"
    return "stop", f"idle_min={int(idle_s // 60)}"


# ── I/O (everything above is pure) ───────────────────────────────────────────

def _docker(*args: str, timeout: float = 30) -> tuple[int, str]:
    try:
        r = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, str(exc)
    return r.returncode, (r.stdout + r.stderr).strip()


def _ma_cmd(command: str) -> Optional[list[dict[str, Any]]]:
    url = os.environ.get("MUSIC_ASSISTANT_URL", "http://localhost:8095").rstrip("/")
    headers = {"Content-Type": "application/json"}
    token = os.environ.get("MUSIC_ASSISTANT_TOKEN", "")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"{url}/api", data=json.dumps({"command": command}).encode(),
                                 headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
    except Exception:  # noqa: BLE001 — unreadable → None → keep
        return None
    if isinstance(data, dict):
        data = data.get("items") or data.get("result") or []
    return data if isinstance(data, list) else None


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--execute", action="store_true", help="actually docker stop (default: dry-run)")
    ap.add_argument("--idle-min", type=float,
                    default=float(os.environ.get("ZOE_MA_IDLE_MIN", DEFAULT_IDLE_MIN)))
    ap.add_argument("--inflight-grace-s", type=float,
                    default=float(os.environ.get("ZOE_MA_INFLIGHT_GRACE_S", DEFAULT_INFLIGHT_GRACE_S)))
    args = ap.parse_args(argv)

    container = os.environ.get("ZOE_MA_CONTAINER", "zoe-music-assistant")
    state_dir = Path(os.environ.get("ZOE_MA_REAP_STATE_DIR", "~/.cache/zoe/ma-reap")).expanduser()
    flag_on = os.environ.get("ZOE_MA_IDLE_REAP", "0").strip().lower() in TRUTHY
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S")

    rc, out = _docker("inspect", "-f", "{{.State.Running}}", container, timeout=10)
    running = rc == 0 and out.strip() == "true"
    players = queues = None
    if flag_on and running:
        players, queues = _ma_cmd("players/all"), _ma_cmd("player_queues/all")

    action, reason = decide(
        flag_on=flag_on, running=running,
        ma_readable=players is not None and queues is not None,
        players=players or [], queues=queues or [],
        now=time.time(), activity_epoch=_mtime(state_dir / "activity"),
        inflight_epoch=_mtime(state_dir / "inflight"),
        idle_min=args.idle_min, inflight_grace_s=args.inflight_grace_s,
        quiet_window=parse_quiet_hours(os.environ.get("ZOE_MA_REAP_QUIET_HOURS")),
        local_hour=time.localtime().tm_hour,
    )
    if action != "stop":
        print(f"{stamp} MA_REAP keep {reason}")
        return 0
    if not args.execute:
        print(f"{stamp} MA_REAP would-stop {reason} (dry-run; pass --execute)")
        return 0
    state_dir.mkdir(parents=True, exist_ok=True)
    with open(state_dir / "lock", "a+") as lock_fh:
        try:
            fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print(f"{stamp} MA_REAP keep a start is in progress (lock held)")
            return 0
        # Re-check the one thing that can change while we waited: a wake stamp.
        if time.time() - _mtime(state_dir / "inflight") < args.inflight_grace_s:
            print(f"{stamp} MA_REAP keep music request in flight (re-check)")
            return 0
        rc, out = _docker("stop", container, timeout=60)
    if rc != 0:
        print(f"{stamp} MA_REAP stop FAILED rc={rc} {out[:200]}", file=sys.stderr)
        return 1
    print(f"{stamp} MA_REAP stop {reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
