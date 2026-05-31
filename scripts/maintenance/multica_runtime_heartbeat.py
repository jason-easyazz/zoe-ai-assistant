#!/usr/bin/env python3
"""multica_runtime_heartbeat.py — Reflect real local liveness into Multica's runtime registry.

Why this exists
---------------
Multica's runtime registry (``agent_runtime`` rows) is normally fed by a Multica
*daemon* that registers and then heartbeats over the daemon-authenticated API.
Multica's sweeper flips an ``online`` row to ``offline`` once its ``last_seen_at``
goes stale (``staleThresholdSeconds`` = 150s server-side).

Zoe does NOT run a Multica daemon — the Zoe<->Multica pipeline talks to the
*issues* REST API (executor_registry -> kanban_adapter), not the daemon/runtime
protocol. The three Zoe runtime rows (openclaw-gateway, hermes-agent, Zoe Home
Server) were seeded directly into the Multica DB by
``scripts/setup/populate_multica.py`` with ``daemon_id = NULL``, so nothing ever
heartbeats them and they permanently read "offline".

This script closes that gap *honestly*: it probes the actual local processes and
writes the truthful status (``online`` iff the process is really up) plus a fresh
``last_seen_at`` directly into the Multica DB — the same channel
``populate_multica.py`` already uses for these exact rows (POST /api/runtimes is
405; PATCH only edits timezone/visibility). It is intended to run on a short
systemd timer (< 150s cadence) so live runtimes stay online and dead ones flip
offline within one tick.

This is a cosmetic registry reflector. The Hermes Kanban execution pipeline does
NOT depend on these rows; it exists so the Multica board shows reality.

Usage
-----
    python3 scripts/maintenance/multica_runtime_heartbeat.py            # apply
    python3 scripts/maintenance/multica_runtime_heartbeat.py --dry-run  # report only

Env (read from .env, overridable by real env):
    MULTICA_WORKSPACE_ID   workspace whose runtimes to refresh (required)
    MULTICA_DB_CONTAINER   docker container running Multica's Postgres (default: zoe-database)
    MULTICA_DB_USER        Postgres user (default: zoe)
    MULTICA_DB_NAME        Postgres db   (default: multica)
    ZOE_HEALTH_URL         Zoe health endpoint (default: http://localhost:8000/health)
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.request
from collections.abc import Callable
from pathlib import Path

_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"


def _load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    if _ENV_PATH.exists():
        for line in _ENV_PATH.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
    env.update(os.environ)
    return env


_ENV = _load_env()
WORKSPACE_ID = _ENV.get("MULTICA_WORKSPACE_ID", "").strip()
DB_CONTAINER = _ENV.get("MULTICA_DB_CONTAINER", "zoe-database").strip()
DB_USER = _ENV.get("MULTICA_DB_USER", "zoe").strip()
DB_NAME = _ENV.get("MULTICA_DB_NAME", "multica").strip()
HEALTH_URL = _ENV.get("ZOE_HEALTH_URL", "http://localhost:8000/health").strip()


def _proc_alive(pattern: str) -> bool:
    """True if a process whose full command line matches ``pattern`` is running.

    ``pgrep`` excludes its own PID, so the pattern appearing in this script's
    arguments does not self-match.
    """
    try:
        res = subprocess.run(
            ["pgrep", "-f", pattern],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return res.returncode == 0 and bool(res.stdout.strip())
    except Exception:
        return False


def _zoe_alive() -> bool:
    """True if the Zoe host API reports healthy."""
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=5) as resp:
            if resp.status != 200:
                return False
            data = json.loads(resp.read().decode("utf-8", errors="replace") or "{}")
            return str(data.get("status", "")).lower() == "ok"
    except Exception:
        return False


# provider -> (human label, liveness probe). Keyed by the agent_runtime.provider
# column so we update exactly the seeded Zoe rows and nothing else.
_RUNTIMES: tuple[tuple[str, str, Callable[[], bool]], ...] = (
    ("zoe", "Zoe Home Server", _zoe_alive),
    ("hermes", "hermes-agent", lambda: _proc_alive("hermes gateway run")),
    ("openclaw_gateway", "openclaw-gateway", lambda: _proc_alive("openclaw/dist/index.js gateway")),
)


def _update_runtime(provider: str, online: bool) -> tuple[bool, str]:
    """Set status (+ bump last_seen_at when online) for one provider's row."""
    status = "online" if online else "offline"
    sql = (
        "UPDATE agent_runtime "
        "SET status = :'st', "
        "    last_seen_at = CASE WHEN :'st' = 'online' THEN now() ELSE last_seen_at END, "
        "    updated_at = now() "
        "WHERE workspace_id = :'ws'::uuid AND provider = :'provider';"
    )
    # SQL is piped on stdin (not -c) so psql performs :'var' interpolation,
    # which quotes/escapes each value safely server-side.
    cmd = [
        "docker", "exec", "-i", DB_CONTAINER,
        "psql", "-U", DB_USER, "-d", DB_NAME,
        "-v", "ON_ERROR_STOP=1",
        "-v", f"ws={WORKSPACE_ID}",
        "-v", f"provider={provider}",
        "-v", f"st={status}",
    ]
    try:
        res = subprocess.run(cmd, input=sql, capture_output=True, text=True, timeout=15)
        if res.returncode == 0:
            return True, res.stdout.strip()
        return False, (res.stderr or res.stdout).strip()
    except Exception as exc:  # pragma: no cover - defensive
        return False, str(exc)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true",
        help="probe liveness and print intended status, but do not write to the DB",
    )
    args = parser.parse_args()

    if not WORKSPACE_ID:
        print("ERROR: MULTICA_WORKSPACE_ID not set (.env or env)", file=sys.stderr)
        return 2

    any_error = False
    for provider, label, probe in _RUNTIMES:
        online = bool(probe())
        status = "online" if online else "offline"
        if args.dry_run:
            print(f"[dry-run] {label} ({provider}) -> {status}")
            continue
        ok, detail = _update_runtime(provider, online)
        if ok:
            print(f"{label} ({provider}) -> {status}")
        else:
            any_error = True
            print(f"{label} ({provider}) -> {status} FAILED: {detail}", file=sys.stderr)

    return 1 if any_error else 0


if __name__ == "__main__":
    raise SystemExit(main())
