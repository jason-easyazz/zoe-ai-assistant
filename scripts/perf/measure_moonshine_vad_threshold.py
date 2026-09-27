#!/usr/bin/env python3
"""Moonshine quiet-clip probe — does Moonshine's OWN VAD drop real commands?

WHY THIS EXISTS
---------------
`transcribe_without_streaming` runs Moonshine's internal Silero VAD (averaged over
a 0.5 s window, `vad_threshold` default 0.5) on top of the Pi daemon's VAD, and
only segments it calls speech reach the encoder. The replay gate scores a clip
EMPTY when STT returns nothing — and its EMPTY clips return in 30-80 ms, i.e. the
encoder never ran. On low-level far-field audio that may be a real, quiet command
the double gate threw away, not silence. This probe re-runs the SAME engine the
service uses (in-process `moonshine_voice`, same model/arch, same wake-word strip)
at several `vad_threshold` values and reports, per clip, whether the transcript
became non-empty and whether it looks like a command or a hallucination.

PRIVACY: the corpus is real household audio. This probe NEVER prints or writes a
transcript. It reports only counts, word counts, a coarse pattern category, and a
short hash (so "did the text change between arms" is answerable without the text).

Run with the zoe-data venv's python (moonshine-voice lives there), one arm at a
time, niced — it loads ONE ~300 MB Moonshine model per arm, never two:

    nice -n 15 ~/.zoe/venvs/zoe-data-py312/bin/python \\
        scripts/perf/measure_moonshine_vad_threshold.py --last 20 \\
        --thresholds default,0.5,0.3,0.0 --repeats 3 --json /tmp/moonshine_vad.json

`default` = no options (what zoe-data runs today). Recommendation-only instrument:
it changes nothing in the service.
"""
from __future__ import annotations

import argparse
import gc
import glob
import hashlib
import json
import os
import re
import statistics
import sys
import time
import wave
from pathlib import Path
from typing import Any

import numpy as np

REPO = Path(__file__).resolve().parents[2]
SERVICE = REPO / "services" / "zoe-data"
CORPUS = Path.home() / ".zoe-voice-samples"

# Silence/noise hallucinations of Whisper-family decoders (upstream moonshine #167
# shows "Thank you for watching" on noise). Matched on the NORMALISED transcript.
_HALLUCINATION_RE = re.compile(
    r"^(thank you( (so much|for watching|very much))?|thanks( for watching)?|you|bye( bye)?|"
    r"oh|um+|uh+|hmm+|mm+|so|the end|subtitles.*|music|applause|"
    r"please subscribe.*|i'?m sorry)$")
# One-word turns that ARE legitimate replies to Zoe (a confirmation, a stop).
_SHORT_REPLIES = frozenset("yes no yeah yep nope okay ok stop pause cancel thanks".split())
# Words a household command or question to Zoe almost always contains.
_COMMAND_CUES = frozenset((
    "add put remind reminder set turn switch play pause stop skip resume what what's whats "
    "when where who how is are can could tell show open call text send list timer weather "
    "time light lights music calendar shopping zoe volume louder quieter next start cancel "
    "delete remove schedule book check find search"
).split())


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s']", " ", (text or "").lower())).strip()


def categorise(text: str) -> str:
    """Pattern-only label; the text itself never leaves this function."""
    norm = _normalise(text)
    if not norm:
        return "empty"
    words = norm.split()
    if len(words) == 1 and words[0] in _SHORT_REPLIES:
        return "short_reply"
    if _HALLUCINATION_RE.match(norm):
        return "likely_hallucination"
    if len(words) >= 3 and any(w in _COMMAND_CUES for w in words):
        return "plausible_command"
    if len(words) <= 1:
        return "likely_hallucination"
    return "unclear"


def text_hash(text: str) -> str:
    norm = _normalise(text)
    return hashlib.sha1(norm.encode()).hexdigest()[:8] if norm else ""


def select_newest(sample_dir: Path, last: int) -> list[str]:
    """Same slice as services/zoe-data/tests/replay_samples._select(--last N):
    TOP-LEVEL wavs ordered by (mtime, name), newest N."""
    rows = sorted((os.stat(p).st_mtime, os.path.basename(p), p)
                  for p in glob.glob(os.path.join(sample_dir, "*.wav")))
    return [r[2] for r in rows][-last:]


