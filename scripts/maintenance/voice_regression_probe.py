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
     per_stage_speed_deltas, baseline_ref, reason, summary,
     non_pass_streak, non_pass_alert_after, non_pass_alert}

A skip/timeout/error MUST leave an artifact with status != "pass" — never an
ABSENT file that a downstream checker could misread as "nothing wrong". The
deploy-path checker scripts/maintenance/voice_gate_check.py reads exactly this
contract to decide whether a voice-path deploy is allowed to proceed.

SKIP-STREAK ALARM ("a gate that can skip forever under green timers is not a
gate"): every run records `non_pass_streak` — the count of consecutive runs
whose status != "pass" (a real pass resets it to 0). Once the streak reaches
`--alert-after-non-pass` (default 3, env ZOE_VOICE_ALERT_NON_PASS_RUNS), the
artifact carries `non_pass_alert: true` AND the memory-skip path stops exiting
0: it exits 4 so the systemd unit/timer goes visibly red instead of recording
SUCCESS forever (same shape as the memory-loops zero-effect streak alert in
services/zoe-data/routers/system.py). fail/error runs already exit non-zero;
they count toward the streak too.

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
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from service_dir import (  # noqa: E402 — sibling-import convention, scripts/ is not a package
    resolve_service_dir as _resolve_service_dir,
    service_dir_candidates as _service_dir_candidates,
    SERVICE_DIR_HELP,
)

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


def _port_open(host: str, port: int, timeout: float = 2.0) -> bool:
    # Fail closed: this runs inside the error path that BUILDS the diagnosis —
    # a raise here would mask the original failure with a socket traceback.
    try:
        with socket.socket() as sock:
            sock.settimeout(timeout)
            return sock.connect_ex((host, port)) == 0
    except OSError:
        return False


def _diagnose_skip(service_dir: str, stt: str = "inprocess") -> list[str]:
    """Report the OBSERVED state behind a measure_voice skip — never a guessed cause.

    Returns human-readable observations in the order they are worth reading. Each
    entry is something this function actually checked just now.
    """
    env_path = os.path.join(service_dir, ".env")
    obs = []
    try:
        obs.append(f".env present ({os.path.getsize(env_path)}B)" if os.path.isfile(env_path)
                   else f"NO .env at {env_path}")
    except OSError as exc:
        obs.append(f".env unreadable at {env_path}: {exc}")
    # Mirror measure_voice.py's OWN resolution (service_dir/tests/replay_samples.py)
    # rather than a repo-relative guess, so the two cannot drift apart.
    replay = os.path.join(service_dir, "tests", "replay_samples.py")
    obs.append(f"replay harness {'present' if os.path.isfile(replay) else f'MISSING at {replay}'}")
    # Postgres is the dependency that actually bit us: the timer is Persistent=true,
    # so a missed nightly run fires during boot, ahead of the database.
    obs.append(f"postgres 127.0.0.1:5432 {'reachable' if _port_open('127.0.0.1', 5432) else 'REFUSED'}")
    if stt == "remote":
        # Remote mode's own failure modes, observed not guessed: the device token
        # (its absence makes the replay exit 1 before any sample runs) and the
        # live endpoint the WAVs go to.
        has_tok = bool((os.environ.get("ZOE_DEVICE_TOKEN")
                        or os.environ.get("DEVICE_TOKEN") or "").strip())
        obs.append(f"ZOE_DEVICE_TOKEN {'present' if has_tok else 'MISSING'}")
        obs.append(f"zoe-data 127.0.0.1:8000 {'reachable' if _port_open('127.0.0.1', 8000) else 'REFUSED'}")
    return obs


def run_measure(samples: int, service_dir: str, user: str, timeout: int, stt: str) -> dict[str, Any]:
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
            "--stt", stt,
        ]
        proc = subprocess.run(
            cmd, cwd=str(REPO), capture_output=True, text=True,
            timeout=timeout + 120, env={**os.environ, "ZOE_PERF": "1"},
        )
        if proc.returncode not in (0, 1):  # 1 = measure_voice's own "a turn broke function"
            raise RuntimeError(f"measure_voice failed (rc={proc.returncode}): {proc.stderr[-400:]}")
        if not os.path.getsize(out_json):
            # measure_voice exits 0 on SEVERAL skip paths without writing JSON.
            # This branch used to NAME one of them ("no .env in --service-dir") as
            # the cause without ever checking it. In the field that guess was wrong:
            # after the 2026-07-27 reboot the real cause was Postgres not yet
            # listening, while the .env was present and correct the whole time — so
            # the gate spent every run pointing at a healthy file. A probe that
            # asserts a cause it did not observe is worse than one that says
            # nothing. Observe first, then report what was actually seen.
            what = ("failed before aggregation (rc=1)" if proc.returncode == 1
                    else "skipped without results (rc=0)")
            raise RuntimeError(
                f"measure_voice {what} — observed: "
                f"{'; '.join(_diagnose_skip(service_dir, stt))}; "
                f"stderr: {proc.stderr[-300:]}"
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
    # NOT `or 1`: a fabricated denominator is exactly what this function must avoid.
    # An empty verdict map means "nothing was measured", which flows to ok_rate=None
    # below and is reported as NO-EVIDENCE rather than as a 0% pass rate.
    total = sum(verdicts.values())
    ok = verdicts.get("OK", 0)
    # EMPTY = "STT heard nothing" (silence / clipped capture, see replay_samples.py
    # ``_classify``). That is a property of the RECORDING, not of Zoe's ability, which
    # is why it is already excluded from ``fail``. Leaving it in the ok_rate DENOMINATOR
    # made one extra silent clip read as a said-vs-did regression and hard-fail the gate:
    # observed 2026-07-26, 18 OK + 2 EMPTY scored 0.900 against a 19 OK + 1 EMPTY
    # baseline of 0.950 with fail=0 on BOTH runs — no capability lost, deploys blocked.
    # Score over SCOREABLE samples only. EMPTY stays in the artifact (and the printed
    # line) so a rising count is still visible, but it never gates a deploy on its own —
    # a silent recording must not be able to veto a voice-path release.
    #
    # When scoreable hits ZERO (every sample EMPTY, or no verdicts at all) there is no
    # evidence in either direction, so ``ok_rate`` is None rather than a fabricated 0.0.
    # Dividing by a clamped ``max(1, ...)`` denominator instead would manufacture an OK
    # rate of 0.000, which ``compare()`` reads as a total said-vs-did collapse — turning
    # "the harness recorded nothing" into "Zoe lost every ability she had". A gate with
    # no evidence must not pass, but it must not lie about WHY it did not pass either;
    # ``compare()`` emits a distinct NO-EVIDENCE warning for this.
    empty = verdicts.get("EMPTY", 0)
    scoreable = total - empty
    fail = verdicts.get("CANT_DO", 0) + verdicts.get("ERROR", 0)
    medians = {k: (agg.get(k) or {}).get("median") for k in ("stt_ms", "brain_ms", "e2e_ms")}
    return {
        "n_samples": report.get("n_samples", 0),
        "ok_rate": round(ok / scoreable, 3) if scoreable > 0 else None,
        "ok": ok, "fail": fail, "total": total,
        "empty": empty, "scoreable": max(0, scoreable),
        "verdicts": verdicts,
        "medians_ms": medians,
    }


def compare(cur: dict[str, Any], baseline: dict[str, Any], warn_ratio: float, warn_ms: float) -> list[str]:
    warnings: list[str] = []
    base = baseline.get("summary") if isinstance(baseline, dict) else None
    if not isinstance(base, dict):
        return warnings
    # No scoreable samples at all (every sample EMPTY / nothing recorded): the gate has
    # no evidence, which is NOT a function regression and must not be reported as one.
    # It still produces a warning, so status != pass — a gate that cannot see anything
    # must never read as green (artifact contract: "a skip is NOT a pass").
    if cur.get("ok_rate") is None:
        warnings.append(
            f"NO-EVIDENCE: 0 scoreable samples ({cur.get('empty', 0)} EMPTY of "
            f"{cur.get('total', 0)}) — the gate could not verify function either way"
        )
        return warnings
    # Function regression — Zoe must not lose the ability to handle the corpus.
    base_ok = base.get("ok_rate")
    if isinstance(base_ok, (int, float)) and cur["ok_rate"] < base_ok - 0.001:
        warnings.append(f"FUNCTION: OK rate {cur['ok_rate']:.3f} vs baseline {base_ok:.3f} "
                        f"(fail {cur['fail']} vs {base.get('fail')})")
    # ...and the CANT_DO/ERROR COUNT must not rise. The module contract promises both
    # checks; only the rate one was implemented, and a rate alone can hide a new
    # CANT_DO when the scoreable denominator grows in the same run (19/20 = 0.950
    # clears a 0.950 bar while carrying a regression the corpus did not have before).
    # "Can't do it" is a bug (memory: project_voice_recording_test_loop) — count it.
    base_fail = base.get("fail")
    if isinstance(base_fail, int) and isinstance(cur.get("fail"), int) and cur["fail"] > base_fail:
        warnings.append(f"FUNCTION: CANT_DO/ERROR count rose to {cur['fail']} "
                        f"from baseline {base_fail}")
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


# Skip/error paths never reach summarize(); this keeps the artifact SHAPE identical so
# a reader never has to branch on which path wrote it. ok_rate stays 0.0 rather than
# None purely for back-compat with existing consumers of the skip/error artifact —
# those paths already carry status != "pass", so the value is never read as a verdict.
EMPTY_SUMMARY = {"n_samples": 0, "ok_rate": 0.0, "ok": 0, "fail": 0,
                 "total": 0, "empty": 0, "scoreable": 0,
                 "verdicts": {}, "medians_ms": {}}


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
    # Consecutive non-pass streak — read the PREVIOUS artifact before this run
    # overwrites it. A genuine pass resets the streak; anything else increments
    # it. The threshold turns a silent skip-forever loop into a visible alarm.
    prev_streak = _previous_non_pass_streak(args.results)
    streak = 0 if status == "pass" else prev_streak + 1
    alert_after = max(1, int(getattr(args, "alert_after_non_pass", 3) or 3))
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
        "non_pass_streak": streak,              # consecutive runs with status != "pass"
        "non_pass_alert_after": alert_after,
        "non_pass_alert": streak >= alert_after,
    }
    write_json(args.results, payload)
    try:
        args.trend.parent.mkdir(parents=True, exist_ok=True)
        with open(args.trend, "a") as fh:
            fh.write(json.dumps(payload, separators=(",", ":")) + "\n")
    except OSError:
        pass
    return payload


