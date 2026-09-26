#!/usr/bin/env python3
"""Cut a bridge WAV into one file per utterance on silence (stdlib only).

The replay path (``services/zoe-data/tests/replay_samples.py``) treats every WAV as
ONE utterance, so a 10-minute capture of 20 read sentences must be split first::

    python3 split_on_silence.py capture.wav --out segments/ --min-silence 0.8 --threshold 300

``threshold`` is peak absolute sample value (int16) below which a 20 ms window
counts as silence; ``--min-silence`` seconds of it ends an utterance. Prints the
segments it wrote so they can be checked against the sentence list (expect 20).

Re-running into the same ``--out`` (the protocol says to tune the settings and
re-run) first removes that capture's previous ``<stem>_NN.wav`` segments, so a run
that finds fewer utterances never leaves stale higher-numbered files behind for
the replay to transcribe.
"""
from __future__ import annotations

import argparse
import array
import re
import sys
import wave
from pathlib import Path

WINDOW_S = 0.02


def read_pcm16(path: Path) -> tuple[array.array, int]:
    with wave.open(str(path), "rb") as w:
        if w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise ValueError(f"{path}: need mono 16-bit PCM")
        rate = w.getframerate()
        pcm = array.array("h")
        pcm.frombytes(w.readframes(w.getnframes()))
    if sys.byteorder != "little":  # pragma: no cover
        pcm.byteswap()
    return pcm, rate


def find_segments(pcm: array.array, rate: int, *, threshold: int, min_silence_s: float,
                  min_speech_s: float, pad_s: float) -> list[tuple[int, int]]:
    """Return (start, end) sample ranges of non-silent stretches."""
    win = max(1, int(rate * WINDOW_S))
    loud = [max((abs(v) for v in pcm[i:i + win]), default=0) >= threshold for i in range(0, len(pcm), win)]
    min_sil_w = max(1, int(round(min_silence_s / WINDOW_S)))
    min_spe_w = max(1, int(round(min_speech_s / WINDOW_S)))
    pad = int(pad_s * rate)
    segments: list[tuple[int, int]] = []
    start = None
    quiet = 0
    for i, is_loud in enumerate(loud):
        if is_loud:
            if start is None:
                start = i
            quiet = 0
        elif start is not None:
            quiet += 1
            if quiet >= min_sil_w:
                end = i - quiet + 1
                if end - start >= min_spe_w:
                    segments.append((max(0, start * win - pad), min(len(pcm), end * win + pad)))
                start, quiet = None, 0
    if start is not None and len(loud) - quiet - start >= min_spe_w:
        segments.append((max(0, start * win - pad), len(pcm)))
    return segments


def clear_segments(out_dir: Path, stem: str) -> int:
    """Delete this capture's earlier ``<stem>_NN.wav`` segments; other files are untouched."""
    pattern = re.compile(rf"{re.escape(stem)}_\d+\.wav")
    stale = [p for p in out_dir.glob(f"{stem}_*.wav") if pattern.fullmatch(p.name)]
    for p in stale:
        p.unlink()
    return len(stale)


def write_segments(pcm: array.array, rate: int, segments: list[tuple[int, int]], out_dir: Path, stem: str) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    removed = clear_segments(out_dir, stem)
    if removed:
        print(f"removed {removed} segment(s) from a previous run of {stem}", file=sys.stderr)
    paths = []
    for n, (a, b) in enumerate(segments, 1):
        path = out_dir / f"{stem}_{n:02d}.wav"
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(pcm[a:b].tobytes())
        paths.append(path)
    return paths


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("wav", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--threshold", type=int, default=300, help="int16 peak below which a window is silence")
    ap.add_argument("--min-silence", type=float, default=0.8, help="seconds of silence that end an utterance")
    ap.add_argument("--min-speech", type=float, default=0.4, help="drop blips shorter than this")
    ap.add_argument("--pad", type=float, default=0.15, help="seconds kept either side of each utterance")
    args = ap.parse_args(argv)
    pcm, rate = read_pcm16(args.wav)
    segs = find_segments(pcm, rate, threshold=args.threshold, min_silence_s=args.min_silence,
                         min_speech_s=args.min_speech, pad_s=args.pad)
    for p, (a, b) in zip(write_segments(pcm, rate, segs, args.out, args.wav.stem), segs):
        print(f"{p}  {a / rate:7.2f}s – {b / rate:7.2f}s")
    print(f"{len(segs)} segment(s) → {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
