#!/usr/bin/env python3
"""A/B Kokoro sidecar RSS with and without the idle CUDA-pool trim (ZOE_KOKORO_IDLE_TRIM_S).

Starts a THROWAWAY copy of the sidecar on a spare localhost port (never :10201), with its
phrase cache pointed at a temp dir and persistence off so the live ~/.zoe/kokoro_cache is
untouched. Per variant: wait healthy, record RSS (warm), synthesise a fixed set of texts of
growing length (latency each), record RSS right after the burst and again after an idle wait
longer than the trim delay, then kill ONLY the PID it started.

A second Kokoro is ~2.3 GB, so this refuses to start unless MemAvailable >= --min-avail-gb
(3.0) and it holds /tmp/zoe-voice-harness.lock (the repo's Kokoro-load lock) for the run.
Never run it at 01:50-04:55 or while a landing holds the lock.

    python3 scripts/perf/measure_kokoro_idle_trim_ab.py --wait-s 1800 --out /tmp/kokoro_trim_ab.json
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import shutil
import signal
import statistics
import subprocess
import tempfile
import time
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = os.path.expanduser("~/.zoe/venvs/kokoro-py310/bin/python")
BASE = ("The garden needs watering and the washing is still on the line. " * 12).split(". ")
TEXTS = [". ".join(BASE[: 1 + i % 11]).strip() + "." for i in range(36)] + [
    "Okay.", "Done.", "Sure thing, one moment.", "It is half past six and the weather is clear."] * 4


LOCK = open("/tmp/zoe-voice-harness.lock", "w")


LIVE_PORT = 10201
MIN_GATE_GB = 3.0  # floor: --min-avail-gb may raise the gate, never lower it


MAX_IDLE_WAIT_S = 300.0


def _guard() -> None:
    """Abort (the finally block kills our child and releases the lock) if the night window opens mid-run."""
    if in_night_window():
        raise SystemExit("night window (01:50-04:55) reached mid-run; aborting")


def _answers(port: int) -> bool:
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1).read()
        return True
    except Exception:
        return False


def in_night_window(now: datetime | None = None) -> bool:
    """01:50-04:55 local: the 12B night window; nothing of ours may run then."""
    now = now or datetime.now()
    t = now.hour * 60 + now.minute
    return 110 <= t < 295


def avail_gb() -> float:
    for line in open("/proc/meminfo"):
        if line.startswith("MemAvailable"):
            return int(line.split()[1]) / 1048576
    return 0.0


def rss_mb(pid: int) -> int:
    for line in open(f"/proc/{pid}/status"):
        if line.startswith("VmRSS"):
            return int(line.split()[1]) // 1024
    return -1


def post(port: int, text: str) -> float:
    t = time.perf_counter()
    req = urllib.request.Request(f"http://127.0.0.1:{port}/synthesize", data=json.dumps(
        {"text": text, "voice": "af_sky"}).encode(), headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=60).read()
    return (time.perf_counter() - t) * 1000


def run_variant(trim_s: float, port: int, gate: float, wait_s: float, idle_wait: float) -> dict:
    if port == LIVE_PORT:
        raise SystemExit("refusing the live sidecar port")
    gate = max(gate, MIN_GATE_GB)
    # Poll for headroom WITHOUT holding the voice-harness lock (holding it while waiting would
    # stall every landing); take it only once the gate is met, and re-check after taking it.
    deadline = time.time() + wait_s
    while True:
        if in_night_window():
            raise SystemExit("inside the 01:50-04:55 night window; not starting")
        if avail_gb() >= gate:
            try:
                fcntl.flock(LOCK, fcntl.LOCK_EX | fcntl.LOCK_NB)
                if avail_gb() >= gate:
                    break
                fcntl.flock(LOCK, fcntl.LOCK_UN)
            except OSError:
                pass
        if time.time() >= deadline:
            raise SystemExit(f"gate not met (MemAvailable {avail_gb():.2f} GB, need {gate}) or lock busy; not starting")
        time.sleep(3)
    if port == LIVE_PORT or _answers(port):
        raise SystemExit(f"port {port} is the live sidecar or already answering; refusing (pick a free spare port)")
    tmp = tempfile.mkdtemp(prefix="kokoro_ab_")
    env = dict(os.environ, KOKORO_SIDECAR_PORT=str(port), ZOE_KOKORO_CACHE_DIR=tmp, ZOE_KOKORO_CACHE_PERSIST="0",
               ZOE_KOKORO_BACKEND="pytorch", MALLOC_ARENA_MAX="2", MALLOC_TRIM_THRESHOLD_="131072",
               ZOE_KOKORO_IDLE_TRIM_S=str(int(trim_s)), PYTHONPATH=os.path.expanduser("~/.local/lib/python3.10/site-packages"),
               LD_LIBRARY_PATH=os.path.expanduser("~/.local/lib") + ":/usr/local/cuda-12.6/lib64:/usr/lib/aarch64-linux-gnu")
    p = subprocess.Popen([PY, str(ROOT / "scripts" / "setup" / "kokoro_sidecar.py")], env=env,
                         stdout=subprocess.DEVNULL, stderr=open(f"/tmp/kokoro_ab_{trim_s}.log", "w"))
    try:
        device = None
        t0 = time.time()
        while time.time() - t0 < 300:
            if p.poll() is not None:  # never accept a /health answer from anything but our own child
                raise SystemExit(f"sidecar exited rc={p.returncode}; /tmp/kokoro_ab_{trim_s}.log")
            try:
                body = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2))
                if body.get("pipeline_loaded") and p.poll() is None:
                    device = body.get("device")
                    if device != "cuda":  # CPU fallback would test an unexercised treatment
                        raise SystemExit(f"throwaway sidecar loaded on {device!r}, not cuda; discarding this run")
                    break
            except Exception:
                pass
            time.sleep(1)
        if device is None:
            raise SystemExit("throwaway sidecar never became healthy within 300 s")
        _guard()
        time.sleep(20)  # let the background phrase pre-cache finish
        rss_warm = rss_mb(p.pid)
        lat = [post(port, t) for t in TEXTS]
        _guard()
        rss_burst = rss_mb(p.pid)
        time.sleep(min(idle_wait, MAX_IDLE_WAIT_S))
        _guard()
        rss_idle = rss_mb(p.pid)
        # Unique suffix per request: the runtime phrase cache is NOT disabled by CACHE_PERSIST=0, so
        # replaying burst texts would return X-Cache hits and never touch the GPU.
        late = [post(port, f"{t.rstrip('.')} again number {i}.") for i, t in enumerate(TEXTS[:6])]
        lat.sort()
        return {"device": device, "idle_trim_s": trim_s, "rss_warm_mb": rss_warm, "rss_after_burst_mb": rss_burst,
                "rss_after_idle_mb": rss_idle, "synth_ms_p50": round(statistics.median(lat)),
                "synth_ms_p95": round(lat[int(.95 * len(lat))]),
                "first_after_idle_ms": [round(x) for x in late]}
    finally:
        p.send_signal(signal.SIGTERM)
        try:
            p.wait(timeout=20)
        except subprocess.TimeoutExpired:
            p.kill()
        fcntl.flock(LOCK, fcntl.LOCK_UN)
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=10299)
    ap.add_argument("--min-avail-gb", type=float, default=3.0)
    ap.add_argument("--wait-s", type=float, default=0.0)
    ap.add_argument("--trims", default="0,3,0,3", help="ZOE_KOKORO_IDLE_TRIM_S per run (0 = off)")
    ap.add_argument("--idle-wait", type=float, default=12.0)
    ap.add_argument("--out", default="/tmp/kokoro_trim_ab.json")
    a = ap.parse_args()
    if a.port == LIVE_PORT:
        raise SystemExit("refusing the live sidecar port")
    runs = []
    for tr in a.trims.split(","):
        if in_night_window():
            raise SystemExit("inside the 01:50-04:55 night window; not starting")
        r = run_variant(float(tr), a.port, a.min_avail_gb, a.wait_s, a.idle_wait)
        runs.append(r)
        print(json.dumps(r), flush=True)
    Path(a.out).write_text(json.dumps(runs, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
