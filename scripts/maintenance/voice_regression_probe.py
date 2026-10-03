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
     non_pass_streak, non_pass_alert_after, non_pass_alert,
     vad_stage: true, vad: {status, clips, speech_detected, pass_frac,
                            min_pass_frac, threshold, min_max_prob,
                            median_max_prob, model: {path, md5}, reason}}

A skip/timeout/error MUST leave an artifact with status != "pass" — never an
ABSENT file that a downstream checker could misread as "nothing wrong". The
deploy-path checker scripts/maintenance/voice_gate_check.py reads exactly this
contract to decide whether a voice-path deploy is allowed to proceed.

VAD STAGE (--vad-check, default ON): the replay starts at STT, so it never ran
the service's Silero VAD — and on 2026-09-26 a model-file swap (Silero v6.2.1
export over the v6.0 file) loaded without error but scored ~0.001 on real
speech, silently disabling barge-in / idle listening for a day with every gate
green. (Root cause, found 2026-09-27: voice_vad.py fed bare 512-sample hops
without upstream's 64-sample context; v6.2.1 needs it, v6.0 degrades without
it. The stage measures THE LOADER + model together, which is why it caught it.) This stage runs the service's REAL ``voice_vad.SileroVAD`` (imported from
--service-dir, so it is the code under test, against the model file the service
would load) over the newest N usable corpus clips, and the run FAILS when fewer
than 60% of them reach the speech threshold (the bar of
services/zoe-data/tests/test_voice_barge_in.py::
test_silero_real_model_detects_speech_across_corpus). It records aggregates only
— counts, fractions, peak probabilities, the model path + md5 — never a clip
name or anything else from the household corpus. It SKIPS (recorded, with a
reason) when the model file is absent or the corpus is too thin; a skip is "no
opinion", not a pass. It is cheap (~100 MB, a few seconds), so it also runs on
the memory-skip path: a known VAD failure turns that run's status into "fail"
instead of hiding behind "skip".

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
import glob
import hashlib
import importlib.util
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
from service_python import (  # noqa: E402
    ENV_VAR as PYTHON_ENV,
    resolve_service_python,
    same_python,
)

REPO = Path(__file__).resolve().parents[2]
MEASURE = REPO / "scripts" / "perf" / "measure_voice.py"
LOCK = "/tmp/zoe-voice-harness.lock"  # shared with all voice harness runs — no concurrent Kokoro OOM
DEFAULT_BASELINE = Path.home() / ".cache" / "zoe" / "voice_regression_baseline.json"
DEFAULT_RESULTS = Path.home() / ".cache" / "zoe" / "voice_regression_last.json"
DEFAULT_TREND = Path.home() / ".cache" / "zoe" / "voice_regression_trend.jsonl"
RATIO_FLOOR_MS = 100.0  # below this absolute delta, a high ratio is treated as noise
# Same default + env override as the replay harness (replay_samples.py main()).
DEFAULT_SAMPLE_DIR = os.environ.get("ZOE_VOICE_SAMPLE_DIR") or "/home/zoe/.zoe-voice-samples"

