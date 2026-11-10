#!/usr/bin/env python3
"""quiet_for_probe.py - chat/QA drivers yield to the voice probe (drivers' side of landing_lib.sh `landing_quiet_chat_take`).

Unwrapped QA drivers made probes fail falsely (2026-11: prewarm timeouts, brain_ms 2.9x): wrap EACH chat turn at the live zoe-data.
Locks (flock(2), same files as the lander): ZOE_PROBE_GATE_LOCK - probe EXCLUSIVE, a turn SHARED for an instant before it starts (so
drivers cannot starve the probe); ZOE_CHAT_TURN_LOCK - a turn SHARED while it runs, the probe EXCLUSIVE after the gate (waits for them).

Python:   sys.path.insert(0, "/home/zoe/assistant/scripts/landing"); from quiet_for_probe import chat_turn
          with chat_turn():          # waits while a probe runs (default up to 3600 s, then raises ProbeBusy)
              reply = post_the_turn()
Shell:    quiet_for_probe.py turn [--wait S] [--turn-timeout S] -- <command...>   run one turn as a command (its exit code; 75 = gave up)
          quiet_for_probe.py wait [--wait S]                                      just wait until no probe is running (0, or 75)
          quiet_for_probe.py status                                               prints "probe active" (exit 1) or "idle" (exit 0)
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

EX_TEMPFAIL = 75
POLL_S = 0.5
DEFAULT_WAIT_S = 3600.0   # a probe runs ~15-30 min; past an hour something is wrong and the driver should hear about it


class ProbeBusy(TimeoutError):
    """A probe held the gate for the whole wait."""


# the lock paths are registered flags (services/zoe-data/flag_registry.py), read through typed_env at call time; zoe-data goes at the
# END of sys.path so nothing in it shadows a driver's own modules
sys.path.append(str(Path(__file__).resolve().parents[2] / "services" / "zoe-data"))
import typed_env  # noqa: E402


def gate_path() -> str:
    return typed_env.flag("ZOE_PROBE_GATE_LOCK")


def turn_path() -> str:
    return typed_env.flag("ZOE_CHAT_TURN_LOCK")


def _open(path: str) -> int:
    return os.open(path, os.O_CREAT | os.O_RDWR, 0o666)


def _lock_shared(fd: int, deadline: float) -> bool:
    """Poll for a SHARED lock (flock has no timeout). Polling also never queues behind the probe's exclusive request."""
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            if time.monotonic() >= deadline:
                return False
            time.sleep(POLL_S)


def probe_active() -> bool:
    fd = _open(gate_path())
    try:
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        return False
    except BlockingIOError:
        return True
    finally:
        os.close(fd)


@contextlib.contextmanager
def chat_turn(wait_s: float = DEFAULT_WAIT_S, *, announce=None):
    """Hold the turn lock SHARED for one chat turn, entered only while no probe holds the gate."""
    deadline = time.monotonic() + wait_s
    gate, turn = _open(gate_path()), _open(turn_path())
    try:
        if probe_active() and announce:
            announce("voice probe running - waiting for it to finish before the next chat turn")
        while True:
            if not _lock_shared(gate, deadline):
                raise ProbeBusy(f"a voice probe held {gate_path()} for {wait_s:.0f}s")
            # Gate held shared, so no probe can be holding the turn lock exclusively right now: this only waits for a probe
            # that took both locks in the instant before our gate poll, and then we start over at the gate.
            got = _lock_shared(turn, time.monotonic() + POLL_S * 4)
            fcntl.flock(gate, fcntl.LOCK_UN)
            if got:
                break
            if time.monotonic() >= deadline:
                raise ProbeBusy(f"{turn_path()} stayed held for {wait_s:.0f}s")
        os.close(gate)
        gate = -1
        yield
    finally:
        if gate >= 0:
            os.close(gate)
        os.close(turn)   # releases the shared turn hold


def _say(msg: str) -> None:
    print(f"quiet_for_probe: {msg}", file=sys.stderr, flush=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("turn", help="run one chat turn (a command) while no probe runs")
    t.add_argument("--wait", type=float, default=DEFAULT_WAIT_S)
    t.add_argument("--turn-timeout", type=float, default=600.0, help="kill the turn command after this long (the probe drains in-flight turns)")
    t.add_argument("command", nargs=argparse.REMAINDER)
    w = sub.add_parser("wait", help="wait until no probe is running")
    w.add_argument("--wait", type=float, default=DEFAULT_WAIT_S)
    sub.add_parser("status", help="exit 1 while a probe runs, 0 when idle")
    a = ap.parse_args(argv)
    if a.cmd == "status":
        busy = probe_active()
        print("probe active" if busy else "idle")
        return 1 if busy else 0
    if a.cmd == "wait":
        try:
            with chat_turn(a.wait, announce=_say):   # entering = no probe holds the gate; nothing runs inside
                pass
        except ProbeBusy as e:
            _say(f"gave up: {e}")
            return EX_TEMPFAIL
        return 0
    cmd = a.command[1:] if a.command[:1] == ["--"] else a.command
    if not cmd:
        ap.error("turn needs a command after --")
    try:
        with chat_turn(a.wait, announce=_say):
            proc = subprocess.Popen(cmd, start_new_session=True)
            try:
                return proc.wait(timeout=a.turn_timeout)
            except subprocess.TimeoutExpired:
                # kill the whole process group BEFORE the turn lock is released: a child still talking to zoe-data is still a turn
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
                _say(f"turn command killed after {a.turn_timeout:.0f}s")
                return 124
    except ProbeBusy as e:
        _say(f"gave up, turn NOT run: {e}")
        return EX_TEMPFAIL


if __name__ == "__main__":
    sys.exit(main())
