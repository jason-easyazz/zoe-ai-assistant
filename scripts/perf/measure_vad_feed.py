#!/usr/bin/env python3
"""Silero feed probe — what the panel daemon's VAD hears, before vs after a feed change.

WHY THIS EXISTS
---------------
Until 2026-10-19 the daemon's ``_vad_prob`` fed each 1,280-sample chunk to Silero as two
512-sample windows and DROPPED the last 256 samples, through ONE model shared by every
consumer, never calling ``reset_states()`` (open-problems 2026-10-05; deep dive 2 V-3/V-5).
The fix (``_VadStream``) carries the remainder and gives each stream its own state. The
open-problems bar for landing it: "the offline corpus diff of probabilities, onset lag and
endpoint close time is zero or the fix lands" - so this probe reports exactly those three,
per arm, on the replay corpus (``~/.zoe-voice-samples``), plus a correctness check:

  * ``reference`` = upstream semantics: ``reset_states()`` then every contiguous 512-sample
    window of the clip (what ``silero_vad.get_speech_timestamps`` does). The new feed must
    reproduce it exactly (``new_vs_reference_max_abs`` == 0).
  * ``old`` = the shipped feed of ``--baseline-daemon`` (a copy of the old daemon source),
    reset once per clip - KINDER to the old code than the live panel, which never reset.
  * ``new`` = the shipped feed of ``--daemon`` (this checkout by default).

Endpoint close: each arm's REAL ``_Endpointer`` (live config: ``ZOE_VAD_TAIL_MS`` 640 by
default) runs over the untrimmed clip plus 3 s of silence; the close sample is compared
old vs new on the SAME clip, so no detector-dependent trim moves the zero. The synthetic
pause / false-cut table is ``measure_endpointing.py --daemon <each source>``. Content-free output: counts, medians
and per-clip numbers keyed by a hash of the file name, never transcripts or audio.

Run under the voice-harness lock (one Silero per arm, ~300 MB with torch):
    flock /tmp/zoe-voice-harness.lock python3 scripts/perf/measure_vad_feed.py \\
        --baseline-daemon /tmp/old_daemon.py --samples 200 --json out.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
from pathlib import Path
from typing import Any

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from measure_endpointing import CORPUS, DAEMON, load_daemon, read_wav_mono16, run_stream, silence  # noqa: E402


def chunk_probs(mod, audio: np.ndarray, chunk: int) -> list[float]:
    """Per-chunk probabilities exactly as the daemon's consumers see them, one fresh stream per clip."""
    if hasattr(mod, "_new_vad_stream"):
        vad = mod._new_vad_stream()
    else:
        vad, _ = mod._get_silero_vad()
        vad.reset_states()
    return [mod._vad_prob(vad, audio[s:s + chunk]) for s in range(0, len(audio) - chunk + 1, chunk)]


def reference_probs(model, audio: np.ndarray, chunk: int, window: int = 512) -> list[float]:
    """Upstream semantics, folded to the daemon's chunk grid: the max over the contiguous
    512-sample windows that END inside each chunk (that is what a carry-over feed scores)."""
    import torch
    model.reset_states()
    f32 = audio.astype(np.float32) / 32768.0
    ends: list[tuple[int, float]] = []
    with torch.no_grad():
        for s in range(0, len(f32) - window + 1, window):
            ends.append((s + window, float(model(torch.from_numpy(f32[s:s + window].copy()), 16000).item())))
    out: list[float] = []
    for c in range(0, len(audio) - chunk + 1, chunk):
        inside = [p for e, p in ends if c < e <= c + chunk]
        out.append(max(inside) if inside else (out[-1] if out else 0.0))
    return out


def first_at(probs: list[float], thr: float) -> int | None:
    return next((i for i, p in enumerate(probs) if p >= thr), None)


def last_at(probs: list[float], thr: float) -> int | None:
    idx = [i for i, p in enumerate(probs) if p >= thr]
    return idx[-1] if idx else None