def clip_level(path: str) -> tuple[float, float]:
    with wave.open(path, "rb") as w:
        rate = w.getframerate()
        raw = w.readframes(w.getnframes())
    a = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    rms = float(np.sqrt(np.mean(a * a))) if a.size else 0.0
    return rms, (a.size / rate if rate else 0.0)


def prepare(audio, sr: int):
    """Mirror of routers/voice_tts._prepare_audio_for_moonshine (identity at 16 kHz,
    linear-interp resample otherwise) without importing the service."""
    if sr == 16000:
        return audio, sr
    a = np.asarray(audio, dtype=np.float32)
    n_out = max(1, int(round(a.shape[0] * 16000 / sr)))
    return np.interp(np.linspace(0.0, a.shape[0] - 1, n_out),
                     np.arange(a.shape[0], dtype=np.float64), a).astype(np.float32).tolist(), 16000


def mem_available_mb() -> int:
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
    except OSError:
        pass
    return -1


def run_arm(threshold: str, clips: list[str], strip_wake) -> list[dict[str, Any]]:
    import moonshine_voice as mv
    from moonshine_voice.transcriber import Transcriber
    from moonshine_voice.utils import load_wav_file

    arch = getattr(mv.ModelArch, os.environ.get("ZOE_MOONSHINE_ARCH", "MEDIUM_STREAMING"))
    model_path, resolved = mv.get_model_for_language("en", arch)
    options = None if threshold == "default" else {"vad_threshold": threshold}
    tr = Transcriber(model_path, resolved, options=options)
    rows = []
    try:
        for path in clips:
            audio, sr = load_wav_file(path)
            audio, sr = prepare(audio, sr)
            w0, c0 = time.perf_counter(), time.process_time()
            out = tr.transcribe_without_streaming(audio, sr)
            wall_ms = (time.perf_counter() - w0) * 1000.0
            cpu_ms = (time.process_time() - c0) * 1000.0
            lines = [getattr(ln, "text", "") or "" for ln in getattr(out, "lines", [])]
            text = strip_wake(lines) if lines else ""
            if not text:
                flat = getattr(out, "text", None)
                if isinstance(flat, str) and flat.strip():
                    text = strip_wake([flat])
            rows.append({"clip": os.path.basename(path), "empty": not (text or "").strip(),
                         "words": len(_normalise(text).split()), "category": categorise(text),
                         "hash": text_hash(text), "wall_ms": round(wall_ms, 1),
                         "cpu_ms": round(cpu_ms, 1)})
            del text, lines, out
    finally:
        close = getattr(tr, "close", None)
        if callable(close):
            close()
        del tr
        gc.collect()
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus", type=Path, default=CORPUS)
    ap.add_argument("--last", type=int, default=20, help="newest N clips (the replay gate's slice)")
    ap.add_argument("--thresholds", default="default,0.5,0.3,0.0",
                    help="comma-separated arms; the FIRST is the baseline ('default' = no options)")
    ap.add_argument("--repeats", type=int, default=3,
                    help="runs per arm (a fresh model load each): the run-to-run noise floor")
    ap.add_argument("--min-free-mb", type=int, default=900,
                    help="refuse to load a model below this MemAvailable (the live brain shares RAM)")
    ap.add_argument("--json", type=Path)
    args = ap.parse_args()

    sys.path.insert(0, str(SERVICE))
    from stt_wake_strip import _strip_wake_word  # the service's own wake-word strip

    clips = select_newest(args.corpus, args.last)
    names = [os.path.basename(p) for p in clips]
    levels = {os.path.basename(p): clip_level(p) for p in clips}
    thresholds = [t.strip() for t in args.thresholds.split(",") if t.strip()]
    # arms[thr] = one row-list per repeat. Repeats are the NOISE FLOOR: the engine
    # is not bit-deterministic across loads on this box, so a single run cannot
    # tell a threshold effect from run-to-run drift.
    arms: dict[str, list[list[dict[str, Any]]]] = {t: [] for t in thresholds}
    for rep in range(args.repeats):
        for thr in thresholds:
            free = mem_available_mb()
            if 0 <= free < args.min_free_mb:
                print(f"MemAvailable {free} MB < {args.min_free_mb} MB — stopping before arm {thr}",
                      file=sys.stderr)
                break
            rows = run_arm(thr, clips, _strip_wake_word)
            arms[thr].append(rows)
            print(f"rep {rep + 1} arm vad_threshold={thr}: {sum(r['empty'] for r in rows)}/{len(clips)} empty",
                  file=sys.stderr)

    def empties(thr: str) -> list[set]:
        return [{r["clip"] for r in rows if r["empty"]} for rows in arms[thr]]

    base_thr = thresholds[0]
    base_sets = empties(base_thr)
    base_any = set().union(*base_sets) if base_sets else set()
    base_all = set.intersection(*base_sets) if base_sets else set()
    base_ref = {r["clip"]: r for r in arms[base_thr][0]} if arms[base_thr] else {}
    summary = {}
    for thr in thresholds:
        reps = arms[thr]
        if not reps:
            continue
        per_rep_nonempty = []
        cats: dict[str, int] = {}
        changed = []
        for rows in reps:
            by = {r["clip"]: r for r in rows}
            rec = [by[c] for c in sorted(base_all) if not by[c]["empty"]]
            per_rep_nonempty.append(len(rec))
            for r in rec:
                cats[r["category"]] = cats.get(r["category"], 0) + 1
            changed.append(sum(1 for c, r in base_ref.items()
                               if not r["empty"] and not by[c]["empty"] and by[c]["hash"] != r["hash"]))
        all_rows = [r for rows in reps for r in rows]
        summary[thr] = {
            "empty_per_rep": [sum(r["empty"] for r in rows) for rows in reps],
            "always_empty_baseline_now_nonempty_per_rep": per_rep_nonempty,
            "recovered_categories_total": cats,
            "ok_text_changed_vs_baseline_rep1_per_rep": changed,
            "cpu_ms_median": round(statistics.median(r["cpu_ms"] for r in all_rows), 1),
            "wall_ms_median": round(statistics.median(r["wall_ms"] for r in all_rows), 1),
            "cpu_ms_median_on_baseline_empty": (
                round(statistics.median(r["cpu_ms"] for r in all_rows if r["clip"] in base_all), 1)
                if base_all else None),
        }

    print(f"\nMoonshine vad_threshold probe — newest {len(clips)} clips x {args.repeats} repeats; "
          f"baseline arm '{base_thr}': empty in EVERY repeat {len(base_all)}, in ANY repeat {len(base_any)}")
    print(f"  {'arm':>8} {'empty/rep':>12} {'always-EMPTY→text/rep':>22} {'recovered categories':<44} "
          f"{'OK text changed/rep':>20} {'cpu_ms med':>10} {'cpu_ms EMPTY':>12}")
    for thr, sm in summary.items():
        print(f"  {thr:>8} {str(sm['empty_per_rep']):>12} {str(sm['always_empty_baseline_now_nonempty_per_rep']):>22} "
              f"{json.dumps(sm['recovered_categories_total']):<44} "
              f"{str(sm['ok_text_changed_vs_baseline_rep1_per_rep']):>20} {sm['cpu_ms_median']:>10} "
              f"{str(sm['cpu_ms_median_on_baseline_empty']):>12}")
    print("\n  per baseline-EMPTY clip (empty in any baseline repeat): rms dBFS, duration, then per arm the")
    print("  category of each repeat (e=empty, c=plausible_command, u=unclear, h=likely_hallucination, s=short_reply):")
    code = {"empty": "e", "plausible_command": "c", "unclear": "u", "likely_hallucination": "h", "short_reply": "s"}
    for c in sorted(base_any):
        rms, dur = levels[c]
        db = 20 * np.log10(rms) if rms > 0 else float("-inf")
        cells = "  ".join(
            f"{thr}:" + "".join(code[rows[names.index(c)]["category"]] for rows in arms[thr])
            for thr in thresholds if arms[thr])
        print(f"    {c}  {db:6.1f} dBFS  {dur:4.1f}s  {cells}")
    if args.json:
        args.json.write_text(json.dumps({"clips": names,
                                         "levels": levels, "arms": arms, "summary": summary},
                                        indent=2), encoding="utf-8")
        print(f"\nWrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
