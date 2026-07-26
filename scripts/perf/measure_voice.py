#!/usr/bin/env python3
"""Voice end-to-end speed + correctness probe (wraps the replay corpus).

The brain TTFT probe (``measure_speed.py``) measures the LLM in isolation. This
script measures the **whole voice path** Zoe actually runs on the panel —
STT (Moonshine v2) → semantic_router → fast_tiers → Gemma brain — by replaying
Jason's saved utterance corpus through the LIVE pipeline and reporting, per
turn AND aggregated:

  * **stt_ms**       — Moonshine transcription latency
  * **resolve_ms**   — fast-tier/router resolution latency
  * **brain_ms**     — Gemma brain latency on fall-through
  * **e2e_ms**       — stt + resolve + brain = time to spoken-ready text
                       (the pre-TTS end-to-end the user waits through)
  * **verdict mix**  — OK / CANT_DO / EMPTY / ERROR / DEFERRED, so a speed
                       change that BREAKS function (Zoe stops doing the right
                       thing) is caught, not just a slower-but-correct one.

This is a thin orchestrator over the canonical replay harness
``services/zoe-data/tests/replay_samples.py`` (the single source of truth for
the live voice path) — it shells out with ``--brain --json`` and aggregates the
per-turn timings it already records. We do NOT duplicate the pipeline wiring.

SAFETY: read-only. Runs the replay in its DEFAULT dry mode (writes are PLANNED,
never fulfilled — ``allow_writes=False``), against the saved DEMO corpus and the
'jason' memory for *reads* only. It never passes ``--execute``, never mutates
the DB, and never triggers the consolidation sweep.

CI gate: requires ``ZOE_PERF=1`` AND a reachable brain. Also needs the live
service env (POSTGRES_URL etc.) — so it must run on the Jetson host from the
real ``services/zoe-data`` checkout, not a GitHub runner. Without ``ZOE_PERF=1``
it exits 0 with a skip notice.

Usage (run on the Jetson host):
    ZOE_PERF=1 python3 scripts/perf/measure_voice.py                 # newest 10 samples
    ZOE_PERF=1 python3 scripts/perf/measure_voice.py --last 30       # newest 30
    ZOE_PERF=1 python3 scripts/perf/measure_voice.py --json voice.json
    # ...including from a git WORKTREE, with no flag: --service-dir auto-resolves
    # to the live env (this repo's services/zoe-data if it has a .env, else the
    # MAIN worktree's). Pass it explicitly only to override that:
    ZOE_PERF=1 python3 scripts/perf/measure_voice.py --service-dir /path/to/services/zoe-data
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from service_dir import (  # noqa: E402 — sibling-import convention, scripts/ is not a package
    resolve_service_dir,
    SERVICE_DIR_HELP,
)


def _median(values: list[float]) -> dict:
    if not values:
        return {}
    vals = sorted(values)
    n = len(vals)

    def pct(p: float) -> float:
        if n == 1:
            return vals[0]
        return vals[min(n - 1, max(0, int(round(p * (n - 1)))))]

    return {
        "median": round(statistics.median(vals), 1),
        "p10": round(pct(0.10), 1),
        "p90": round(pct(0.90), 1),
        "min": round(vals[0], 1),
        "max": round(vals[-1], 1),
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--last", type=int, default=10, help="newest N samples to replay")
    ap.add_argument("--since", help="only samples whose filename sorts >= this")
    ap.add_argument("--user", default="jason", help="user_id for memory recall (reads only)")
    ap.add_argument("--service-dir", default=None, help=SERVICE_DIR_HELP)
    ap.add_argument("--json", help="write aggregated machine-readable results here")
    ap.add_argument("--timeout", type=int, default=600, help="replay subprocess timeout (s)")
    args = ap.parse_args()

    if os.environ.get("ZOE_PERF") != "1":
        print("ZOE_PERF != 1 — skipping live voice e2e probe (set ZOE_PERF=1 to run).")
        return 0

    # Same ladder the probe walks (scripts/lib/service_dir.py): explicit flag
    # always wins, else this repo's services/zoe-data if it has a .env, else the
    # MAIN worktree's — so a DIRECT run from a git worktree needs no flag. When
    # nothing resolves it returns the in-tree default, and the loud skips below
    # still fire: the ladder fixes the DEFAULT, never the failure mode.
    service_dir = str(resolve_service_dir(args.service_dir))
    replay = os.path.join(service_dir, "tests", "replay_samples.py")
    if not os.path.exists(replay):
        print(f"replay harness not found at {replay} — skipping.", file=sys.stderr)
        return 0

    # The replay harness loads <service_dir>/.env for POSTGRES_URL etc. If that
    # env is missing (no live env anywhere, or an explicit --service-dir that has
    # none), it can't reach the DB. Skip cleanly rather than fail CI — the caller
    # (voice_regression_probe) turns this result-less skip into status=error.
    if not os.path.exists(os.path.join(service_dir, ".env")):
        print(f"no .env in {service_dir} (live service env required) — skipping.", file=sys.stderr)
        return 0

    with tempfile.NamedTemporaryFile("r", suffix=".json", delete=False) as tf:
        replay_json = tf.name

    cmd = [
        sys.executable, "tests/replay_samples.py",
        "--brain", "--user", args.user, "--json", replay_json,
    ]
    if args.since:
        cmd += ["--since", args.since]
    else:
        cmd += ["--last", str(args.last)]

    print(f"Replaying voice corpus via {replay}\n  (dry/read-only, +brain)\n")
    # Single outer finally so EVERY exit path (timeout, non-zero exit, parse
    # error, success) removes the temp file — no leak on the Jetson's limited
    # storage across repeated probes.
    try:
        return _run_and_report(cmd, service_dir, replay_json, args)
    finally:
        try:
            os.unlink(replay_json)
        except OSError:
            pass


def _run_and_report(cmd: list[str], service_dir: str, replay_json: str, args) -> int:
    """Run the replay subprocess and aggregate its JSON. Caller owns temp cleanup."""
    try:
        proc = subprocess.run(
            cmd, cwd=service_dir, timeout=args.timeout,
            capture_output=True, text=True,
        )
    except subprocess.TimeoutExpired:
        print(f"replay timed out after {args.timeout}s", file=sys.stderr)
        return 1

    # Surface the harness's own per-turn output (heard / route / spoken).
    sys.stdout.write(proc.stdout)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        print(f"\nreplay exited {proc.returncode}", file=sys.stderr)
        return proc.returncode or 1

    try:
        with open(replay_json) as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"could not read replay results: {exc}", file=sys.stderr)
        return 1

    rows = data.get("rows", [])
    counts = data.get("counts", {})

    # Per-turn e2e = stt + resolve + brain (= time-to-spoken-ready text).
    e2e_rows = []
    for r in rows:
        stt = r.get("stt_ms") or 0
        resolve = r.get("resolve_ms") or 0
        brain = r.get("brain_ms") or 0
        e2e = stt + resolve + brain
        e2e_rows.append({
            "file": r.get("file"), "verdict": r.get("verdict"),
            "stt_ms": stt, "resolve_ms": resolve, "brain_ms": brain, "e2e_ms": e2e,
        })

    agg = {
        "stt_ms": _median([r["stt_ms"] for r in e2e_rows]),
        "resolve_ms": _median([r["resolve_ms"] for r in e2e_rows]),
        "brain_ms": _median([r["brain_ms"] for r in e2e_rows if r["brain_ms"] > 0]),
        "e2e_ms": _median([r["e2e_ms"] for r in e2e_rows]),
    }

    print("\n" + "═" * 60)
    print("VOICE END-TO-END (median ms over corpus)")
    for k in ("stt_ms", "resolve_ms", "brain_ms", "e2e_ms"):
        s = agg[k]
        if s:
            print(f"  {k:11s}: median={s['median']}  p10={s['p10']}  p90={s['p90']}  max={s['max']}")
    print(f"\n  verdicts   : " + "  ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    bad = counts.get("CANT_DO", 0) + counts.get("ERROR", 0)
    if bad:
        print(f"  ⚠ {bad} turns failed function (CANT_DO/ERROR) — a speed change that breaks Zoe.")

    report = {
        "kind": "voice_e2e",
        "service_dir": service_dir,
        "n_samples": len(e2e_rows),
        "aggregate_ms": agg,
        "verdicts": counts,
        "rows": e2e_rows,
    }
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(report, fh, indent=2)
        print(f"\nwrote {args.json}")

    # Non-zero exit if any turn broke function — lets CI/operators gate on it.
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