def summarize(rows: list[dict[str, Any]], arm: str, thr: float, chunk_ms: float) -> dict[str, Any]:
    peaks = [r[arm]["peak"] for r in rows]
    fracs = [r[arm]["speech_frac"] for r in rows]
    detected = sum(1 for r in rows if r[arm]["onset"] is not None)
    return {
        "clips": len(rows),
        f"detected_at_{thr}": detected,
        "peak_median": round(statistics.median(peaks), 4) if peaks else None,
        "speech_frac_median": round(statistics.median(fracs), 4) if fracs else None,
        "onset_ms_median": (round(statistics.median([r[arm]["onset"] * chunk_ms for r in rows
                                                     if r[arm]["onset"] is not None]), 1)
                            if detected else None),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--daemon", type=Path, default=DAEMON, help="daemon source with the NEW feed")
    ap.add_argument("--baseline-daemon", type=Path, required=True, help="daemon source with the OLD feed")
    ap.add_argument("--corpus", type=Path, default=CORPUS)
    ap.add_argument("--samples", type=int, default=200, help="newest N top-level corpus WAVs")
    ap.add_argument("--threshold", type=float, help="speech threshold (default: the daemon's VAD_ENDPOINT_THRESHOLD)")
    ap.add_argument("--tail-flag-ms", type=int, default=640, help="ZOE_VAD_TAIL_MS for the close arm (live: 640)")
    ap.add_argument("--json", type=Path)
    args = ap.parse_args()

    old = load_daemon(args.baseline_daemon)
    new = load_daemon(args.daemon)
    for mod in (old, new):
        mod.VAD_ENDPOINT_ENABLED = True
        mod.ZOE_VAD_TAIL_MS = args.tail_flag_ms
    model, _ = new._get_silero_vad()
    if model is None or old._get_silero_vad()[0] is None:
        print("Silero unavailable - refusing to report a fiction", file=sys.stderr)
        return 2
    import copy
    ref_model = copy.deepcopy(model)
    rate, chunk = new.SAMPLE_RATE, new.CHUNK_SIZE
    chunk_ms = 1000.0 * chunk / rate
    thr = args.threshold if args.threshold is not None else float(new.VAD_ENDPOINT_THRESHOLD)

    files = sorted(args.corpus.glob("*.wav"))[-args.samples:]
    rows: list[dict[str, Any]] = []
    max_abs = 0.0
    for path in files:
        audio = read_wav_mono16(path, rate)
        if audio is None or len(audio) < 4 * chunk:
            continue
        arms = {"old": chunk_probs(old, audio, chunk), "new": chunk_probs(new, audio, chunk),
                "reference": reference_probs(ref_model, audio, chunk)}
        max_abs = max(max_abs, max(abs(a - b) for a, b in zip(arms["new"], arms["reference"])))
        row: dict[str, Any] = {"clip": hashlib.sha256(path.name.encode()).hexdigest()[:12]}
        for arm, probs in arms.items():
            row[arm] = {"peak": round(max(probs), 4),
                        "speech_frac": round(sum(p >= thr for p in probs) / len(probs), 4),
                        "onset": first_at(probs, thr), "last": last_at(probs, thr)}
        both = row["old"]["onset"] is not None and row["new"]["onset"] is not None
        row["onset_shift_ms"] = (row["old"]["onset"] - row["new"]["onset"]) * chunk_ms if both else None
        both = row["old"]["last"] is not None and row["new"]["last"] is not None
        row["last_shift_ms"] = (row["new"]["last"] - row["old"]["last"]) * chunk_ms if both else None
        tail = np.concatenate([audio, silence(3000, rate)])
        closes = {arm: run_stream(mod, tail, chunk) for arm, mod in (("old", old), ("new", new))}
        row["close_shift_ms"] = (1000.0 * (closes["new"] - closes["old"]) / rate
                                 if None not in closes.values() else None)
        # A CUT: the recording closes before the last chunk the reference (upstream-correct) VAD
        # calls speech - the speaker was still talking. The clip's own capture already ended on
        # the live endpointer, so a close before that point is a said-vs-did risk.
        ref_last = row["reference"]["last"]
        for arm in ("old", "new"):
            row[f"cut_{arm}"] = bool(closes[arm] is not None and ref_last is not None
                                     and closes[arm] <= ref_last * chunk)
        row["mean_abs_diff_old_new"] = round(float(np.mean(np.abs(np.array(arms["old"]) - np.array(arms["new"])))), 4)
        rows.append(row)
    if not rows:
        print(f"no usable clips in {args.corpus}", file=sys.stderr)
        return 2

    shifts = [r["onset_shift_ms"] for r in rows if r["onset_shift_ms"] is not None]
    closes_d = sorted(r["close_shift_ms"] for r in rows if r["close_shift_ms"] is not None)
    lasts = [r["last_shift_ms"] for r in rows if r["last_shift_ms"] is not None]
    result = {
        "threshold": thr, "chunk_ms": chunk_ms, "n": len(rows),
        "new_vs_reference_max_abs": round(max_abs, 6),
        "arms": {arm: summarize(rows, arm, thr, chunk_ms) for arm in ("old", "new", "reference")},
        "onset_shift_ms": {"median": statistics.median(shifts) if shifts else None,
                           "new_earlier": sum(s > 0 for s in shifts), "new_later": sum(s < 0 for s in shifts),
                           "same": sum(s == 0 for s in shifts)},
        "last_speech_shift_ms": {"median": statistics.median(lasts) if lasts else None,
                                 "new_later": sum(s > 0 for s in lasts), "new_earlier": sum(s < 0 for s in lasts),
                                 "same": sum(s == 0 for s in lasts)},
        "endpoint_close_shift_ms": {
            "median": statistics.median(closes_d) if closes_d else None,
            "p10": closes_d[int(0.1 * (len(closes_d) - 1))] if closes_d else None,
            "p90": closes_d[int(0.9 * (len(closes_d) - 1))] if closes_d else None,
            "new_earlier": sum(x < 0 for x in closes_d), "new_later": sum(x > 0 for x in closes_d),
            "same": sum(x == 0 for x in closes_d),
        },
        "cuts_before_reference_speech_end": {arm: sum(r[f"cut_{arm}"] for r in rows) for arm in ("old", "new")},
        "mean_abs_diff_old_new_median": round(statistics.median(r["mean_abs_diff_old_new"] for r in rows), 4),
        "rows": rows,
    }
    print(json.dumps({k: v for k, v in result.items() if k != "rows"}, indent=2))
    if args.json:
        args.json.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f"wrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