def _previous_non_pass_streak(results_path: Path) -> int:
    """Read the prior run's non_pass_streak from the last-result artifact.

    Missing/corrupt artifact or a pre-streak artifact (no field) => 0: the
    streak then starts counting from THIS run — never a crash, never an
    invented alarm."""
    try:
        prev = json.loads(Path(results_path).read_text(encoding="utf-8"))
        streak = prev.get("non_pass_streak")
        return max(0, int(streak)) if isinstance(streak, (int, float)) else 0
    except Exception:
        return 0


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
       the live service (already resolved by `_resolve_service_dir`, so a probe
       run from a git WORKTREE lands on the live services/zoe-data);
    3. each `_service_dir_candidates()` entry's `.env` — the same ladder the
       service-dir resolution walks, so the two can't drift apart.

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
    for candidate in _service_dir_candidates():
        dsn = _dsn_from_env_file(candidate / ".env")
        if dsn:
            return dsn
    return ""


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


def resolve_min_mem(stt: str) -> int:
    """Memory floor for a run, by STT mode. ZOE_VOICE_PROBE_MIN_MEM_MB always wins."""
    env_min = os.environ.get("ZOE_VOICE_PROBE_MIN_MEM_MB")
    if env_min:
        try:
            return int(env_min)
        except ValueError:
            # Operator-facing config: name the bad value instead of a bare traceback.
            raise SystemExit(
                f"ZOE_VOICE_PROBE_MIN_MEM_MB={env_min!r} is not an integer (MB)")
    return 700 if stt == "remote" else 1500