# ── VAD stage ────────────────────────────────────────────────────────────────
# The bar is the real-model test's (test_voice_barge_in.py _SPEECH_MIN_PASS_FRAC).
# Re-measured 2026-09-27 after voice_vad.py gained upstream's 64-sample context
# (the earlier 0.894 / 0.795 numbers were the context-less loader), live v6.0:
# 0.959 corpus-wide, 0.889 worst stride phase; THIS stage's newest-24 slice
# 19/24 (0.79 — the worst of 217 sliding newest-24 windows; median 0.917): the
# newest panel captures are quiet and 5 now peak 0.08-0.47. The 60% floor still
# sits under all of that, while a dead VAD collapses to 0/24 (the v6.2.1 file
# through the context-less loader, highest peak 0.026). voice_gate_check.py
# holds the same floor (VAD_MIN_PASS_FRAC there) and re-derives it from counts.
VAD_MIN_PASS_FRAC = 0.60
VAD_DEFAULT_CLIPS = 24
# Below this many usable clips the fraction is not a signal (test: 20 of 48;
# here the newest-N slice is smaller, so the floor scales with it).
VAD_MIN_USABLE = 8
# Newest-first scan budget: off-format members (24 kHz resamples, non-RIFF) are
# skipped, so look past N — but boundedly, never the whole 1000+ file corpus.
VAD_SCAN_FACTOR = 4
VAD_FRAME_BYTES = 640            # 20 ms of int16 @16 kHz — the live frame size
VAD_DEFAULT_MODEL_PATH = "/home/zoe/models/silero_vad.onnx"  # mirrors voice_vad
VAD_STAGE_MIN_MEM_MB = 400       # the stage peaks ~100 MB; never OOM the box for it


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
    # create_connection() (not a bare AF_INET socket) so IPv6 literals and
    # v6-only hostnames resolve properly, matching wait_for_port.py.
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _service_env_get(service_dir: str, *names: str) -> tuple[str | None, str | None]:
    """(name, value) for the first of *names* found — process env first, then
    service_dir/.env, mirroring the harness's _load_env (setdefault semantics).
    The diagnosis must read the SAME sources the harness reads, or it reports a
    'missing' token the replay actually had."""
    file_vals: dict[str, str] = {}
    try:
        with open(os.path.join(service_dir, ".env")) as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    file_vals[k] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    for n in names:
        # setdefault semantics EXACTLY: a name PRESENT in the process env — even
        # as an empty string — masks the .env value (the harness would see the
        # empty too and fall through its `or` chain). Skipping empties here made
        # the diagnosis claim a .env token the replay never received.
        if n in os.environ:
            v = os.environ[n].strip()
            if v:
                return n, v
            continue  # set-but-empty: masks .env for this name, harness sees nothing
        if file_vals.get(n):
            return f"{n} (from .env)", file_vals[n]
    return None, None


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
        # Name the variable actually observed — claiming ZOE_DEVICE_TOKEN when
        # only the DEVICE_TOKEN fallback is set would be its own small lie.
        tok_name, _ = _service_env_get(service_dir, "ZOE_DEVICE_TOKEN", "DEVICE_TOKEN")
        obs.append(f"{tok_name} present" if tok_name
                   else "ZOE_DEVICE_TOKEN/DEVICE_TOKEN MISSING")
        # Probe the endpoint the harness ACTUALLY targets (ZOE_BASE_URL), not a
        # hardcoded 127.0.0.1:8000 — a hardcoded probe against a redirected base
        # is exactly the reports-a-guess failure this file exists to remove.
        from urllib.parse import urlparse
        _, base = _service_env_get(service_dir, "ZOE_BASE_URL")
        base = base or "http://127.0.0.1:8000"
        u = urlparse(base)
        if u.scheme not in ("http", "https") or not u.hostname:
            # A malformed base is ITSELF the observation. Probing a fallback like
            # 127.0.0.1:80 would report the state of an endpoint the harness
            # cannot target — a guess with a confident tone.
            obs.append(f"ZOE_BASE_URL INVALID ({base!r}) — cannot probe the endpoint")
        else:
            port = u.port or (443 if u.scheme == "https" else 80)
            obs.append(f"zoe-data {u.hostname}:{port} "
                       f"{'reachable' if _port_open(u.hostname, port) else 'REFUSED'}")
    return obs


def run_measure(samples: int, service_dir: str, user: str, timeout: int, stt: str,
                python: str | None = None) -> dict[str, Any]:
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
        # The resolved interpreter (never a PATH "python3", which is 3.10) runs
        # measure_voice AND, via --python, the replay: that hop decides which
        # chromadb/STT stack the whole run measures (scripts/lib/service_python.py).
        python = python or sys.executable
        cmd = [
            python, str(MEASURE),
            "--last", str(samples), "--user", user,
            "--service-dir", service_dir, "--json", out_json, "--timeout", str(timeout),
            "--stt", stt, "--python", python,
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
        # Did the brain turns run WITH the memory the live service injects?
        # Anything but "ok" (incl. a replay too old to say) fails closed in main().
        "memory_recall": report.get("memory_recall") or "unreported",
        "memory_recall_detail": report.get("memory_recall_detail"),
        "memory_load_failures": report.get("memory_load_failures"),
        "interpreter": report.get("interpreter"),
    }


