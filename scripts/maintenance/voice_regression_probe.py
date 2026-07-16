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

RESULT ARTIFACT CONTRACT ("a gate that can silently not-run is not a gate"):
every run — pass, fail, SKIP (box too tight), or ERROR (could not run) — writes a
durable, machine-readable result to --results (default
~/.cache/zoe/voice_regression_last.json):

    {status: pass|fail|skip|error, timestamp, said_vs_did_regressions,
     per_stage_speed_deltas, baseline_ref, reason, summary}

A skip/timeout/error MUST leave an artifact with status != "pass" — never an
ABSENT file that a downstream checker could misread as "nothing wrong". The
deploy-path checker scripts/maintenance/voice_gate_check.py reads exactly this
contract to decide whether a voice-path deploy is allowed to proceed.

Examples:
    # establish/refresh the baseline (run when the path is known-good):
    python3 scripts/maintenance/voice_regression_probe.py --update-baseline
    # routine check (exits non-zero on a function or speed regression):
    python3 scripts/maintenance/voice_regression_probe.py --samples 20
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
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
        # NO inner flock here: the harness lock (/tmp/zoe-voice-harness.lock)
        # is the CALLER'S boundary — the systemd unit and the documented manual
        # invocation both wrap the probe in `flock <lock> python3 probe.py`.
        # Re-taking the same lock in this child was a guaranteed deadlock: the
        # parent held it, the child blocked forever, and every run (nightly
        # AND manual) timed out at ~17 min. The gate never once succeeded.
        # Args are passed WITHOUT a shell, so paths with spaces/metachars are
        # safe; ZOE_PERF goes via env, not a shell prefix.
        cmd = [
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
        if not os.path.getsize(out_json):
            # measure_voice exits 0 on its skip paths (no .env in --service-dir,
            # missing replay harness) without writing JSON — surface that
            # instead of a cryptic JSONDecodeError. Worktree runs must point
            # --service-dir at the LIVE services/zoe-data (env lives there).
            raise RuntimeError(
                "measure_voice skipped without results — likely no .env in "
                f"--service-dir; stderr: {proc.stderr[-300:]}"
            )
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


def stage_speed_deltas(summary: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    """Per-stage medians this run vs the baseline — recorded on EVERY run so the
    result artifact carries the raw speed picture even when nothing regressed.
    The pass/fail DECISION stays in compare(); this only records the numbers."""
    base = baseline.get("summary") if isinstance(baseline, dict) else None
    base_med = base.get("medians_ms", {}) if isinstance(base, dict) else {}
    if not isinstance(base_med, dict):
        base_med = {}
    out: dict[str, Any] = {}
    for stage, cur_ms in (summary.get("medians_ms") or {}).items():
        base_ms = base_med.get(stage)
        entry: dict[str, Any] = {"cur_ms": cur_ms, "baseline_ms": base_ms}
        if isinstance(cur_ms, (int, float)) and isinstance(base_ms, (int, float)) and base_ms > 0:
            entry["delta_ms"] = round(cur_ms - base_ms, 1)
            entry["ratio"] = round(cur_ms / base_ms, 3)
        out[stage] = entry
    return out


EMPTY_SUMMARY = {"n_samples": 0, "ok_rate": 0.0, "ok": 0, "fail": 0,
                 "total": 0, "verdicts": {}, "medians_ms": {}}


def emit_result(args, *, status: str, summary: dict[str, Any],
                said_vs_did: list[str], speed_deltas: dict[str, Any],
                baseline: dict[str, Any], reason: str = "") -> dict[str, Any]:
    """Write the durable, machine-readable RESULT ARTIFACT — on EVERY exit path.

    This is the whole point of the gate's hardening: a skip / timeout / error
    leaves an artifact whose status != "pass", never an ABSENT file that a
    downstream checker could misread as "nothing wrong". voice_gate_check.py
    reads exactly this contract; keep the keys stable. `summary` and `created_at`
    are also retained for the existing router_selftrain replay_gate reader."""
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    base_summary = baseline.get("summary") if isinstance(baseline, dict) else None
    baseline_ref = {
        "path": str(args.baseline),
        "created_at": (baseline or {}).get("created_at"),
        "ok_rate": (base_summary or {}).get("ok_rate") if isinstance(base_summary, dict) else None,
    }
    payload = {
        "status": status,                       # pass | fail | skip | error
        "timestamp": ts,
        "created_at": ts,                       # back-compat: router_selftrain reads mtime + summary
        "reason": reason,
        "said_vs_did_regressions": said_vs_did,
        "per_stage_speed_deltas": speed_deltas,
        "baseline_ref": baseline_ref,
        "summary": summary,                     # back-compat: n_samples / ok_rate / medians_ms
    }
    write_json(args.results, payload)
    try:
        args.trend.parent.mkdir(parents=True, exist_ok=True)
        with open(args.trend, "a") as fh:
            fh.write(json.dumps(payload, separators=(",", ":")) + "\n")
    except OSError:
        pass
    return payload


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _dsn_from_env_file(env_file: Path) -> str:
    """Parse POSTGRES_URL out of a services `.env` file; "" if absent/unreadable."""
    try:
        with open(env_file) as fh:
            for line in fh:
                if line.startswith("POSTGRES_URL="):
                    return line[len("POSTGRES_URL="):].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def _resolve_dsn(args) -> str:
    """Resolve the Postgres DSN for the cleanup sweep. Precedence:

    1. an explicit ``POSTGRES_URL`` in the environment;
    2. ``--service-dir/.env`` — the SAME directory measure_voice.py uses to reach
       the live service, so a probe run from a git WORKTREE (which has no
       gitignored services/zoe-data/.env of its own) resolves the DSN as long as
       --service-dir points at the live services/zoe-data;
    3. ``REPO/services/zoe-data/.env`` — last-ditch fallback for the in-tree run.

    Returns "" when the DSN is genuinely unresolvable (caller must fail loudly,
    not hide a real failure behind a silent success)."""
    env_dsn = os.environ.get("POSTGRES_URL", "")
    if env_dsn:
        return env_dsn
    service_dir = getattr(args, "service_dir", None)
    if service_dir:
        dsn = _dsn_from_env_file(Path(service_dir) / ".env")
        if dsn:
            return dsn
    return _dsn_from_env_file(REPO / "services" / "zoe-data" / ".env")


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
    dsn = _resolve_dsn(args)
    if not dsn:
        print("cleanup: FAILED — POSTGRES_URL unavailable (checked env, "
              "--service-dir/.env, and REPO/services/zoe-data/.env); replay "
              "artifacts were NOT swept", file=sys.stderr)
        return False
    replay_users = [getattr(args, "user", "jason") or "jason", "guest"]
    try:
        import asyncio

        async def _run() -> tuple[str, str]:
            conn = await asyncpg.connect(dsn)
            try:
                # The replay necessarily writes AS the probe user (identity
                # threading is part of the pipeline under test), so an owner
                # filter cannot distinguish probe writes from a human's. The
                # mitigations are: an off-peak flock-serialized window, a
                # reversible soft-delete, and a PER-ROW log below so any rare
                # collision is visible in the unit journal and restorable by id.
                ev_rows = await conn.fetch(
                    "SELECT id, user_id, title FROM events "
                    "WHERE deleted = 0 AND created_at::timestamptz >= $1::timestamptz AND user_id = ANY($2)",
                    datetime.strptime(run_started_utc, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc), replay_users,
                )
                li_rows = await conn.fetch(
                    "SELECT i.id, l.user_id, i.text FROM list_items i JOIN lists l ON i.list_id = l.id "
                    "WHERE i.deleted = 0 AND i.created_at::timestamptz >= $1::timestamptz AND l.user_id = ANY($2)",
                    datetime.strptime(run_started_utc, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc), replay_users,
                )
                for r in ev_rows:
                    print(f"cleanup: sweeping event id={r['id']} owner={r['user_id']} title={r['title']!r}")
                for r in li_rows:
                    print(f"cleanup: sweeping list_item id={r['id']} owner={r['user_id']} text={r['text']!r}")
                ev = await conn.execute(
                    "UPDATE events SET deleted = 1, updated_at = NOW() WHERE id = ANY($1)",
                    [r["id"] for r in ev_rows],
                )
                li = await conn.execute(
                    "UPDATE list_items SET deleted = 1, updated_at = NOW() WHERE id = ANY($1)",
                    [r["id"] for r in li_rows],
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


def _ancestor_holds_lock() -> bool:
    """True when a PARENT process (e.g. the systemd unit's or the operator's
    `flock <lock> …` wrapper) already has the lock file open — in that case the
    run IS serialized and we must not block on our own ancestor."""
    try:
        target = os.path.realpath(LOCK)
        pid = os.getppid()
        for _ in range(15):
            if pid <= 1:
                break
            fd_dir = f"/proc/{pid}/fd"
            try:
                for fd in os.listdir(fd_dir):
                    try:
                        if os.path.realpath(os.path.join(fd_dir, fd)) == target:
                            return True
                    except OSError:
                        continue
            except OSError:
                pass
            try:
                with open(f"/proc/{pid}/status") as fh:
                    pid = next((int(l.split()[1]) for l in fh if l.startswith("PPid:")), 0)
            except (OSError, ValueError, StopIteration):
                break
    except OSError:
        pass
    return False


def _acquire_harness_lock():
    """Serialize against other harness runs even when invoked BARE.

    Returns the held fd (kept open for the process lifetime) or None when a
    parent wrapper already holds the lock. Exits(3) if another, unrelated
    harness run holds it — two Kokoro/replay loads (~2.3GB each) would OOM
    the box."""
    import fcntl
    fd = os.open(LOCK, os.O_CREAT | os.O_RDWR, 0o666)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd   # we own the lock now — bare runs are serialized too
    except BlockingIOError:
        os.close(fd)
        if _ancestor_holds_lock():
            return None   # our own flock wrapper — already serialized
        print(f"ABORT: another voice-harness run holds {LOCK} — refusing a "
              "concurrent Kokoro/replay load (would OOM the box).", file=sys.stderr)
        raise SystemExit(3)


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

    _lock_fd = _acquire_harness_lock()  # noqa: F841 — held for process lifetime

    # Baseline is loaded up front so EVERY exit path — including skip/error —
    # can record which baseline it was (or would have been) judged against.
    baseline: dict[str, Any] = {}
    try:
        baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    except Exception:
        pass

    avail = mem_available_mb()
    if avail < args.min_mem_mb:
        reason = (f"available memory {avail}MB < {args.min_mem_mb}MB threshold — "
                  "deferring to avoid OOM on the live box")
        print(f"SKIP: {reason}.")
        emit_result(args, status="skip", summary=dict(EMPTY_SUMMARY),
                    said_vs_did=[], speed_deltas={}, baseline=baseline, reason=reason)
        print(f"Results: {args.results}  (status=skip — a skip is NOT a pass)")
        return 0

    run_started_utc = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    try:
        report = run_measure(args.samples, args.service_dir, args.user, args.timeout)
    except Exception as exc:
        reason = f"voice probe could not run: {exc}"
        print(f"ERROR: {reason}", file=sys.stderr)
        emit_result(args, status="error", summary=dict(EMPTY_SUMMARY),
                    said_vs_did=[], speed_deltas={}, baseline=baseline, reason=reason)
        cleanup_replay_artifacts(run_started_utc, args)   # even a failed run may have executed turns
        return 2

    summary = summarize(report)
    warnings = compare(summary, baseline, args.warn_ratio, args.warn_ms)
    said_vs_did = [w for w in warnings if w.startswith("FUNCTION")]
    speed_deltas = stage_speed_deltas(summary, baseline)

    m = summary["medians_ms"]
    print(f"Zoe voice regression probe — {summary['n_samples']} samples, "
          f"OK {summary['ok']}/{summary['total']} ({summary['ok_rate']:.0%}), fail={summary['fail']}")
    print(f"  medians: STT={m.get('stt_ms')}  brain={m.get('brain_ms')}  e2e={m.get('e2e_ms')}  (ms; warm-harness, relative only)")
    for w in warnings:
        print(f"WARN {w}")

    if args.update_baseline or not args.baseline.exists():
        write_json(args.baseline, {
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "summary": summary,
        })
        print(f"Baseline saved: {args.baseline}")
        # This run IS the new bar now — reload so baseline_ref points at it.
        try:
            baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
        except Exception:
            pass

    cleanup_ok = cleanup_replay_artifacts(run_started_utc, args)

    # a failed sweep is a warning-level exit: results are valid but the
    # calendar/lists are dirty and the systemd unit shows non-zero.
    status = "pass" if (not warnings and cleanup_ok) else "fail"
    reason_parts = list(warnings)
    if not cleanup_ok:
        reason_parts.append("replay-artifact cleanup FAILED (calendar/lists may be dirty)")
    emit_result(args, status=status, summary=summary, said_vs_did=said_vs_did,
                speed_deltas=speed_deltas, baseline=baseline, reason="; ".join(reason_parts))

    print(f"Results: {args.results}  Trend: {args.trend}  (status={status})")
    return 1 if (warnings or not cleanup_ok) else 0


if __name__ == "__main__":
    raise SystemExit(main())
