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
regression.{service,timer}).

PINNED CLIP SET: the baseline records WHICH clips it measured (summary.clip_ids,
hashes of name+size, never content or names) and every later run replays exactly
that set, so new recordings can no longer shift the speed bar (on 2026-10-13 the
newest-N slice drifted STT to ~400 ms against a 921 ms baseline, which would have
let a 2x STT regression through). If the baseline's clips are not all present, or
the baseline predates pinning, SPEED IS REFUSED (status error/fail with a
BASELINE-SET reason), never guessed. --update-baseline re-pins to the newest N
(--samples). summary.stt_ms_per_audio_s is a length-normalised STT metric that is
also gated. The artifact gains an additive `speed_comparison` field.

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

PER-CLIP OUTCOME CHECKS (T-3, deep dive 2): "OK" used to mean "not empty, not an
error, not a refusal phrase", so a clip that confidently did the WRONG thing passed.
Every pinned clip is now held to an expectation (scripts/maintenance/
voice_clip_checks.py): the baseline's per-clip profile, or an owner label kept OFF
the repo (--expectations). A clip that was OK must still be heard (newly EMPTY is a
regression), still be OK, and take the same route: the lane and fast-path
tier:domain:intent, plus (baselines recorded after T-3) the router's domain + tool
and the Tier-0 intent. Any failing clip fails the run; the artifact carries
`clip_checks: true`, `clip_summary` and `clip_results` (ids, hashes and code labels
only), and voice_gate_check.py re-derives the verdict from them. A pinned set with no
expectation at all judges nothing and is a FUNCTION failure, never a pass.

SELF-TEST (--self-test): the negative control. Replays the pinned clips with each
injected fault (blank STT; a router that moves every clip to another domain) and
exits 0 only if the per-clip checks go red on every one. It writes
~/.cache/zoe/voice_probe_selftest.json and NEVER the shared artifact, trend or
baseline (the deploy gate reads the shared one); a normal run strips any inherited
ZOE_REPLAY_FAULT and refuses a report that carries one.

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
    # the negative control (exit 0 only when every injected fault went red):
    python3 scripts/maintenance/voice_regression_probe.py --self-test
    # adopt a stratified pinned set proposed by voice_corpus_strata.py:
    python3 scripts/maintenance/voice_regression_probe.py --update-baseline --pin-from ~/.zoe/voice-gate/proposed_pins.json
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
import wave
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


def _sibling(name: str):
    """Load a scripts/maintenance sibling BY FILE PATH (never via sys.path: that
    directory holds names that shadow service modules)."""
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, Path(__file__).resolve().parent / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


vcc = _sibling("voice_clip_checks")   # T-3: per-clip outcome assertions + the self-test verdicts

REPO = Path(__file__).resolve().parents[2]
MEASURE = REPO / "scripts" / "perf" / "measure_voice.py"
LOCK = "/tmp/zoe-voice-harness.lock"  # shared with all voice harness runs — no concurrent Kokoro OOM
DEFAULT_BASELINE = Path.home() / ".cache" / "zoe" / "voice_regression_baseline.json"
DEFAULT_RESULTS = Path.home() / ".cache" / "zoe" / "voice_regression_last.json"
DEFAULT_TREND = Path.home() / ".cache" / "zoe" / "voice_regression_trend.jsonl"
# --self-test writes HERE and never to the shared artifact / trend / baseline above:
# the deploy gate reads the shared artifact, and a self-test is red by design.
DEFAULT_SELFTEST_RESULTS = Path.home() / ".cache" / "zoe" / "voice_probe_selftest.json"
DEFAULT_SELFTEST_TREND = Path.home() / ".cache" / "zoe" / "voice_probe_selftest_trend.jsonl"
FAULT_ENV = "ZOE_REPLAY_FAULT"
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


# -- Pinned clip set (instrument hygiene, 2026-10-13) -------------------------
# The probe used to replay "the newest N clips by mtime". Every new recording
# silently CHANGED that set, so a speed baseline recorded on one set was judged
# against another: on 2026-10-13 STT measured ~400 ms against a 921 ms baseline
# (the newest clips are shorter), meaning a genuine 2x STT regression would have
# passed. The baseline now records WHICH clips it measured (ids = a hash of name
# + size, never content, never a name) and every later run replays exactly that
# set. If the set is unavailable, speed is REFUSED rather than guessed.
CLIP_ID_SCHEME = "sha256(name NUL size)[:12]"
# Per-audio-second STT is length-normalised, so it is far less sensitive to which
# clips were picked. Absolute floor (ms of STT per second of audio) below which a
# high ratio is noise: the per-second analogue of RATIO_FLOOR_MS.
CLIP_ID_LEN = 12
STT_PER_S_FLOOR = 50.0


def clip_id(name: str, size: int) -> str:
    """Stable, content-free id of a corpus clip: hash of basename + byte size."""
    return hashlib.sha256(f"{name}\x00{int(size)}".encode()).hexdigest()[:CLIP_ID_LEN]


def corpus_index(sample_dir: str) -> dict[str, str]:
    idx: dict[str, str] = {}
    for p in glob.glob(os.path.join(sample_dir, "*.wav")):
        try:
            idx[clip_id(os.path.basename(p), os.stat(p).st_size)] = p
        except OSError:
            continue
    return idx


def clip_ids_for_names(names: list[str], sample_dir: str) -> list[str]:
    """Ids for the replayed clip names (order preserved; unreadable ones dropped)."""
    out: list[str] = []
    for n in names:
        try:
            out.append(clip_id(n, os.stat(os.path.join(sample_dir, n)).st_size))
        except OSError:
            continue
    return out


def baseline_clip_ids(baseline: dict[str, Any] | None) -> list[str] | None:
    """The clip ids a baseline pinned, or None for a legacy/absent baseline."""
    summ = baseline.get("summary") if isinstance(baseline, dict) else None
    ids = summ.get("clip_ids") if isinstance(summ, dict) else None
    if isinstance(ids, list) and ids and all(isinstance(i, str) for i in ids):
        return list(ids)
    return None


