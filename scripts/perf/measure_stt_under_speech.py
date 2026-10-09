#!/usr/bin/env python3
"""STT-under-speech probe: how much Moonshine work can hide behind the user's own speech?

Little Gemma (arXiv, sec 4.2) hides prefill behind dictation. For Zoe the numbers say brain prefill is ~free
(prefix cache: median 1 new token) and the movable post-speech cost is the batch STT of the whole clip
(0.56-0.88 s). This probe measures the one lever that could move it: feeding the SAME in-process Moonshine
(MEDIUM_STREAMING, same options as the service) with audio chunks WHILE the clip "is being recorded", and timing what
is left after the last chunk. Three arms, every clip in every arm, order rotated:

  batch   transcribe_without_streaming(whole clip)          -> what the service does today (post-speech cost)
  sess    the SHIPPED voice_stt_stream.SttStreamSession fed PCM16 batches of 320 ms paced to wall-clock (what the
          daemon uploads) -> post-speech cost = time from the end of the clip to the finished transcript
  burst   the same stream fed with no pacing                -> NEGATIVE CONTROL: nothing is hidden, so the "after
                                                               last chunk" cost must come back to batch scale

PRIVACY: real household audio. Nothing printed or written is transcript text; only hashes, word counts, timings.
One model is loaded (~300 MB); refuses below --min-mem-mb MemAvailable. Run under the shared harness lock:

    flock -w 1800 /tmp/zoe-voice-harness.lock nice -n 5 ~/.zoe/venvs/zoe-data-py312/bin/python \\
        scripts/perf/measure_stt_under_speech.py --n 14 --json out.json
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import re
import statistics
import threading
import sys
import time
import wave
from pathlib import Path

import numpy as np

CORPUS = Path.home() / ".zoe-voice-samples"
SERVICE = Path(__file__).resolve().parents[2] / "services" / "zoe-data"
sys.path.insert(0, str(SERVICE))
BATCH_S = 0.32  # the daemon uploads 4 capture chunks (4 x 80 ms) per POST
ARMS = ("batch", "sess", "burst")


def mem_available_mb() -> int:
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) // 1024
    return -1


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s']", " ", (text or "").lower())).strip()


def thash(text: str) -> str:
    n = norm(text)
    return hashlib.sha1(n.encode()).hexdigest()[:8] if n else ""


def word_edits(a: str, b: str) -> int:
    """Word-level Levenshtein distance between two normalised transcripts (counts only, no text kept)."""
    x, y = norm(a).split(), norm(b).split()
    prev = list(range(len(y) + 1))
    for i, wx in enumerate(x, 1):
        cur = [i]
        for j, wy in enumerate(y, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (wx != wy)))
        prev = cur
    return prev[-1]


def lines_text(tr) -> str:
    return " ".join(getattr(ln, "text", "") or "" for ln in getattr(tr, "lines", []))


def run_batch(model, audio) -> tuple[float, str]:
    t = time.monotonic()
    out = model.transcribe_without_streaming(audio, 16000)
    return time.monotonic() - t, lines_text(out)


def run_session(model, audio, paced: bool) -> dict:
    """Drive the SHIPPED ``voice_stt_stream.SttStreamSession`` the way the daemon does: PCM16 batches of BATCH_S, each
    fed once it has been spoken (paced) or all at once at the end (burst = the negative control). ``after_last_s`` is
    the time from the end of the clip to the finished transcript."""
    import numpy as np
    import voice_stt_stream as ss

    def make():
        st = model.create_stream()
        st.start()
        return st

    step = int(16000 * BATCH_S)
    sess = ss.SttStreamSession("a" * 16, make, threading.Lock())
    t0 = time.monotonic()
    seq = 0
    for i in range(0, len(audio), step):
        seg = audio[i:i + step]
        if paced:
            due = t0 + (i + len(seg)) / 16000
            now = time.monotonic()
            if due > now:
                time.sleep(due - now)
        pcm = np.rint(np.clip(np.asarray(seg, dtype=np.float32), -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
        sess.feed(seq, pcm)
        seq += 1
    last_arrival = (t0 + len(audio) / 16000) if paced else t0  # burst: ALL work is post-speech
    lines, reason = sess.finish(len(audio), 30.0)
    done = time.monotonic()
    return {"after_last_s": done - last_arrival, "reason": reason, "text": " ".join(lines or [])}


def pick_clips(n: int, scan: int):
    from moonshine_voice.utils import load_wav_file
    rows = sorted((os.stat(p).st_mtime, os.path.basename(p), p) for p in glob.glob(str(CORPUS / "*.wav")))
    out = []
    for _, _, p in reversed(rows[-scan:]):
        try:
            with wave.open(p, "rb") as w:
                dur = w.getnframes() / float(w.getframerate())
                rate = w.getframerate()
        except Exception:
            continue
        if rate != 16000 or not 2.5 <= dur <= 9.0:
            continue
        audio, sr = load_wav_file(p)
        out.append((p, list(audio)))
        if len(out) >= n * 2:
            break
    return out


def q(vals, p):
    s = sorted(vals)
    return s[min(len(s) - 1, int(round(p * (len(s) - 1))))] if s else float("nan")


def summ(vals):
    return {"n": len(vals), "median": round(statistics.median(vals), 3) if vals else None,
            "p10": round(q(vals, .1), 3) if vals else None, "p90": round(q(vals, .9), 3) if vals else None}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=14)
    ap.add_argument("--scan", type=int, default=400)
    ap.add_argument("--min-mem-mb", type=int, default=900)
    ap.add_argument("--json")
    args = ap.parse_args()
    mem = mem_available_mb()
    if mem < args.min_mem_mb:
        print(f"REFUSED: MemAvailable {mem} MB < {args.min_mem_mb}", file=sys.stderr)
        return 2
    import moonshine_voice as mv
    from moonshine_voice.transcriber import Transcriber
    arch = getattr(mv.ModelArch, os.environ.get("ZOE_MOONSHINE_ARCH", "MEDIUM_STREAMING"))
    model_path, resolved = mv.get_model_for_language("en", arch)
    model = Transcriber(model_path, resolved)
    load0 = os.getloadavg()
    cands = pick_clips(args.n, args.scan)
    # warm-up (discarded): one batch + one stream, then keep clips whose batch transcript is non-empty
    run_batch(model, cands[0][1])
    run_session(model, cands[0][1], paced=False)
    clips = []
    for p, a in cands:
        if len(clips) >= args.n:
            break
        _, txt = run_batch(model, a)
        if len(norm(txt).split()) >= 2:
            clips.append((p, a, thash(txt)))
    rows = []
    for k, (p, a, bhash) in enumerate(clips):
        order = ARMS[k % 3:] + ARMS[:k % 3]
        row = {"clip": os.path.basename(p), "dur_s": round(len(a) / 16000, 2), "order": ">".join(order)}
        texts = {}
        for arm in order:
            if arm == "batch":
                s, txt = run_batch(model, a)
                row["batch_s"], row["batch_hash"] = round(s, 3), thash(txt)
                texts["batch"] = txt
                row["batch_words"] = len(norm(txt).split())
            else:
                r = run_session(model, a, paced=(arm == "sess"))
                row[f"{arm}_after_last_s"] = round(r["after_last_s"], 3)
                row[f"{arm}_reason"] = r["reason"]
                row[f"{arm}_hash"] = thash(r["text"])
                row[f"{arm}_words"] = len(norm(r["text"]).split())
                texts[arm] = r["text"]
        # noise floor: the SAME batch call repeated (the engine is not bit-stable across calls on this box)
        _, txt2 = run_batch(model, a)
        row["batch2_hash"] = thash(txt2)
        row["edits_batch_vs_batch2"] = word_edits(texts["batch"], txt2)
        row["edits_batch_vs_sess"] = word_edits(texts["batch"], texts["sess"])
        row["edits_batch_vs_burst"] = word_edits(texts["batch"], texts["burst"])
        rows.append(row)
        print(json.dumps(row), flush=True)
    res = {
        "n": len(rows), "mem_available_mb_start": mem, "mem_available_mb_end": mem_available_mb(),
        "loadavg_start": load0, "loadavg_end": os.getloadavg(), "batch_s_upload": BATCH_S,
        "batch_s": summ([r["batch_s"] for r in rows]),
        "sess_after_last_s": summ([r["sess_after_last_s"] for r in rows]),
        "burst_after_last_s": summ([r["burst_after_last_s"] for r in rows]),
        "sess_saving_s": summ([r["batch_s"] - r["sess_after_last_s"] for r in rows]),
        "sess_faster_than_batch": sum(r["sess_after_last_s"] < r["batch_s"] for r in rows),
        "burst_faster_than_batch": sum(r["burst_after_last_s"] < r["batch_s"] for r in rows),
        "sess_text_equal_batch": sum(r["sess_hash"] == r["batch_hash"] for r in rows),
        "burst_text_equal_batch": sum(r["burst_hash"] == r["batch_hash"] for r in rows),
        "batch2_text_equal_batch": sum(r["batch2_hash"] == r["batch_hash"] for r in rows),
        "word_edits_batch_vs_batch2": sum(r["edits_batch_vs_batch2"] for r in rows),
        "word_edits_batch_vs_sess": sum(r["edits_batch_vs_sess"] for r in rows),
        "word_edits_batch_vs_burst": sum(r["edits_batch_vs_burst"] for r in rows),
        "words_total_batch": sum(r["batch_words"] for r in rows),
        "rows": rows,
    }
    print(json.dumps({k: v for k, v in res.items() if k != "rows"}, indent=1))
    if args.json:
        Path(args.json).write_text(json.dumps(res, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