def main() -> int:
    ap = argparse.ArgumentParser(description="Zoe voice regression + speed probe.")
    ap.add_argument("--samples", type=int, default=int(os.environ.get("ZOE_VOICE_PROBE_SAMPLES", "20")),
                    help="newest N corpus samples to replay")
    ap.add_argument("--user", default=os.environ.get("ZOE_VOICE_PROBE_USER", "jason"))
    ap.add_argument("--service-dir", default=None, help=SERVICE_DIR_HELP)
    ap.add_argument("--stt", choices=["inprocess", "remote"],
                    default=os.environ.get("ZOE_VOICE_REPLAY_STT", "inprocess"),
                    help="'remote' = STT via the LIVE service (no second Moonshine "
                         "load, needs ZOE_DEVICE_TOKEN). Default via ZOE_VOICE_REPLAY_STT.")
    ap.add_argument("--timeout", type=int, default=int(os.environ.get("ZOE_VOICE_PROBE_TIMEOUT_S", "900")))
    ap.add_argument("--baseline", type=Path, default=Path(os.environ.get("ZOE_VOICE_BASELINE", DEFAULT_BASELINE)))
    ap.add_argument("--results", type=Path, default=Path(os.environ.get("ZOE_VOICE_RESULTS", DEFAULT_RESULTS)))
    ap.add_argument("--trend", type=Path, default=Path(os.environ.get("ZOE_VOICE_TREND", DEFAULT_TREND)))
    ap.add_argument("--update-baseline", action="store_true", help="Save this run as the new comparison baseline.")
    ap.add_argument("--warn-ratio", type=float, default=float(os.environ.get("ZOE_VOICE_WARN_RATIO", "1.5")))
    ap.add_argument("--warn-ms", type=float, default=float(os.environ.get("ZOE_VOICE_WARN_MS", "1500")))
    # Per-mode default, both MEASURED not guessed. inprocess: 1500MB (set
    # empirically when the harness carried its own Moonshine; that load is the
    # bulk of it). remote: 700MB against a measured 445MB peak RSS for a REAL
    # 2-sample remote run (STT via the live endpoint, +brain, dry) inside a
    # MemoryMax=500M cgroup on the live box, 2026-07-27 — the embedder is 293MB
    # of it; the ~255MB margin covers WAV buffers, more samples, and drift.
    # An explicit flag or env always wins.
    ap.add_argument("--min-mem-mb", type=int, default=None,
                    help="skip if available memory is below this — never OOM the live box "
                         "(exit 0 until the non-pass streak trips the alert, then exit 4)")
    ap.add_argument("--alert-after-non-pass", type=int,
                    default=int(os.environ.get("ZOE_VOICE_ALERT_NON_PASS_RUNS", "3")),
                    help="consecutive non-pass runs before the skip path exits non-zero "
                         "(4) so the systemd timer goes visibly red instead of green-skipping forever")
    ap.add_argument("--no-cleanup", action="store_true",
                    help="skip the post-run replay-artifact cleanup (soft-delete of rows created during the replay window)")
    args = ap.parse_args()
    # Resolve BEFORE anything reads it: _resolve_dsn and run_measure both consume
    # args.service_dir, and both must see the same live dir.
    args.service_dir = str(_resolve_service_dir(args.service_dir))

    _lock_fd = _acquire_harness_lock()  # noqa: F841 — held for process lifetime

    # Baseline is loaded up front so EVERY exit path — including skip/error —
    # can record which baseline it was (or would have been) judged against.
    baseline: dict[str, Any] = {}
    try:
        baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    except Exception:
        pass

    if args.min_mem_mb is None:
        args.min_mem_mb = resolve_min_mem(args.stt)

    avail = mem_available_mb()
    if avail < args.min_mem_mb:
        reason = (f"available memory {avail}MB < {args.min_mem_mb}MB threshold — "
                  "deferring to avoid OOM on the live box")
        print(f"SKIP: {reason}.")
        payload = emit_result(args, status="skip", summary=dict(EMPTY_SUMMARY),
                              said_vs_did=[], speed_deltas={}, baseline=baseline, reason=reason)
        print(f"Results: {args.results}  (status=skip — a skip is NOT a pass)")
        if payload.get("non_pass_alert"):
            # Skip-streak alarm: N consecutive runs without a real pass. Exit
            # non-zero so the oneshot unit (and its timer) goes visibly RED —
            # a gate that green-skips forever is not a gate.
            print(f"ALERT: {payload['non_pass_streak']} consecutive non-pass runs "
                  f"(threshold {payload['non_pass_alert_after']}) — the replay gate has "
                  "not produced a real PASS; failing loudly so the unit goes red. "
                  "Free memory (or fix the underlying error) and re-run.", file=sys.stderr)
            return 4
        return 0

    run_started_utc = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    try:
        report = run_measure(args.samples, args.service_dir, args.user, args.timeout, args.stt)
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
    _rate = "n/a" if summary["ok_rate"] is None else f"{summary['ok_rate']:.0%}"
    print(f"Zoe voice regression probe — {summary['n_samples']} samples, "
          f"OK {summary['ok']}/{summary['scoreable']} ({_rate}), "
          f"fail={summary['fail']}, empty={summary['empty']}/{summary['total']}")
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