def resolve_pinned(ids: list[str], sample_dir: str) -> tuple[list[str], list[str]]:
    """(basenames of the pinned clips present in the corpus, ids that are missing)."""
    idx = corpus_index(sample_dir)
    names = [os.path.basename(idx[i]) for i in ids if i in idx]
    return names, [i for i in ids if i not in idx]


def wav_duration_s(path: str) -> float | None:
    try:
        with wave.open(path, "rb") as w:
            rate = w.getframerate()
            return w.getnframes() / rate if rate else None
    except (OSError, EOFError, wave.Error):
        return None


def stt_per_audio_second(rows: list[dict[str, Any]], sample_dir: str) -> float | None:
    """STT ms per second of audio: total STT ms divided by total audio seconds over
    the replayed clips. Length-normalised, so it still means something when the
    clip set differs."""
    stt = dur = 0.0
    for r in rows or []:
        ms, name = r.get("stt_ms"), r.get("file")
        if not isinstance(ms, (int, float)) or ms <= 0 or not name:
            continue
        d = wav_duration_s(os.path.join(sample_dir, os.path.basename(name)))
        if d and d > 0:
            stt += ms
            dur += d
    return round(stt / dur, 1) if dur > 0 else None


def clip_set_summary(report: dict[str, Any], sample_dir: str) -> dict[str, Any]:
    """The pinned-set fields merged into the summary (and thus into a baseline)."""
    rows = report.get("rows") or []
    names = [os.path.basename(r["file"]) for r in rows if r.get("file")]
    return dict(
        clip_ids=clip_ids_for_names(names, sample_dir),
        clip_id_scheme=CLIP_ID_SCHEME,
        stt_ms_per_audio_s=stt_per_audio_second(rows, sample_dir),
        clip_profile=clip_profile(rows, sample_dir),
    )


def _row_clip_id(row: dict[str, Any], sample_dir: str) -> str | None:
    name = row.get("file")
    if not name:
        return None
    try:
        return clip_id(os.path.basename(name),
                       os.stat(os.path.join(sample_dir, os.path.basename(name))).st_size)
    except OSError:
        return None


def clip_profile(rows: list[dict[str, Any]], sample_dir: str) -> dict[str, dict[str, Any]]:
    """Per-clip {verdict, via_brain, route, heard[, sig, labels]}, keyed by clip id.
    Content-free (hashes and code labels, never a name, transcript or reply). A
    baseline that carries it is the per-clip EXPECTATION (voice_clip_checks): every
    clip OK here must stay OK on the same route; ``sig``/``labels`` (T-3, from a
    replay that records route labels) make "the same route" include the router's
    domain + tool and the Tier-0 intent, not only tier:domain:intent."""
    out: dict[str, dict[str, Any]] = {}
    for r in rows or []:
        cid = _row_clip_id(r, sample_dir)
        if cid and isinstance(r.get("verdict"), str):
            out[cid] = vcc.profile_entry(r)
    return out


# ── Flaky brain-wording tolerance ────────────────────────────────────────────
# Incident (2026-10-15): the pinned set holds a short garbled clip that routes to
# chat, so the BRAIN's free-text reply decides its verdict — and that reply varies
# run to run, sometimes matching replay_samples._CANT_DO_RE ("can't tell you…").
# Same code scored OK, OK, CANT_DO: a PR failed ~1/3 of the time with no regression.
#
# The tolerance is deliberately narrow. Only a clip that (a) was OK in the baseline,
# (b) is CANT_DO now AFTER falling through to the brain (via_brain — the verdict is
# a function of reply text), and (c) still takes the same route with the same
# transcript as the baseline is re-run, in the same harness on the same code. It
# counts as regressed if it fails in >= FLAKY_FAIL_MIN of its 3 runs. A deterministic
# regression (route/tier change, STT change, crash, ERROR, a real refusal) fails
# every run, so it still fails; anything that is not brain wording is never retried.
FLAKY_MAX_CLIPS = 3       # more candidates than this is a regression, not noise
FLAKY_EXTRA_RUNS = 2      # runs 2 and 3
FLAKY_FAIL_MIN = 2        # failures (of 3 runs) that make a clip regressed


def _profile_of(baseline: dict[str, Any] | None) -> dict[str, dict[str, Any]] | None:
    summ = baseline.get("summary") if isinstance(baseline, dict) else None
    prof = summ.get("clip_profile") if isinstance(summ, dict) else None
    return prof if isinstance(prof, dict) and prof else None


def flaky_candidates(report: dict[str, Any], baseline: dict[str, Any] | None,
                     sample_dir: str) -> dict[str, Any]:
    """Split the failing rows of a first run into retry-eligible and hard ones.

    eligible     [(clip_id, basename)]: CANT_DO via the brain with the same route and
                 transcript as a baseline OK (on a legacy baseline with no per-clip
                 profile: brain CANT_DO only; route change cannot be checked); and
                 (T-3) a clip OK in the baseline profile that is EMPTY now, or OK on
                 ANOTHER route. The per-clip checks count both as regressions, and
                 both were measured as transient on the pinned set (2026-10-15..18,
                 44 runs: one empty transcript; one clip OK on a different route in
                 2 isolated runs, e.g. a router sidecar timeout falling back to the
                 similarity route). A real route regression fails every run.
    hard         failures that are not brain wording, or whose route / transcript
                 differs from the baseline: deterministic, never retried
    pre_existing failing in the baseline too: not a regression, not retried
    profile      True when the baseline carried per-clip verdicts."""
    prof = _profile_of(baseline)
    eligible: list[tuple[str, str]] = []
    hard: list[str] = []
    pre_existing: list[str] = []
    for i, r in enumerate(report.get("rows") or []):
        if r.get("verdict") in ("EMPTY", "OK"):
            real_id = _row_clip_id(r, sample_dir)
            b = (prof or {}).get(real_id) if real_id else None
            if isinstance(b, dict) and b.get("verdict") == "OK" and (
                    r.get("verdict") == "EMPTY" or _route_differs(b, r)):
                eligible.append((real_id, os.path.basename(r["file"])))
            continue
        if r.get("verdict") not in ("CANT_DO", "ERROR"):
            continue
        real_id = _row_clip_id(r, sample_dir)
        cid = real_id or f"unidentified-{i}"
        brain_wording = r.get("verdict") == "CANT_DO" and r.get("via_brain") is True and real_id is not None
        if prof is not None:
            b = prof.get(cid)
            if isinstance(b, dict) and b.get("verdict") in ("CANT_DO", "ERROR"):
                pre_existing.append(cid)
                continue
            if not brain_wording or not isinstance(b, dict):
                hard.append(cid)
                continue
            if (b.get("verdict") != "OK" or b.get("via_brain") is not True
                    or b.get("route") != r.get("route_hash") or b.get("heard") != r.get("heard_hash")):
                hard.append(cid)       # route/tier/transcript changed: deterministic
                continue
        elif not brain_wording:
            hard.append(cid)
            continue
        eligible.append((cid, os.path.basename(r["file"])))
    return dict(eligible=eligible, hard=hard, pre_existing=pre_existing, profile=prof is not None)


