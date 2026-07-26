#!/usr/bin/env python3
"""Bounded TCP wait — block until host:port accepts a connection, or fail LOUD.

Built for `zoe-voice-regression.service` (perf-hardening plan §3a): it is a
USER unit, so `After=docker.service` (a SYSTEM unit) cannot order it after the
dockerized Postgres — at boot the probe fired before :5432 was up and recorded
`status=error` runs (`ConnectionRefusedError ... 5432`). The unit now runs this
as an `ExecStartPre` gate instead.

Contract:
  * exit 0 as soon as ONE TCP connect to --host:--port succeeds;
  * exit 1 after --timeout seconds with a LOUD one-line diagnosis on stderr —
    the unit then fails RED with a clear reason instead of a mid-run traceback;
  * stdlib only, no repo imports — safe to run before anything else is up.

Example (as the unit uses it):
    python3 scripts/maintenance/wait_for_port.py --host 127.0.0.1 --port 5432 \
        --timeout 300 --label postgres
"""
from __future__ import annotations

import argparse
import socket
import sys
import time


def wait_for_port(
    host: str,
    port: int,
    timeout_s: float,
    interval_s: float = 5.0,
    *,
    _connect=socket.create_connection,
    _sleep=time.sleep,
    _monotonic=time.monotonic,
) -> bool:
    """True once host:port accepts a TCP connection; False when timeout_s
    elapses first. Always attempts at least once (timeout_s <= 0 => one shot)."""
    deadline = _monotonic() + timeout_s
    while True:
        try:
            conn = _connect((host, port), timeout=min(5.0, max(interval_s, 0.1)))
            try:
                conn.close()
            except OSError:
                pass
            return True
        except OSError:
            pass
        if _monotonic() >= deadline:
            return False
        _sleep(interval_s)


def main() -> int:
    ap = argparse.ArgumentParser(description="Bounded TCP wait for host:port; loud non-zero exit on timeout.")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--timeout", type=float, default=300.0, help="seconds before giving up (bounded, never forever)")
    ap.add_argument("--interval", type=float, default=5.0, help="seconds between attempts")
    ap.add_argument("--label", default="", help="human name for the service being waited on (log clarity)")
    args = ap.parse_args()
    what = f"{args.label} ({args.host}:{args.port})" if args.label else f"{args.host}:{args.port}"
    if wait_for_port(args.host, args.port, args.timeout, args.interval):
        print(f"wait_for_port: {what} is accepting connections.")
        return 0
    print(
        f"wait_for_port: TIMEOUT — {what} did not accept a TCP connection within "
        f"{args.timeout:.0f}s; failing loudly instead of letting the caller hit a "
        "mid-run ConnectionRefusedError.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