def recall_gate(summary: dict[str, Any]) -> str | None:
    """The reason this run is NOT evidence, or None. A gate that scored brain
    turns without recall must never report pass (B0.8: a 0.6.3 client on the 1.x
    palace had recall silently off inside every replay while live had it on)."""
    state = summary.get("memory_recall")
    if state == "ok":
        return None
    return (f"memory recall {state} inside the replay "
            f"({summary.get('memory_recall_detail') or 'no detail'}) — brain turns were "
            "scored WITHOUT recall; run the probe on the zoe-data service interpreter")


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


# ── VAD stage ────────────────────────────────────────────────────────────────
def _vad_block(status: str, reason: str = "", **fields: Any) -> dict[str, Any]:
    """One artifact shape for every VAD outcome, so a reader never branches on
    which path wrote it. Aggregates only — no clip names, no household data."""
    block: dict[str, Any] = {
        "status": status,            # pass | fail | skip | error
        "reason": reason,
        "clips": 0,                  # usable clips actually scored
        "speech_detected": 0,        # clips whose peak prob reached the threshold
        "pass_frac": None,
        "min_pass_frac": VAD_MIN_PASS_FRAC,
        "threshold": None,
        # [lowest, highest] per-clip PEAK speech probability. The highest is the
        # tell for a dead model: when even the best clip peaks at ~0.001 the model
        # is not detecting speech at all (the 2026-09-26 signature).
        "min_max_prob": None,
        "median_max_prob": None,
        "model": None,               # {path, md5} of the file the service would load
    }
    block.update(fields)
    return block