def _recount(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for r in rows:
        v = r.get("verdict")
        if isinstance(v, str):
            counts[v] = counts.get(v, 0) + 1
    return counts


def _route_differs(expect: dict[str, Any], row: dict[str, Any]) -> bool:
    """Did ``row`` take another route than the profile entry ``expect``? The full
    signature when both carry one (voice_clip_checks), else the legacy route hash."""
    if expect.get("sig") and row.get("route_sig"):
        return expect["sig"] != row["route_sig"]
    return expect.get("route") is not None and expect.get("route") != row.get("route_hash")


def _retry_verdict(ref_row: dict[str, Any], row: dict[str, Any] | None) -> str:
    """One retry's verdict for a clip: OK only on positive evidence (an OK verdict on
    the reference lane + route: the first run's for a brain-wording CANT_DO, the
    baseline's for a transient EMPTY or route flip); otherwise the failure."""
    if not row:
        return "ERROR"
    v = row.get("verdict")
    if v != "OK":
        return v if isinstance(v, str) else "ERROR"
    if ref_row.get("sig") and row.get("route_sig"):
        same = ref_row["sig"] == row["route_sig"]
    else:
        same = row.get("route_hash") == ref_row.get("route_hash")
    if same and (row.get("via_brain") is True) == (ref_row.get("via_brain") is True):
        return "OK"
    return "ROUTE-CHANGED"


def apply_flaky_tolerance(report: dict[str, Any], baseline: dict[str, Any] | None,
                          rerun: Any, sample_dir: str, *,
                          max_clips: int = FLAKY_MAX_CLIPS,
                          extra_runs: int = FLAKY_EXTRA_RUNS) -> dict[str, Any]:
    """Re-run ONLY the retry-eligible failing clips (<= extra_runs more times) and
    return a report whose verdicts reflect the majority outcome.

    rerun(names) -> measure report for exactly those clips (same code/harness).
    Returns {retried, report, clips, note}. `report` is the input unchanged unless
    retried; a clip that fails in >= FLAKY_FAIL_MIN of its runs keeps CANT_DO,
    otherwise its row is set to OK with `flaky: true` (first-run numbers kept).
    A retry that cannot run, has no recall evidence, or returns a different route
    counts as a failure: tolerance is only ever granted on positive evidence."""
    out: dict[str, Any] = dict(retried=False, report=report, clips=[], note="")
    cand = flaky_candidates(report, baseline, sample_dir)
    if not cand["eligible"]:
        return out
    if cand["profile"] and cand["hard"]:
        out["note"] = (f"{len(cand['hard'])} failing clip(s) are not brain wording (route/transcript "
                       "change, ERROR or non-brain CANT_DO): deterministic, not retried")
        return out
    if not cand["profile"]:
        summ = (baseline or {}).get("summary") if isinstance(baseline, dict) else None
        base_fail = summ.get("fail") if isinstance(summ, dict) else None
        if isinstance(base_fail, int) and len(cand["hard"]) > base_fail:
            out["note"] = (f"{len(cand['hard'])} failing clip(s) are not brain wording and the "
                           f"baseline had {base_fail} failure(s): deterministic, not retried")
            return out
    if len(cand["eligible"]) > max_clips:
        out["note"] = (f"{len(cand['eligible'])} retry-eligible failures exceed the retry cap of "
                       f"{max_clips}: counted as a regression, not retried")
        return out

    rows = report.get("rows") or []
    first = {c: r for r in rows if (c := _row_clip_id(r, sample_dir))}
    names = dict(cand["eligible"])
    # A first-run OK in this list came back on ANOTHER route: that run counts as a failure.
    runs: dict[str, list[str]] = {
        cid: ["ROUTE-CHANGED" if first[cid].get("verdict") == "OK" else str(first[cid].get("verdict"))]
        for cid in names}
    # The lane + route a retry must come back on: the first run's for a brain-wording
    # CANT_DO (already checked equal to the baseline's), the BASELINE's for an EMPTY (an
    # empty transcript has no route of its own) or a route flip.
    prof = _profile_of(baseline) or {}
    ref = {cid: (dict(route_hash=prof[cid].get("route"), sig=prof[cid].get("sig"),
                      via_brain=prof[cid].get("via_brain"))
                 if first[cid].get("verdict") in ("EMPTY", "OK") and isinstance(prof.get(cid), dict)
                 else first[cid])
           for cid in names}
    passing: dict[str, dict[str, Any]] = {}
    problems: list[str] = []
    for _ in range(extra_runs):
        pending = [cid for cid, v in runs.items() if sum(x != "OK" for x in v) < FLAKY_FAIL_MIN]
        if not pending:
            break
        try:
            rep = rerun([names[c] for c in pending])
            recall = rep.get("memory_recall") if isinstance(rep, dict) else None
            if recall != "ok":
                raise RuntimeError(f"retry replay has no recall evidence ({recall})")
        except Exception as exc:
            problems.append(f"retry did not run ({type(exc).__name__}: {str(exc)[:120]})")
            for cid in pending:
                runs[cid].append("ERROR")
            continue
        got = {c: r for r in rep.get("rows") or [] if (c := _row_clip_id(r, sample_dir))}
        for cid in pending:
            v = _retry_verdict(ref[cid], got.get(cid))
            runs[cid].append(v)
            if v == "OK":
                passing.setdefault(cid, got[cid])

    clips: list[dict[str, Any]] = []
    adjusted = [dict(r) for r in rows]
    for r in adjusted:
        cid = _row_clip_id(r, sample_dir)
        if cid not in runs:
            continue
        fails = sum(v != "OK" for v in runs[cid])
        total = len(runs[cid])
        regressed = fails >= FLAKY_FAIL_MIN
        kind = {"EMPTY": "EMPTY (transient STT)",
                "ROUTE-CHANGED": "route flip (transient router)"}.get(runs[cid][0], "CANT_DO (brain wording)")
        if not regressed:
            r["verdict"], r["flaky"] = "OK", True
            # The per-clip checks read the route of the run that PASSED (first-run timings
            # are kept): an EMPTY first run has no route of its own to be judged on.
            for k in ("route_hash", "route_sig", "route", "heard_hash", "via_brain"):
                if k in passing.get(cid, {}):
                    r[k] = passing[cid][k]
        clips.append(dict(
            clip_id=cid, runs=runs[cid], failures=fails, of=total, regressed=regressed,
            note=(f"clip {cid} regressed: {fails}/{total} runs failed {runs[cid]}: counted"
                  if regressed else
                  f"clip {cid} flaky: {fails}/{total} {kind}: not counted")))
    new_report = dict(report, rows=adjusted, verdicts=_recount(adjusted),
                      first_run_verdicts=report.get("verdicts"))
    return dict(retried=True, report=new_report, clips=clips, note="; ".join(problems),
                profile=cand["profile"])


def plan_clip_set(baseline: dict[str, Any], sample_dir: str,
                  update_baseline: bool) -> dict[str, Any]:
    """Decide WHICH clips this run replays and whether speed may be compared.

    mode:
      pinned   replay exactly the baseline's set (names); speed comparable
      newest   replay the newest N (no usable pin); speed_ok is False when a
               baseline exists (re-pinning, or a legacy baseline with no set)
      missing  the baseline's set is not all present: do not run, refuse
    warn True means the refusal must surface as a failing reason; a deliberate
    re-pin (update_baseline) is not a failure.
    """
    if not baseline:
        return dict(mode="newest", names=None, speed_ok=True, warn=False,
                    note="no baseline yet")
    if update_baseline:
        return dict(mode="newest", names=None, speed_ok=False, warn=False,
                    note="--update-baseline re-pins the clip set to the newest N; "
                         "speed is not compared across sets")
    pinned = baseline_clip_ids(baseline)
    if pinned is None:
        return dict(mode="newest", names=None, speed_ok=False, warn=True,
                    note="baseline has no pinned clip set (recorded before set-pinning); "
                         "speed cannot be compared across a different clip set; "
                         "re-pin once with --update-baseline")
    names, missing = resolve_pinned(pinned, sample_dir)
    if missing:
        return dict(mode="missing", names=None, speed_ok=False, warn=True,
                    missing=len(missing), pinned=len(pinned),
                    note=f"{len(missing)} of the baseline's {len(pinned)} pinned clips are not in "
                         f"{sample_dir} (deleted, quarantined or renamed); refusing to compare "
                         "speed against a clip set that cannot be replayed; restore them or "
                         "re-pin with --update-baseline")
    return dict(mode="pinned", names=names, speed_ok=True, warn=False, note="")


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


def _replay_base(service_dir: str) -> str:
    """Base handed to the replay for --stt remote — ZOE_REPLAY_BASE_URL, else loopback,
    never ZOE_BASE_URL (runbook §20). Passed explicitly so the replay and the
    diagnosis cannot disagree about the target."""
    _, base = _service_env_get(service_dir, "ZOE_REPLAY_BASE_URL")
    return (base or "http://127.0.0.1:8000").strip()


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
        # Probe the endpoint the replay ACTUALLY targets, and NEVER resolve a name:
        # create_connection's timeout does not bound getaddrinfo (runbook §20).
        import ipaddress
        from urllib.parse import urlparse
        base = _replay_base(service_dir)
        try:
            u = urlparse(base)
            port = u.port or (443 if u.scheme == "https" else 80)
        except ValueError:
            u, port = None, None
        if u is None or u.scheme not in ("http", "https") or not u.hostname:
            # A malformed base is ITSELF the observation. Probing a fallback like
            # 127.0.0.1:80 would report the state of an endpoint the harness
            # cannot target — a guess with a confident tone.
            obs.append(f"replay base INVALID ({base!r}) — cannot probe the endpoint")
        else:
            try:
                ipaddress.ip_address(u.hostname)
            except ValueError:
                state = "NOT PROBED (hostname; the diagnosis never resolves names)"
            else:
                state = "reachable" if _port_open(u.hostname, port) else "REFUSED"
            obs.append(f"zoe-data {u.hostname}:{port} {state}")
        # Reported, never resolved: the public URL the replay must NOT use.
        _, public = _service_env_get(service_dir, "ZOE_BASE_URL")
        if public:
            obs.append(f"ZOE_BASE_URL={public} (public URL; not used by the replay)")
    return obs


def run_measure(samples: int, service_dir: str, user: str, timeout: int, stt: str,
                python: str | None = None, names: list[str] | None = None) -> dict[str, Any]:
    """Run measure_voice.py under the shared flock and return its aggregated JSON.

    names (basenames) pins the EXACT clips to replay (--files-from); without it
    the replay takes the newest N by mtime."""
    with tempfile.NamedTemporaryFile("r", suffix=".json", delete=False) as tf:
        out_json = tf.name
    files_from: str | None = None
    try:
        if names is not None:
            with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as nf:
                nf.write("\n".join(names) + "\n")
                files_from = nf.name
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
            *(["--files-from", files_from] if files_from else ["--last", str(samples)]),
            "--user", user,
            "--service-dir", service_dir, "--json", out_json, "--timeout", str(timeout),
            "--stt", stt, "--python", python,
        ]
        if stt == "remote":
            cmd += ["--base-url", _replay_base(service_dir)]
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
        for leftover in (out_json, files_from):
            if not leftover:
                continue
            try:
                os.unlink(leftover)
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


