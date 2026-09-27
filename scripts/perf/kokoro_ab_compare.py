#!/usr/bin/env python3
"""Kokoro sidecar A/B: latency, duration equality and a spectral quality proxy (B5.1).

Captures the SAME synthetic phrases from one Kokoro sidecar at a time, then compares
two captures (e.g. the live PyTorch sidecar on :10201 vs an ONNX candidate on :10202).
Capture-then-compare means the two engines never have to be resident together — on a
16 GB Orin already carrying the brain, two Kokoro loads may not fit. It reports:

* per-phrase synth latency (p50/p95) for each side,
* audio duration of each output and the B/A duration ratio,
* a quality PROXY: log-mel spectrogram cosine similarity (after aligning to the
  shorter output) and the RMS loudness delta in dB.

A proxy is not a MOS. The pair of WAVs for every phrase is written under
``--out`` (default ``~/.cache/zoe/kokoro-ab/``) so a human can listen A vs B.

It uses ``/synthesize_stream`` — raw S16_LE/24 kHz PCM of the whole utterance, with
NO phrase-cache read or write — so running it against the LIVE sidecar neither
pollutes ``~/.zoe/kokoro_cache`` nor measures a cache hit. Phrases are synthetic
(no household data). Run on the Jetson under the harness lock:

    P=scripts/perf/kokoro_ab_compare.py
    flock /tmp/zoe-voice-harness.lock python3 $P --capture http://127.0.0.1:10201 --tag pytorch
    flock /tmp/zoe-voice-harness.lock python3 $P --capture http://127.0.0.1:10202 --tag onnx
    python3 $P --compare pytorch onnx --json ab.json
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import time
import urllib.request
import wave
from pathlib import Path

SAMPLE_RATE = 24000

PHRASES = [
    "Sure, I can help with that.",
    "The kitchen lights are now off.",
    "It's twenty two degrees and sunny outside.",
    "Your timer for ten minutes has started.",
    "I've added milk, eggs and bread to the shopping list.",
    "Tomorrow looks cloudy with a chance of light rain in the afternoon.",
    "Playing some relaxing jazz in the living room.",
    "The front door is locked.",
    "Good morning! You have three things on your calendar today.",
    "I couldn't find that song, would you like me to try something similar?",
    "Setting the thermostat to twenty one degrees.",
    "The bus leaves in fifteen minutes, so you have time for a coffee.",
    "Here's a fun fact: octopuses have three hearts.",
    "Okay, I'll remind you to water the plants at six o'clock.",
    "The washing machine finished about five minutes ago.",
    "Volume turned down to thirty percent.",
    "Sorry, I didn't quite catch that. Could you say it again?",
    "Sunset today is at six forty two in the evening.",
    "I've paused the music. Just say resume when you're ready.",
    "All done! Is there anything else you'd like me to do?",
]


def synth(base: str, text: str, voice: str, timeout: float = 30.0) -> tuple[float, bytes]:
    body = json.dumps({"text": text, "voice": voice}).encode()
    req = urllib.request.Request(
        f"{base.rstrip('/')}/synthesize_stream", data=body,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    t = time.monotonic()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        pcm = resp.read()
    return (time.monotonic() - t) * 1000.0, pcm


def pcm_to_float(pcm: bytes):
    import numpy as np

    return np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0


def write_wav(path: Path, pcm: bytes) -> None:
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(pcm)


def log_mel(x, n_fft: int = 1024, hop: int = 256, n_mels: int = 64):
    """Minimal log-mel spectrogram (numpy only)."""
    import numpy as np

    if len(x) < n_fft:
        x = np.pad(x, (0, n_fft - len(x)))
    frames = 1 + (len(x) - n_fft) // hop
    idx = np.arange(n_fft)[None, :] + hop * np.arange(frames)[:, None]
    spec = np.abs(np.fft.rfft(x[idx] * np.hanning(n_fft), axis=1)) ** 2
    hz = np.linspace(0, SAMPLE_RATE / 2, spec.shape[1])
    mel = 2595 * np.log10(1 + hz / 700)
    edges = np.linspace(mel[0], mel[-1], n_mels + 2)
    fb = np.zeros((n_mels, spec.shape[1]))
    for m in range(n_mels):
        lo, c, hi = edges[m], edges[m + 1], edges[m + 2]
        fb[m] = np.clip(np.minimum((mel - lo) / (c - lo), (hi - mel) / (hi - c)), 0, None)
    return np.log(spec @ fb.T + 1e-10)


def spectral_similarity(a, b) -> float:
    """Cosine similarity of mean-removed log-mel spectrograms over the shared length.

    Outputs differ slightly in length, so frames are compared after linear time
    warping of B onto A's frame count — a proxy for 'same voice, same words',
    not an intelligibility or MOS score."""
    import numpy as np

    ma, mb = log_mel(a), log_mel(b)
    if len(mb) != len(ma):
        pos = np.linspace(0, len(mb) - 1, len(ma))
        mb = np.stack([np.interp(pos, np.arange(len(mb)), mb[:, k]) for k in range(mb.shape[1])], axis=1)
    va, vb = (ma - ma.mean()).ravel(), (mb - mb.mean()).ravel()
    return float(va @ vb / (np.linalg.norm(va) * np.linalg.norm(vb) + 1e-12))


def rms_db(x) -> float:
    import numpy as np

    return 20 * math.log10(float(np.sqrt(np.mean(x**2))) + 1e-9)


def pct(vals: list[float], p: float) -> float:
    s = sorted(vals)
    return s[min(len(s) - 1, max(0, int(round(p * (len(s) - 1)))))]


def capture(base: str, tag: str, voice: str, rounds: int, out: Path) -> dict:
    """Synthesize every phrase on one sidecar; write NN_<tag>.wav + <tag>.json."""
    with urllib.request.urlopen(f"{base.rstrip('/')}/health", timeout=5) as r:
        health = json.loads(r.read())
    print(f"{tag} {base}: {health}")
    synth(base, "Warming up.", voice)  # untimed: no first-call cost in the stats
    rows, lat = [], []
    for i, text in enumerate(PHRASES):
        times = []
        for _ in range(rounds):
            ms, pcm = synth(base, text, voice)
            times.append(ms)
        lat += times
        write_wav(out / f"{i:02d}_{tag}.wav", pcm)
        dur = len(pcm) / 2 / SAMPLE_RATE
        rows.append({"i": i, "text": text, "ms": [round(t, 1) for t in times], "dur_s": round(dur, 3)})
        print(f"{i:02d} {tag} {statistics.median(times):7.1f}ms  {dur:5.2f}s  {text[:48]!r}")
    audio = sum(r["dur_s"] for r in rows)
    rep = {"tag": tag, "url": base, "health": health, "n": len(lat),
           "p50_ms": round(statistics.median(lat), 1), "p95_ms": round(pct(lat, 0.95), 1),
           "max_ms": round(max(lat), 1), "rtf": round(sum(lat) / 1000 / audio / rounds, 3), "rows": rows}
    (out / f"{tag}.json").write_text(json.dumps(rep, indent=2))
    print(f"{tag}: p50 {rep['p50_ms']} ms  p95 {rep['p95_ms']} ms  RTF {rep['rtf']}")
    return rep


def compare(out: Path, tag_a: str, tag_b: str) -> dict:
    """Duration ratio + spectral similarity + loudness delta of captured WAV pairs."""
    rows = []
    for i, text in enumerate(PHRASES):
        pa, pb = out / f"{i:02d}_{tag_a}.wav", out / f"{i:02d}_{tag_b}.wav"
        if not (pa.exists() and pb.exists()):
            continue
        with wave.open(str(pa)) as wa, wave.open(str(pb)) as wb:
            a, b = pcm_to_float(wa.readframes(wa.getnframes())), pcm_to_float(wb.readframes(wb.getnframes()))
        rows.append({"i": i, "text": text, "a_dur_s": round(len(a) / SAMPLE_RATE, 3),
                     "b_dur_s": round(len(b) / SAMPLE_RATE, 3),
                     "dur_ratio": round(len(b) / max(1, len(a)), 4),
                     "spec_sim": round(spectral_similarity(a, b), 4),
                     "rms_delta_db": round(rms_db(b) - rms_db(a), 2)})
        r = rows[-1]
        print(f"{i:02d} {r['a_dur_s']:5.2f}s vs {r['b_dur_s']:5.2f}s ratio {r['dur_ratio']:.3f} "
              f"sim {r['spec_sim']:.3f} dRMS {r['rms_delta_db']:+.2f}dB  {text[:40]!r}")
    if not rows:
        raise SystemExit(f"no {tag_a}/{tag_b} WAV pairs in {out}")
    rep = {"a": tag_a, "b": tag_b, "n": len(rows),
           "dur_ratio_median": round(statistics.median(r["dur_ratio"] for r in rows), 4),
           "dur_ratio_min": min(r["dur_ratio"] for r in rows),
           "dur_ratio_max": max(r["dur_ratio"] for r in rows),
           "spec_sim_median": round(statistics.median(r["spec_sim"] for r in rows), 4),
           "spec_sim_min": min(r["spec_sim"] for r in rows),
           "rms_delta_db_median": round(statistics.median(r["rms_delta_db"] for r in rows), 2),
           "rows": rows}
    print(f"duration {tag_b}/{tag_a} median {rep['dur_ratio_median']} (min {rep['dur_ratio_min']}, "
          f"max {rep['dur_ratio_max']}); spectral sim median {rep['spec_sim_median']} "
          f"(min {rep['spec_sim_min']}); loudness delta median {rep['rms_delta_db_median']} dB")
    print(f"WAV pairs for a human listen: {out}/NN_{tag_a}.wav vs NN_{tag_b}.wav")
    return rep


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--capture", metavar="URL", help="synthesize all phrases on this sidecar")
    ap.add_argument("--tag", help="label for --capture output (e.g. A / B / pytorch / onnx)")
    ap.add_argument("--compare", nargs=2, metavar=("TAG_A", "TAG_B"), help="compare two captures")
    ap.add_argument("--voice", default="af_sky")
    ap.add_argument("--rounds", type=int, default=3, help="timed passes per phrase")
    ap.add_argument("--out", default=str(Path.home() / ".cache" / "zoe" / "kokoro-ab"))
    ap.add_argument("--json", help="write the compare report here")
    args = ap.parse_args()

    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    if args.capture:
        if not args.tag:
            ap.error("--capture needs --tag")
        capture(args.capture, args.tag, args.voice, args.rounds, out)
    if args.compare:
        rep = compare(out, *args.compare)
        for tag in args.compare:
            meta = out / f"{tag}.json"
            if meta.exists():
                rep[tag] = {k: v for k, v in json.loads(meta.read_text()).items() if k != "rows"}
        if args.json:
            Path(args.json).write_text(json.dumps(rep, indent=2))
    if not (args.capture or args.compare):
        ap.error("give --capture URL --tag T and/or --compare TAG_A TAG_B")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