def _md5(path: str) -> str | None:
    try:
        h = hashlib.md5()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _load_service_vad(service_dir: str):
    """Import THE SERVICE'S voice_vad.py from --service-dir under a private
    module name — the code under test, not a copy, and not whatever
    `voice_vad` happens to be importable in this interpreter."""
    path = Path(service_dir) / "voice_vad.py"
    if not path.is_file():
        raise FileNotFoundError(f"voice_vad.py not found in service dir {service_dir}")
    spec = importlib.util.spec_from_file_location("_zoe_probe_voice_vad", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _vad_model_path(vad_mod: Any) -> str:
    """The model path the SERVICE would load (its own resolver when present)."""
    resolver = getattr(vad_mod, "_model_path", None)
    if callable(resolver):
        try:
            return str(resolver())
        except Exception:
            pass
    return os.environ.get("ZOE_SILERO_VAD_MODEL", "").strip() or VAD_DEFAULT_MODEL_PATH


def _newest_usable_clips(sample_dir: str, n: int) -> list[bytes]:
    """PCM of the newest `n` USABLE corpus clips (16 kHz mono int16), newest first.

    Newest by capture time (mtime, name tiebreak) and TOP-LEVEL only — the same
    selection semantics as replay_samples._select, so quarantine-*/ captures
    never re-enter. Off-format members are skipped, not failed: they are a
    known corpus fact (24 kHz resamples, non-RIFF), not a VAD bug."""
    import wave

    paths = glob.glob(os.path.join(sample_dir, "*.wav"))
    rows = []
    for p in paths:
        try:
            rows.append((os.stat(p).st_mtime, os.path.basename(p), p))
        except OSError:
            continue
    rows.sort(reverse=True)
    out: list[bytes] = []
    for _, _, p in rows[: max(n, n * VAD_SCAN_FACTOR)]:
        if len(out) >= n:
            break
        try:
            with wave.open(p, "rb") as w:
                if (w.getframerate(), w.getnchannels(), w.getsampwidth()) != (16000, 1, 2):
                    continue
                out.append(w.readframes(w.getnframes()))
        except (wave.Error, OSError, EOFError):
            # NARROW on purpose: an unreadable member is corpus hygiene; a broad
            # except would also swallow a real failure and shrink the sample.
            continue
    return out


def run_vad_check(service_dir: str, sample_dir: str, clips: int, *,
                  vad_mod: Any = None,
                  min_usable: int = VAD_MIN_USABLE) -> dict[str, Any]:
    """Run the service's REAL Silero VAD over the newest `clips` corpus clips.

    Never raises: every outcome is a block with a status. `vad_mod` is injectable
    for tests (a fake with create_vad/speech_threshold/_model_path); in a real
    run it is the service's own voice_vad.py.

      pass  — >= VAD_MIN_PASS_FRAC of usable clips peak at/above the threshold
      fail  — below that floor, OR the model file exists but create_vad() returned
              None (the service would silently fall back to RMS energy VAD)
      skip  — model file absent, or fewer than `min_usable` usable clips
              (recorded with a reason; "no opinion", never a pass)
      error — the stage itself could not run (voice_vad import failed)
    """
    if vad_mod is None:
        try:
            vad_mod = _load_service_vad(service_dir)
        except Exception as exc:
            return _vad_block("error", f"could not import the service's voice_vad: {exc}")
    model_path = _vad_model_path(vad_mod)
    if not os.path.isfile(model_path):
        return _vad_block("skip", f"Silero model not present at {model_path} — VAD stage "
                          "not run (the service would use the RMS energy fallback)",
                          model={"path": model_path, "md5": None})
    model = {"path": model_path, "md5": _md5(model_path)}
    try:
        threshold = float(vad_mod.speech_threshold())
    except Exception:
        threshold = 0.5
    pcm = _newest_usable_clips(sample_dir, clips)
    if len(pcm) < min_usable:
        return _vad_block("skip", f"only {len(pcm)} usable 16k mono clips in the newest "
                          f"slice of {sample_dir} (< {min_usable}) — corpus too thin "
                          "to judge the model", clips=len(pcm), threshold=threshold,
                          model=model)
    peaks: list[float] = []
    for raw in pcm:
        vad = vad_mod.create_vad()      # fresh recurrent state per clip, as live
        if vad is None:
            return _vad_block("fail", f"model present at {model_path} but create_vad() "
                              "returned None — the service would silently fall back to "
                              "RMS energy VAD", threshold=threshold, model=model)
        peak = 0.0
        for i in range(0, len(raw), VAD_FRAME_BYTES):   # 20 ms frames, streaming state
            peak = max(peak, float(vad.process(raw[i:i + VAD_FRAME_BYTES])))
        peaks.append(peak)
    # `>=` matches the live frame loop's comparison (max(probs) >= threshold).
    detected = sum(1 for p in peaks if p >= threshold)
    frac = detected / len(peaks)
    ordered = sorted(peaks)
    fields = dict(clips=len(peaks), speech_detected=detected, pass_frac=round(frac, 3),
                  threshold=threshold,
                  min_max_prob=[round(ordered[0], 4), round(ordered[-1], 4)],
                  median_max_prob=round(ordered[len(ordered) // 2], 4), model=model)
    if frac >= VAD_MIN_PASS_FRAC:
        return _vad_block("pass", "", **fields)
    return _vad_block(
        "fail",
        f"speech detected in {detected}/{len(peaks)} newest clips ({frac:.0%}) < "
        f"{VAD_MIN_PASS_FRAC:.0%} floor — the Silero model at {model_path} "
        f"(md5 {model['md5']}) is not detecting real speech (highest clip peak "
        f"{ordered[-1]:.3f}); barge-in / idle listening would be silently off",
        **fields)


def vad_not_run(reason: str) -> dict[str, Any]:
    return _vad_block("skip", reason)


def fold_vad_status(status: str, vad: dict[str, Any] | None) -> str:
    """Fold the VAD verdict into the run status. Only ever TIGHTENS:
    a VAD fail makes the run "fail" (even a memory-"skip" run — a known failure
    beats "we did not look"); a VAD error makes a passing run "error" (unknown is
    not a pass). A VAD skip changes nothing."""
    vs = (vad or {}).get("status")
    if vs == "fail" and status in ("pass", "skip"):
        return "fail"
    if vs == "error" and status == "pass":
        return "error"
    return status


# Skip/error paths never reach summarize(); this keeps the artifact SHAPE identical so
# a reader never has to branch on which path wrote it. ok_rate stays 0.0 rather than
# None purely for back-compat with existing consumers of the skip/error artifact —
# those paths already carry status != "pass", so the value is never read as a verdict.
EMPTY_SUMMARY = {"n_samples": 0, "ok_rate": 0.0, "ok": 0, "fail": 0,
                 "total": 0, "empty": 0, "scoreable": 0,
                 "verdicts": {}, "medians_ms": {}}


def service_revision(service_dir: Any) -> dict[str, Any] | None:
    """Git identity of the tree this probe actually EXERCISED.

    The artifact is evidence, and evidence must name what it is evidence FOR.
    Without this, a fresh passing run against `main` is indistinguishable from one
    against the PR under review, so the PR gate would accept the wrong code's
    result and clear every voice PR for the whole freshness window. Records the
    commit, the tree sha, and whether the worktree was DIRTY — a dirty tree is
    reported honestly and `voice_gate_check.py --expect-revision` refuses it,
    because an uncommitted tree cannot be attributed to any commit.

    Returns None when the revision cannot be determined (no service dir, not a git
    checkout, git unavailable). The gate treats a missing revision as unattributed
    and blocks whenever it was asked to bind one — never as a pass."""
    if not service_dir:
        return None
    repo = Path(service_dir)
    if not repo.exists():
        return None

    def _git(*a: str) -> str | None:
        try:
            proc = subprocess.run(["git", "-C", str(repo), *a],
                                  capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            return None
        return proc.stdout.strip() if proc.returncode == 0 else None

    commit = _git("rev-parse", "HEAD")
    if not commit:
        return None
    # `status --porcelain` covers the whole checkout, not just services/zoe-data:
    # an uncommitted edit anywhere in the tree breaks attribution to the commit.
    #
    # FAIL CLOSED ON AN UNREADABLE STATUS. `_git` returns None both for "git said
    # nothing" and for "git failed" (unreadable index, a bad inherited
    # GIT_INDEX_FILE, a permissions problem), and `bool(None)` is False — so a
    # failed cleanliness check used to record the tree as CLEAN. That is a real
    # fail-open in the revision binding: cleanliness was never established, yet a
    # matching commit would clear `--expect-revision`. Unknown is not clean.
    status = _git("status", "--porcelain")
    clean_verified = status is not None
    return {
        "commit": commit,
        "tree": _git("rev-parse", "HEAD^{tree}"),
        "dirty": True if not clean_verified else bool(status),
        # Distinguishes "we looked and it was dirty" from "we could not look".
        # Both block, but only one of them is a repo state the operator can fix
        # by committing, so the gate's message should not have to guess.
        "clean_verified": clean_verified,
        "service_dir": str(repo),
    }


def emit_result(args, *, status: str, summary: dict[str, Any],
                said_vs_did: list[str], speed_deltas: dict[str, Any],
                baseline: dict[str, Any], reason: str = "",
                vad: dict[str, Any] | None = None) -> dict[str, Any]:
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
        # WHAT CODE THIS IS EVIDENCE FOR. Read by voice_gate_check.py
        # --expect-revision (the PR gate). getattr: emit_result is also called
        # with lightweight arg objects that carry no service_dir.
        "revision": service_revision(getattr(args, "service_dir", None)),
        "summary": summary,                     # back-compat: n_samples / ok_rate / medians_ms
        "non_pass_streak": streak,              # consecutive runs with status != "pass"
        "non_pass_alert_after": alert_after,
        "non_pass_alert": streak >= alert_after,
        # THE VAD STAGE. `vad_stage: true` is this artifact's CLAIM that it was
        # written by a probe that runs the stage; voice_gate_check.py then
        # requires a well-formed `vad` block and blocks on a failed one. Every
        # exit path writes the block (a stage that did not run says so, as a
        # skip with a reason) — an older artifact without the claim is read as
        # "no opinion on VAD", so pre-stage artifacts keep working.
        "vad_stage": True,
        "vad": vad if isinstance(vad, dict) else vad_not_run("VAD stage not run on this exit path"),
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

    THIS IS A SAFETY NET, NOT THE PRIMARY GUARD, and it never covered the whole
    surface. It sweeps two classes; the brain sidecar's tools also write
    reminders, notes, journal_entries, people, users, lists, MemPalace memories,
    Home Assistant device state and Music Assistant playback — none of which are
    reversible by an UPDATE here, and a NEW mutating tool would leak by default.
    Brain-lane writes are now prevented at the seam instead: replay_samples.py
    sends a per-request replay marker that makes the sidecar report writes as
    done without committing them (zoe_flue_client._wrap_message_with_replay).
    Keep this sweep for the fast_tiers half and for --execute runs; do NOT let it
    grow class-by-class, because that race is unwinnable.
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


def _vad_stage(args) -> dict[str, Any]:
    if not getattr(args, "vad_check", True):
        return vad_not_run("disabled (--no-vad-check / ZOE_VOICE_PROBE_VAD_CHECK=0)")
    try:
        return run_vad_check(args.service_dir, args.sample_dir, args.vad_clips)
    except Exception as exc:  # the stage must never take the artifact down with it
        return _vad_block("error", f"VAD stage crashed: {type(exc).__name__}: {exc}")


def _print_vad(vad: dict[str, Any]) -> None:
    if vad.get("status") in ("pass", "fail") and vad.get("clips"):
        print(f"  VAD: {vad['speech_detected']}/{vad['clips']} newest clips >= "
              f"{vad['threshold']} (floor {vad['min_pass_frac']:.0%}), per-clip peak "
              f"range {vad['min_max_prob']}, model md5 {(vad.get('model') or {}).get('md5')} "
              f"-> {vad['status'].upper()}")
    else:
        print(f"  VAD: {str(vad.get('status')).upper()} — {vad.get('reason')}")


_REEXEC_ENV = "_ZOE_PROBE_REEXEC"


def _reexec_if_needed(python: str, source: str) -> None:
    """Re-exec this probe on `python` so EVERY stage (VAD, cleanup, replay) runs
    the service's stack, whoever launched it (timer, landing script, bare python3).
    Same PID, so a parent `flock` wrapper still owns the harness lock. Guarded
    against loops: a re-exec'd child never re-execs again."""
    if same_python(python, sys.executable) or os.environ.get(_REEXEC_ENV) == python:
        return
    print(f"probe: re-executing on {python} ({source}); was {sys.executable}", flush=True)
    sys.stderr.flush()
    os.execve(python, [python, os.path.abspath(__file__), *sys.argv[1:]],
              {**os.environ, _REEXEC_ENV: python})


def main(reexec: bool = False) -> int:
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
    ap.add_argument("--vad-check", action=argparse.BooleanOptionalAction,
                    default=os.environ.get("ZOE_VOICE_PROBE_VAD_CHECK", "1") not in ("0", "false", "no"),
                    help="run the service's real Silero VAD over the newest corpus clips and "
                         "FAIL the run below a 60%% speech-detection floor (default on; skips "
                         "with a recorded reason when the model file is absent)")
    ap.add_argument("--vad-clips", type=int,
                    default=int(os.environ.get("ZOE_VOICE_PROBE_VAD_CLIPS", str(VAD_DEFAULT_CLIPS))),
                    help="newest N usable corpus clips the VAD stage scores")
    ap.add_argument("--sample-dir", default=DEFAULT_SAMPLE_DIR,
                    help="voice corpus dir for the VAD stage (default: ZOE_VOICE_SAMPLE_DIR "
                         "or ~zoe/.zoe-voice-samples — the replay harness's own default)")
    ap.add_argument("--python", default=None,
                    help=f"interpreter for the whole run (default: ${PYTHON_ENV}, else the "
                         "zoe-data unit's ExecStart — scripts/lib/service_python.py). The "
                         "probe re-executes itself on it, so VAD + replay match the service.")
    ap.add_argument("--no-cleanup", action="store_true",
                    help="skip the post-run replay-artifact cleanup (soft-delete of rows created during the replay window)")
    args = ap.parse_args()
    # Resolve BEFORE anything reads it: _resolve_dsn and run_measure both consume
    # args.service_dir, and both must see the same live dir.
    args.service_dir = str(_resolve_service_dir(args.service_dir))
    try:
        args.python, python_source = resolve_service_python(args.python)
    except SystemExit as exc:   # a bad explicit choice still leaves an artifact
        print(f"ERROR: {exc}", file=sys.stderr)
        emit_result(args, status="error", summary=dict(EMPTY_SUMMARY), said_vs_did=[],
                    speed_deltas={}, baseline={}, reason=f"interpreter: {exc}")
        return 2
    if reexec:
        _reexec_if_needed(args.python, python_source)
    print(f"probe interpreter: {sys.executable}; replay interpreter: {args.python} "
          f"({python_source})")

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
        # The VAD stage is ~100 MB, not a replay: still run it when it fits, so a
        # box that is too tight for the replay for days (exactly how the
        # 2026-09-26 model swap went unseen) still reports a dead VAD model.
        if avail >= VAD_STAGE_MIN_MEM_MB:
            vad = _vad_stage(args)
        else:
            vad = vad_not_run(f"available memory {avail}MB < {VAD_STAGE_MIN_MEM_MB}MB — "
                              "VAD stage not run either")
        status = fold_vad_status("skip", vad)
        if status != "skip":
            reason = f"{reason}; VAD: {vad.get('reason')}"
        payload = emit_result(args, status=status, summary=dict(EMPTY_SUMMARY),
                              said_vs_did=[], speed_deltas={}, baseline=baseline, reason=reason,
                              vad=vad)
        if status == "fail":
            print(f"FAIL: VAD stage — {vad.get('reason')}", file=sys.stderr)
            print(f"Results: {args.results}  (status=fail)")
            return 1
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
        report = run_measure(args.samples, args.service_dir, args.user, args.timeout, args.stt,
                             python=args.python)
    except Exception as exc:
        reason = f"voice probe could not run: {exc}"
        print(f"ERROR: {reason}", file=sys.stderr)
        vad = _vad_stage(args)   # cheap, and still evidence when the replay could not run
        if vad.get("status") == "fail":
            reason = f"{reason}; VAD: {vad.get('reason')}"
        emit_result(args, status="error", summary=dict(EMPTY_SUMMARY),
                    said_vs_did=[], speed_deltas={}, baseline=baseline, reason=reason,
                    vad=vad)
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
    interp = summary.get("interpreter") or {}
    print(f"  memory recall: {summary['memory_recall']}  (replay on {interp.get('python')}, "
          f"Python {interp.get('version')}, chromadb {interp.get('chromadb')})")
    recall_error = recall_gate(summary)
    base_recall = ((baseline.get("summary") or {}) if isinstance(baseline, dict) else {}).get("memory_recall")
    if not recall_error and baseline and base_recall != "ok" and not args.update_baseline:
        print("NOTE baseline was recorded WITHOUT recall-on evidence — per-stage speed deltas "
              "are not comparable; re-baseline once in a quiet window (--update-baseline)")

    if recall_error:
        # NEVER write a recall-off run as the bar, and never call it a pass.
        print(f"ERROR: {recall_error}", file=sys.stderr)
    elif args.update_baseline or not args.baseline.exists():
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

    # AFTER the replay, so the stage's ~100 MB never overlaps the replay's peak.
    vad = _vad_stage(args)
    _print_vad(vad)

    # a failed sweep is a warning-level exit: results are valid but the
    # calendar/lists are dirty and the systemd unit shows non-zero.
    status = fold_vad_status("pass" if (not warnings and cleanup_ok) else "fail", vad)
    reason_parts = list(warnings)
    if recall_error:
        status = "error"
        reason_parts.insert(0, recall_error)
    if not cleanup_ok:
        reason_parts.append("replay-artifact cleanup FAILED (calendar/lists may be dirty)")
    if vad.get("status") in ("fail", "error"):
        reason_parts.append(f"VAD {vad['status'].upper()}: {vad.get('reason')}")
    emit_result(args, status=status, summary=summary, said_vs_did=said_vs_did,
                speed_deltas=speed_deltas, baseline=baseline, reason="; ".join(reason_parts),
                vad=vad)

    print(f"Results: {args.results}  Trend: {args.trend}  (status={status})")
    if status == "error":
        return 2
    return 0 if status == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main(reexec=True))
