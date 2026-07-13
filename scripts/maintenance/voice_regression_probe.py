#!/usr/bin/env python3
"""Zoe voice regression + speed probe — the fleet-shared, evolving voice gate.

Replays a slice of Jason's real-voice corpus (~/.zoe-voice-samples) through the
LIVE voice path via scripts/perf/measure_voice.py, then compares this run against
a saved baseline on TWO axes:

  * function (regression): the OK rate over the corpus must not drop, and the
    CANT_DO/ERROR count must not rise — i.e. Zoe must not stop being able to do
    something she could do before ("can't do it" = a bug, see memory
    project_voice_recording_test_loop).
  * speed: per-stage medians (STT / brain / end-to-end) must not regress beyond
    a ratio + absolute-ms gate (same shape as scripts/maintenance/zoe_latency_probe.py).

Designed to run on demand OR on a schedule (scripts/setup/systemd/zoe-voice-
regression.{service,timer}). Every newly captured sample (ZOE_VOICE_SAVE_AUDIO)
becomes part of the bar, so the test evolves with real use.

CAVEAT (do not misread the numbers): the replay harness uses WARM models and
stops before TTS, so its timings UNDERSTATE real live latency — this probe tracks
*relative drift vs baseline*, not absolute live performance. See
docs/knowledge/voice-pipeline.md.

Examples:
    # establish/refresh the baseline (run when the path is known-good):
    python3 scripts/maintenance/voice_regression_probe.py --update-baseline
    # routine check (exits non-zero on a function or speed regression):
    python3 scripts/maintenance/voice_regression_probe.py --samples 20
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
MEASURE = REPO / "scripts" / "perf" / "measure_voice.py"
LOCK = "/tmp/zoe-voice-harness.lock"  # shared with all voice harness runs — no concurrent Kokoro OOM
DEFAULT_BASELINE = Path.home() / ".cache" / "zoe" / "voice_regression_baseline.json"
DEFAULT_RESULTS = Path.home() / ".cache" / "zoe" / "voice_regression_last.json"
DEFAULT_TREND = Path.home() / ".cache" / "zoe" / "voice_regression_trend.jsonl"
RATIO_FLOOR_MS = 100.0  # below this absolute delta, a high ratio is treated as noise


def mem_available_mb() -> int:
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
    except Exception:
        pass
    return 0


def run_measure(samples: int, service_dir: str, user: str, timeout: int) -> dict[str, Any]:
    """Run measure_voice.py under the shared flock and return its aggregated JSON."""
    with tempfile.NamedTemporaryFile("r", suffix=".json", delete=False) as tf:
        out_json = tf.name
    try:
        # `flock <file> <cmd> [args...]` runs the command WITHOUT a shell, so a path
        # with spaces or a shell metachar in --user/--service-dir can't be split or
        # interpreted. ZOE_PERF is passed via env, not a shell prefix. flock still
        # serializes runs so two Kokoro/replay loads (~2.3GB each) can't run at once.
        cmd = [
            "flock", LOCK,
            "python3", str(MEASURE),
            "--last", str(samples), "--user", user,
            "--service-dir", service_dir, "--json", out_json, "--timeout", str(timeout),
        ]
        proc = subprocess.run(
            cmd, cwd=str(REPO), capture_output=True, text=True,
            timeout=timeout + 120, env={**os.environ, "ZOE_PERF": "1"},
        )
        if proc.returncode not in (0, 1):  # 1 = measure_voice's own "a turn broke function"
            raise RuntimeError(f"measure_voice failed (rc={proc.returncode}): {proc.stderr[-400:]}")
        with open(out_json) as fh:
            return json.load(fh)
    finally:
        try:
            os.unlink(out_json)
        except OSError:
            pass


def summarize(report: dict[str, Any]) -> dict[str, Any]:
    agg = report.get("aggregate_ms", {}) or {}
    verdicts = report.get("verdicts", {}) or {}
    total = sum(verdicts.values()) or 1
    ok = verdicts.get("OK", 0)
    fail = verdicts.get("CANT_DO", 0) + verdicts.get("ERROR", 0)
    medians = {k: (agg.get(k) or {}).get("median") for k in ("stt_ms", "brain_ms", "e2e_ms")}
    return {
        "n_samples": report.get("n_samples", 0),
        "ok_rate": round(ok / total, 3),
        "ok": ok, "fail": fail, "total": total,
        "verdicts": verdicts,
        "medians_ms": medians,
    }


def compare(cur: dict[str, Any], baseline: dict[str, Any], warn_ratio: float, warn_ms: float) -> list[str]:
    warnings: list[str] = []
    base = baseline.get("summary") if isinstance(baseline, dict) else None
    if not isinstance(base, dict):
        return warnings
    # Function regression — Zoe must not lose the ability to handle the corpus.
    base_ok = base.get("ok_rate")
    if isinstance(base_ok, (int, float)) and cur["ok_rate"] < base_ok - 0.001:
        warnings.append(f"FUNCTION: OK rate {cur['ok_rate']:.3f} vs baseline {base_ok:.3f} "
                        f"(fail {cur['fail']} vs {base.get('fail')})")
    # Speed regression — per stage, ratio AND absolute gate.
    base_med = base.get("medians_ms", {}) if isinstance(base.get("medians_ms"), dict) else {}
    for stage, cur_ms in cur["medians_ms"].items():
        base_ms = base_med.get(stage)
        if not isinstance(cur_ms, (int, float)) or not isinstance(base_ms, (int, float)) or base_ms <= 0:
            continue
        delta, ratio = cur_ms - base_ms, cur_ms / base_ms
        if (ratio >= warn_ratio and delta >= RATIO_FLOOR_MS) or (delta >= warn_ms):
            warnings.append(f"SPEED {stage}: {cur_ms:.0f}ms vs baseline {base_ms:.0f}ms "
                            f"({ratio:.2f}x, +{delta:.0f}ms)")
    return warnings


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def cleanup_replay_artifacts(run_started_utc: str, args) -> bool:
    """Soft-delete replay artifacts: rows created during the probe window and
    owned by the replay identities only.

    The replay corpus executes REAL commands through the live pipeline ("add
    bread to the shopping list", "dentist appointment at 2pm", …), so every run
    would otherwise accumulate junk in the calendar/lists (operator bug report
    2026-07-13). Scope is deliberately narrow on BOTH axes: created_at within
    this run's window AND user_id in {probe user, 'guest'} — a family member's
    row written during the window under any other account is never touched.
    Reversible soft-delete (deleted=1); counts printed for the run log.

    Returns True on success (or intentional skip), False on failure — the
    caller surfaces a failed cleanup in the exit code so a silently dirty
    calendar can't hide behind a green probe.
    """
    if getattr(args, "no_cleanup", False):
        return True
    try:
        import asyncpg  # hard requirement: a probe env without asyncpg must be visible
    except ImportError as exc:
        print(f"cleanup: FAILED — asyncpg unavailable in the probe environment: {exc}", file=sys.stderr)
        return False
    dsn = os.environ.get("POSTGRES_URL", "")
    if not dsn:
        env_file = REPO / "services" / "zoe-data" / ".env"
        try:
            with open(env_file) as fh:
                for line in fh:
                    if line.startswith("POSTGRES_URL="):
                        dsn = line[len("POSTGRES_URL="):].strip().strip('"').strip("'")
                        break
        except OSError:
            pass
    if not dsn:
        print("cleanup: FAILED — POSTGRES_URL unavailable; replay artifacts were NOT swept", file=sys.stderr)
        return False
    replay_users = [getattr(args, "user", "jason") or "jason", "guest"]
    try:
        import asyncio

        async def _run() -> tuple[str, str]:
            conn = await asyncpg.connect(dsn)
            try:
                ev = await conn.execute(
                    "UPDATE events SET deleted = 1, updated_at = NOW() "
                    "WHERE deleted = 0 AND created_at >= $1::timestamptz AND user_id = ANY($2)",
                    run_started_utc + "+00", replay_users,
                )
                li = await conn.execute(
                    "UPDATE list_items SET deleted = 1, updated_at = NOW() "
                    "WHERE deleted = 0 AND created_at >= $1::timestamptz AND list_id IN "
                    "(SELECT id FROM lists WHERE user_id = ANY($2))",
                    run_started_utc + "+00", replay_users,
                )
                return ev, li
            finally:
                await conn.close()

        ev, li = asyncio.run(_run())
        print(f"cleanup: replay-window artifacts soft-deleted (owners {replay_users}) — events: {ev}, list_items: {li}")
        return True
    except Exception as exc:
        print(f"cleanup: FAILED — replay artifacts were NOT swept: {exc}", file=sys.stderr)
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description="Zoe voice regression + speed probe.")
    ap.add_argument("--samples", type=int, default=int(os.environ.get("ZOE_VOICE_PROBE_SAMPLES", "20")),
                    help="newest N corpus samples to replay")
    ap.add_argument("--user", default=os.environ.get("ZOE_VOICE_PROBE_USER", "jason"))
    ap.add_argument("--service-dir", default=str(REPO / "services" / "zoe-data"))
    ap.add_argument("--timeout", type=int, default=int(os.environ.get("ZOE_VOICE_PROBE_TIMEOUT_S", "900")))
    ap.add_argument("--baseline", type=Path, default=Path(os.environ.get("ZOE_VOICE_BASELINE", DEFAULT_BASELINE)))
    ap.add_argument("--results", type=Path, default=Path(os.environ.get("ZOE_VOICE_RESULTS", DEFAULT_RESULTS)))
    ap.add_argument("--trend", type=Path, default=Path(os.environ.get("ZOE_VOICE_TREND", DEFAULT_TREND)))
    ap.add_argument("--update-baseline", action="store_true", help="Save this run as the new comparison baseline.")
    ap.add_argument("--warn-ratio", type=float, default=float(os.environ.get("ZOE_VOICE_WARN_RATIO", "1.5")))
    ap.add_argument("--warn-ms", type=float, default=float(os.environ.get("ZOE_VOICE_WARN_MS", "1500")))
    ap.add_argument("--min-mem-mb", type=int, default=int(os.environ.get("ZOE_VOICE_PROBE_MIN_MEM_MB", "1500")),
                    help="skip (exit 0) if available memory is below this — never OOM the live box")
    ap.add_argument("--no-cleanup", action="store_true",
                    help="skip the post-run replay-artifact cleanup (soft-delete of rows created during the replay window)")
    args = ap.parse_args()

    avail = mem_available_mb()
    if avail < args.min_mem_mb:
        print(f"SKIP: available memory {avail}MB < {args.min_mem_mb}MB threshold — "
              f"deferring to avoid OOM on the live box.")
        return 0

    run_started_utc = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    try:
        report = run_measure(args.samples, args.service_dir, args.user, args.timeout)
    except Exception as exc:
        print(f"ERROR: voice probe could not run: {exc}", file=sys.stderr)
        cleanup_replay_artifacts(run_started_utc, args)   # even a failed run may have executed turns
        return 2

    summary = summarize(report)
    payload = {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "summary": summary,
    }
    write_json(args.results, payload)
    # Append to the trend log so drift is visible over time (one line per run).
    try:
        args.trend.parent.mkdir(parents=True, exist_ok=True)
        with open(args.trend, "a") as fh:
            fh.write(json.dumps(payload, separators=(",", ":")) + "\n")
    except OSError:
        pass

    baseline = {}
    try:
        baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    except Exception:
        pass
    warnings = compare(summary, baseline, args.warn_ratio, args.warn_ms)

    m = summary["medians_ms"]
    print(f"Zoe voice regression probe — {summary['n_samples']} samples, "
          f"OK {summary['ok']}/{summary['total']} ({summary['ok_rate']:.0%}), fail={summary['fail']}")
    print(f"  medians: STT={m.get('stt_ms')}  brain={m.get('brain_ms')}  e2e={m.get('e2e_ms')}  (ms; warm-harness, relative only)")
    for w in warnings:
        print(f"WARN {w}")

    if args.update_baseline or not args.baseline.exists():
        write_json(args.baseline, payload)
        print(f"Baseline saved: {args.baseline}")
    print(f"Results: {args.results}  Trend: {args.trend}")

    cleanup_ok = cleanup_replay_artifacts(run_started_utc, args)

    # a failed sweep is a warning-level exit: results are valid but the
    # calendar/lists are dirty and the systemd unit shows non-zero.
    return 1 if (warnings or not cleanup_ok) else 0


if __name__ == "__main__":
    raise SystemExit(main())