def compare(cur: dict[str, Any], baseline: dict[str, Any], warn_ratio: float, warn_ms: float,
            *, compare_speed: bool = True) -> list[str]:
    """Function + speed verdicts. compare_speed=False is used ONLY when the run
    could not replay the baseline's clip set (plan_clip_set); the caller then owns
    the loud 'speed refused' reason, it must never be a silent skip."""
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
    if not compare_speed:
        return warnings
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
    # Length-normalised STT: ms of STT per second of audio. Robust to WHICH clips
    # were replayed, so it catches a slow STT even when the set is not identical.
    cur_ps, base_ps = cur.get("stt_ms_per_audio_s"), base.get("stt_ms_per_audio_s")
    if (isinstance(cur_ps, (int, float)) and isinstance(base_ps, (int, float)) and base_ps > 0
            and cur_ps / base_ps >= warn_ratio and cur_ps - base_ps >= STT_PER_S_FLOOR):
        warnings.append(f"SPEED stt_ms_per_audio_s: {cur_ps:.0f}ms/s vs baseline {base_ps:.0f}ms/s "
                        f"({cur_ps / base_ps:.2f}x, +{cur_ps - base_ps:.0f}ms/s)")
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
    cur_ps, base_ps = summary.get("stt_ms_per_audio_s"), (base or {}).get("stt_ms_per_audio_s")
    if isinstance(cur_ps, (int, float)) or isinstance(base_ps, (int, float)):
        entry = dict(cur_ms=cur_ps, baseline_ms=base_ps)
        if isinstance(cur_ps, (int, float)) and isinstance(base_ps, (int, float)) and base_ps > 0:
            entry["delta_ms"] = round(cur_ps - base_ps, 1)
            entry["ratio"] = round(cur_ps / base_ps, 3)
        out["stt_ms_per_audio_s"] = entry
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
                vad: dict[str, Any] | None = None,
                speed_comparison: str = "not-applicable",
                flaky: dict[str, Any] | None = None,
                clip_eval: dict[str, Any] | None = None) -> dict[str, Any]:
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
        "clip_set_size": len(baseline_clip_ids(baseline) or []),
    }
    payload = {
        "status": status,                       # pass | fail | skip | error
        "timestamp": ts,
        "created_at": ts,                       # back-compat: router_selftrain reads mtime + summary
        "reason": reason,
        "said_vs_did_regressions": said_vs_did,
        "per_stage_speed_deltas": speed_deltas,
        # compared | refused: <why> | skipped: <why> | not-applicable. A speed
        # verdict is only meaningful when the baseline's own clip set was replayed.
        "speed_comparison": speed_comparison,
        "baseline_ref": baseline_ref,
        # WHAT CODE THIS IS EVIDENCE FOR. Read by voice_gate_check.py
        # --expect-revision (the PR gate). getattr: emit_result is also called
        # with lightweight arg objects that carry no service_dir.
        "revision": service_revision(getattr(args, "service_dir", None)),
        "summary": summary,                     # back-compat: n_samples / ok_rate / medians_ms
        # Flaky brain-wording tolerance (see apply_flaky_tolerance). Present on EVERY
        # exit path so a reader never has to guess: flaky_retried says whether any clip
        # was re-run; flaky_clips records each one's per-run verdicts and whether it was
        # counted. A tolerated clip is VISIBLE here, never silently absorbed.
        "flaky_retried": bool(flaky and flaky.get("retried")),
        "flaky_clips": list((flaky or {}).get("clips") or []),
        "flaky_note": str((flaky or {}).get("note") or ""),
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
        # PER-CLIP OUTCOME ASSERTIONS (T-3, voice_clip_checks). `clip_checks: true` is this
        # artifact's CLAIM that every pinned clip was held to its expectation; the deploy
        # checker then re-derives the verdict from `clip_summary` (a pass with a failed or
        # zero checked clips is refused). `clip_results` carries per clip: clip id, result
        # (pass | fail | unchecked), verdict, route hashes and code labels; never a name,
        # transcript or reply. On exit paths that never replayed, the summary is None.
        "clip_checks": True,
        "clip_summary": (clip_eval or {}).get("summary"),
        "clip_results": list((clip_eval or {}).get("results") or []),
        "self_test": bool(getattr(args, "self_test", False)),
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


def _harness_quiet_refusal(*, check=None, wait=None) -> "str | None":
    """None when the probe may start; else why not. Hours refuse at once; a busy panel is waited out for up to
    ``ZOE_HARNESS_PANEL_WAIT_S`` (default 2700 s) first. On a go, the replay is told to yield between clips."""
    # load by FILE PATH, never via sys.path: scripts/maintenance holds names that shadow service modules
    # (pi_runtime_probe.py exists in both), and this runs inside processes that import service code
    import importlib.util

    harness_quiet = sys.modules.get("harness_quiet")
    if harness_quiet is None:
        spec = importlib.util.spec_from_file_location("harness_quiet", Path(__file__).resolve().parent / "harness_quiet.py")
        harness_quiet = importlib.util.module_from_spec(spec)
        sys.modules["harness_quiet"] = harness_quiet
        spec.loader.exec_module(harness_quiet)

    check = check or harness_quiet.check
    wait = wait or harness_quiet.wait_for_panel_quiet
    verdict = check("voice_regression_probe")
    if not verdict.allowed and verdict.kind == "panel":
        try:
            max_wait = float(os.environ.get("ZOE_HARNESS_PANEL_WAIT_S", "2700"))
        except ValueError:
            max_wait = 2700.0
        if wait(max_wait):
            verdict = check("voice_regression_probe")
    print(verdict.reason, flush=True)
    if not verdict.allowed:
        return verdict.reason
    if verdict.kind != "forced":
        os.environ["ZOE_HARNESS_YIELD_TO_PANEL"] = "1"   # inherited by the replay subprocess
    return None


def load_pin_ids(path: Path) -> list[str]:
    """Clip ids to pin: a JSON list, or an object with 'clip_ids' (voice_corpus_strata.py's
    proposal). Raises ValueError on anything else."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    ids = data.get("clip_ids") if isinstance(data, dict) else data
    if not isinstance(ids, list) or not ids or not all(isinstance(i, str) and len(i) == CLIP_ID_LEN for i in ids):
        raise ValueError(f"{path}: expected a non-empty list of {CLIP_ID_LEN}-char clip ids")
    if len(set(ids)) != len(ids):
        raise ValueError(f"{path}: duplicate clip ids")
    return ids


def plan_from_pin_file(path: Path, sample_dir: str) -> dict[str, Any]:
    """The plan for ``--update-baseline --pin-from``: exactly the named clips, never
    speed-compared (a new set), refused when any is missing or the file is bad."""
    try:
        ids = load_pin_ids(path)
    except (OSError, ValueError) as exc:
        return dict(mode="missing", names=None, speed_ok=False, warn=True, note=f"--pin-from: {exc}")
    names, missing = resolve_pinned(ids, sample_dir)
    if missing:
        return dict(mode="missing", names=None, speed_ok=False, warn=True,
                    note=f"--pin-from: {len(missing)} of {len(ids)} clip ids are not in {sample_dir}")
    return dict(mode="repin", names=names, speed_ok=False, warn=False,
                note=f"--pin-from re-pins the clip set to {len(names)} chosen clips; speed is not "
                     "compared across sets")


# The negative controls --self-test injects; MUST equal replay_samples.REPLAY_FAULTS
# (pinned by tests/unit/test_voice_probe_clip_checks.py).
SELF_TEST_FAULTS = ("blank_stt", "misroute")


def run_self_test(args, baseline: dict[str, Any]) -> int:
    """THE PROBE'S NEGATIVE CONTROL (T-3). Replays the baseline's pinned clips once per
    injected fault (replay_samples ZOE_REPLAY_FAULT) and judges each run with the SAME
    per-clip checks a gate run uses. Exit 0 only when every fault went red
    (voice_clip_checks.fault_caught); 1 when a fault stayed green (the instrument is
    broken: a gate that cannot see a blank STT or a misrouting router is not a gate);
    2 when it could not run. Writes ONLY args.results (the self-test file, never the
    shared artifact: main() redirected it), never a baseline, and no flaky retries."""
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    faults: dict[str, Any] = {}

    def _finish(status: str, reason: str) -> int:
        payload = {"self_test": True, "status": f"self-test-{status}", "self_test_status": status,
                   "timestamp": ts, "reason": reason, "faults": faults,
                   "revision": service_revision(args.service_dir),
                   "baseline_ref": {"path": str(args.baseline),
                                    "created_at": (baseline or {}).get("created_at"),
                                    "clip_set_size": len(baseline_clip_ids(baseline) or [])},
                   "expectations": str(args.expectations), "clip_checks": False}
        write_json(args.results, payload)
        try:
            args.trend.parent.mkdir(parents=True, exist_ok=True)
            with open(args.trend, "a") as fh:
                fh.write(json.dumps(payload, separators=(",", ":")) + "\n")
        except OSError:
            pass
        print(f"SELF-TEST {status.upper()}: {reason}")
        print(f"Results: {args.results}")
        return {"pass": 0, "fail": 1}.get(status, 2)

    owner_labels, problem = vcc.load_expectations(args.expectations)
    if problem:
        return _finish("error", f"clip expectations: {problem}")
    plan = plan_clip_set(baseline, args.sample_dir, False)
    if not baseline or plan["mode"] != "pinned":
        return _finish("error", "the self-test needs a baseline whose pinned clips are all present "
                                f"({plan.get('note') or 'no baseline'})")
    pinned = baseline_clip_ids(baseline) or []
    prof = vcc.baseline_profile(baseline)
    expected_ok = sum(1 for c in pinned if c in owner_labels or (prof.get(c) or {}).get("verdict") == "OK")
    if expected_ok == 0:
        return _finish("error", "no pinned clip has an expectation (baseline without a clip profile, no "
                                "owner labels): there is nothing for a fault to violate")
    avail = mem_available_mb()
    if avail < args.min_mem_mb:
        return _finish("error", f"available memory {avail}MB < {args.min_mem_mb}MB - not run")
    os.environ["ZOE_VOICE_SAMPLE_DIR"] = args.sample_dir
    run_started_utc = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    try:
        for fault in SELF_TEST_FAULTS:
            print(f"self-test: replaying {len(plan['names'])} pinned clips with fault={fault}", flush=True)
            os.environ[FAULT_ENV] = fault
            try:
                rep = run_measure(args.samples, args.service_dir, args.user, args.timeout, args.stt,
                                  python=args.python, names=plan["names"])
            except Exception as exc:  # noqa: BLE001 - recorded, never a pass
                faults[fault] = {"caught": None, "error": f"replay could not run: {str(exc)[:300]}"}
                continue
            finally:
                os.environ.pop(FAULT_ENV, None)
            if rep.get("fault") != fault:
                faults[fault] = {"caught": None,
                                 "error": f"the replay under {args.service_dir} did not apply the fault "
                                          f"(reported {rep.get('fault')!r}; it predates fault injection)"}
                continue
            ce = vcc.evaluate(rep.get("rows") or [], baseline, owner_labels, pinned,
                              lambda r: _row_clip_id(r, args.sample_dir))
            caught, note = vcc.fault_caught(fault, ce, expected_ok)
            kinds: dict[str, int] = {}
            for r in ce["results"]:
                if r["result"] == "fail":
                    kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
            faults[fault] = {"caught": caught, "note": note, "clip_summary": ce["summary"],
                             "failed_kinds": kinds, "verdicts": rep.get("verdicts")}
            print(f"self-test: fault={fault} -> {'RED (caught)' if caught else 'GREEN (MISSED)'}: {note}")
    finally:
        cleanup_replay_artifacts(run_started_utc, args)
    if any(f.get("caught") is None for f in faults.values()):
        return _finish("error", "; ".join(f"{k}: {v['error']}" for k, v in faults.items() if v.get("caught") is None))
    missed = [k for k, v in faults.items() if not v["caught"]]
    if missed:
        return _finish("fail", f"the per-clip checks stayed GREEN on injected fault(s) {missed} - the "
                               "instrument is broken; do not trust a pass until this is fixed")
    notes = ", ".join(f"{k}: {v['note']}" for k, v in faults.items())
    return _finish("pass", f"every injected fault went red ({notes})")


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
    ap.add_argument("--update-baseline", action="store_true", help="Save this run as the new comparison baseline and PIN its clip set "
                         "(the newest --samples clips).")
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
                    help="voice corpus dir for the replay and the VAD stage (default: ZOE_VOICE_SAMPLE_DIR "
                         "or ~zoe/.zoe-voice-samples, the replay harness's own default)")
    ap.add_argument("--python", default=None,
                    help=f"interpreter for the whole run (default: ${PYTHON_ENV}, else the "
                         "zoe-data unit's ExecStart — scripts/lib/service_python.py). The "
                         "probe re-executes itself on it, so VAD + replay match the service.")
    ap.add_argument("--no-cleanup", action="store_true",
                    help="skip the post-run replay-artifact cleanup (soft-delete of rows created during the replay window)")
    ap.add_argument("--expectations", type=Path, default=vcc.default_expectations(),
                    help="owner per-clip labels (OFF the repo; default ~/.zoe/voice-gate/clip_expectations.json, "
                         "env ZOE_VOICE_CLIP_EXPECTATIONS). Absent = the baseline's per-clip profile is the bar.")
    ap.add_argument("--self-test", action="store_true",
                    help="the NEGATIVE CONTROL: replay the pinned clips with each injected fault (blank STT, a "
                         "misrouting router) and exit 0 only if the per-clip checks go red on every one. Writes "
                         "--self-test-results, never the shared artifact, trend or baseline.")
    ap.add_argument("--self-test-results", type=Path, default=DEFAULT_SELFTEST_RESULTS)
    ap.add_argument("--pin-from", type=Path, default=None,
                    help="with --update-baseline: pin EXACTLY these clip ids (a JSON list, or a JSON object with "
                         "'clip_ids', e.g. voice_corpus_strata.py's proposal) instead of the newest --samples")
    args = ap.parse_args()
    if args.pin_from and not args.update_baseline:
        ap.error("--pin-from re-pins the clip set: use it with --update-baseline")
    if args.self_test and args.update_baseline:
        ap.error("--self-test never writes a baseline")
    # A fault in the environment is only ever the self-test's own, per replay: a normal
    # run strips it so an inherited ZOE_REPLAY_FAULT can never shape gate evidence.
    stray_fault = os.environ.pop(FAULT_ENV, None)
    if stray_fault:
        print(f"NOTE: ignoring {FAULT_ENV}={stray_fault!r} from the environment (only --self-test injects faults)",
              file=sys.stderr)
    if args.self_test:
        shared = Path(os.environ.get("ZOE_VOICE_RESULTS", DEFAULT_RESULTS))
        # ...nor whatever --results names on THIS command line (a caller's shared artifact)
        if args.self_test_results.resolve() in (shared.resolve(), DEFAULT_RESULTS.resolve(),
                                                args.results.resolve()):
            ap.error("--self-test-results must not be the shared voice-gate artifact (or --results)")
        # Every exit path below writes args.results: point it (and the trend) away from
        # the deploy gate's file BEFORE anything can write.
        args.results, args.trend = args.self_test_results, DEFAULT_SELFTEST_TREND
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

    # Harness quiet hours (P-2, harness_quiet.py) - BEFORE the lock and before any artifact write: a refused
    # run must not clobber the single shared voice-gate artifact with a skip. The landing loop's probe
    # (ZOE_HARNESS_LANDING=1) is exempt from the hours and only yields to the panel; a panel-busy refusal
    # waits (bounded) instead of failing, and the replay itself yields between clips.
    refusal = _harness_quiet_refusal()
    if refusal:
        print(f"REFUSED: {refusal}", file=sys.stderr)
        return 3

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

    if args.self_test:
        return run_self_test(args, baseline)

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

    # WHICH clips this run replays: the baseline's pinned set, never "whatever is
    # newest today" (see the pinned clip set block above).
    os.environ["ZOE_VOICE_SAMPLE_DIR"] = args.sample_dir   # replay and probe read ONE corpus
    plan = plan_clip_set(baseline, args.sample_dir, args.update_baseline)
    if args.pin_from:
        plan = plan_from_pin_file(args.pin_from, args.sample_dir)
    if plan["mode"] == "missing":
        reason = f"BASELINE-SET: {plan['note']}"
        print(f"ERROR: {reason}", file=sys.stderr)
        emit_result(args, status="error", summary=dict(EMPTY_SUMMARY), said_vs_did=[],
                    speed_deltas={}, baseline=baseline, reason=reason,
                    vad=_vad_stage(args), speed_comparison=f"refused: {plan['note']}")
        return 2
    if plan["mode"] == "pinned":
        print(f"replaying the baseline's {len(plan['names'])} pinned clips "
              f"(--samples {args.samples} ignored; re-pin with --update-baseline)")
    elif plan["note"]:
        print(f"NOTE {plan['note']}")

    run_started_utc = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    try:
        report = run_measure(args.samples, args.service_dir, args.user, args.timeout, args.stt,
                             python=args.python, names=plan["names"])
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
    summary.update(clip_set_summary(report, args.sample_dir))
    # A report that carries an injected fault is never evidence (the self-test's own
    # replays go through run_self_test, not here).
    fault_error = (f"the replay reported an injected fault ({report.get('fault')!r}) on a normal run - "
                   "NOT evidence" if report.get("fault") else None)
    owner_labels, exp_problem = vcc.load_expectations(args.expectations)
    speed_ok, speed_note, speed_warn = plan["speed_ok"], plan["note"], plan["warn"]
    if plan["mode"] == "pinned" and set(summary["clip_ids"]) != set(baseline_clip_ids(baseline) or []):
        # The replay silently dropped or swapped a clip: not the baseline's set.
        speed_ok, speed_warn = False, True
        speed_note = (f"replayed {len(summary['clip_ids'])} clips but the baseline pinned "
                      f"{len(baseline_clip_ids(baseline) or [])}; the sets differ, so speed is not comparable")

    def _judge(rep: dict[str, Any]) -> tuple[list[str], dict[str, Any] | None]:
        """Function + speed warnings plus the PER-CLIP outcome checks (T-3)."""
        summ = summarize(rep)
        summ.update(clip_set_summary(rep, args.sample_dir))
        warns = compare(summ, baseline, args.warn_ratio, args.warn_ms, compare_speed=speed_ok)
        ce = None
        if baseline:
            # A re-pin judges the clips its OLD baseline knew (possibly none); a normal run
            # judges exactly the pinned set, and a run whose pinned clips carry no
            # expectation at all has judged nothing, which is never a pass.
            ids = summ.get("clip_ids") if args.update_baseline else (baseline_clip_ids(baseline) or summ.get("clip_ids"))
            ce = vcc.evaluate(rep.get("rows") or [], baseline, owner_labels, ids,
                              lambda r: _row_clip_id(r, args.sample_dir))
            warns += ce["regressions"]
            if not args.update_baseline and ce["summary"]["checked"] == 0:
                warns.append("FUNCTION: no pinned clip has a per-clip expectation (the baseline carries no "
                             "clip profile) - re-baseline with --update-baseline")
        return warns, ce

    warnings, clip_eval = _judge(report)
    flaky: dict[str, Any] | None = None
    if (any(w.startswith("FUNCTION") for w in warnings) and baseline
            and not args.update_baseline and recall_gate(summary) is None and not fault_error):
        # Same code, same harness: re-run only the brain-wording failures and the clips
        # that came back EMPTY after being OK (<= 2 more times, <= 3 clips). Anything
        # deterministic (a route change, an ERROR) stays a first-run failure.
        flaky = apply_flaky_tolerance(
            report, baseline,
            lambda names: run_measure(args.samples, args.service_dir, args.user, args.timeout,
                                      args.stt, python=args.python, names=names),
            args.sample_dir)
        if flaky["retried"]:
            report = flaky["report"]
            summary = summarize(report)
            summary.update(clip_set_summary(report, args.sample_dir))
            warnings, clip_eval = _judge(report)
        if flaky["note"]:
            print(f"NOTE flaky tolerance: {flaky['note']}")
        for c in flaky["clips"]:
            print(f"NOTE {c['note']}  runs={c['runs']}")
    if speed_warn and not speed_ok:
        warnings.append(f"BASELINE-SET: {speed_note}")
    if speed_ok:
        speed_comparison = "compared" if baseline else "not-applicable"
    else:
        speed_comparison = ("refused: " if speed_warn else "skipped: ") + speed_note
    said_vs_did = [w for w in warnings if w.startswith("FUNCTION")]
    speed_deltas = stage_speed_deltas(summary, baseline)

    m = summary["medians_ms"]
    _rate = "n/a" if summary["ok_rate"] is None else f"{summary['ok_rate']:.0%}"
    print(f"Zoe voice regression probe — {summary['n_samples']} samples, "
          f"OK {summary['ok']}/{summary['scoreable']} ({_rate}), "
          f"fail={summary['fail']}, empty={summary['empty']}/{summary['total']}")
    print(f"  medians: STT={m.get('stt_ms')}  brain={m.get('brain_ms')}  e2e={m.get('e2e_ms')}  (ms; warm-harness, relative only)")
    if clip_eval is not None:
        cs = clip_eval["summary"]
        print(f"  per-clip: {cs['passed']}/{cs['checked']} held their expected outcome "
              f"({cs['failed']} failed, {cs['unchecked']} unchecked, {cs['owner_labelled']} owner-labelled; "
              f"compared by {', '.join(cs['compared']) or 'n/a'})")
        for r in clip_eval["results"]:
            if r["result"] == "fail":
                print(f"    FAIL clip {r['clip_id']}: {r['why']}")
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

    pin_error = None
    if (args.update_baseline or not args.baseline.exists()) and not summary.get("clip_ids"):
        # A baseline with no pinned set re-opens the very drift this guards.
        pin_error = "no replayed clip could be identified; refusing to write a baseline with no pinned set"
    if fault_error:
        print(f"ERROR: {fault_error}", file=sys.stderr)
    elif recall_error:
        # NEVER write a recall-off run as the bar, and never call it a pass.
        print(f"ERROR: {recall_error}", file=sys.stderr)
    elif pin_error:
        print(f"ERROR: {pin_error}", file=sys.stderr)
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
    if fault_error:
        status = "error"
        reason_parts.insert(0, fault_error)
    elif recall_error:
        status = "error"
        reason_parts.insert(0, recall_error)
    elif pin_error:
        status = "error"
        reason_parts.insert(0, pin_error)
    if exp_problem:
        # An owner's labels that cannot be read must not silently stop being checked.
        status = "error"
        reason_parts.insert(0, f"clip expectations: {exp_problem}")
    if not cleanup_ok:
        reason_parts.append("replay-artifact cleanup FAILED (calendar/lists may be dirty)")
    if vad.get("status") in ("fail", "error"):
        reason_parts.append(f"VAD {vad['status'].upper()}: {vad.get('reason')}")
    emit_result(args, status=status, summary=summary, said_vs_did=said_vs_did,
                speed_deltas=speed_deltas, baseline=baseline, reason="; ".join(reason_parts),
                vad=vad, speed_comparison=speed_comparison, flaky=flaky, clip_eval=clip_eval)

    print(f"Results: {args.results}  Trend: {args.trend}  (status={status})")
    if status == "error":
        return 2
    return 0 if status == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main(reexec=True))
