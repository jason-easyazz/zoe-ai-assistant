#!/usr/bin/env python3
"""Offline replay of the long-speech capture rules against saved panel recordings.

WHY THIS EXISTS
---------------
`scripts/perf/measure_voice.py` / the replay gate start from SAVED WAVs, so they cannot see
what the Pi daemon does BEFORE a WAV exists: when to stop recording. The 2026-10-12 panel
log shows four follow-up recordings stopped at the 12.32 s cap with the speaker mid-sentence
(`stop=max_duration`), a sentence cut at a trailing "and", and a 1.2 s leftover sent as its
own turn. The saved corpus holds exactly those recordings, so the capture rules can be
replayed offline - no network, no STT, no TTS, no brain, no Pi.

WHAT IT DOES
------------
Feeds each WAV, chunk by chunk, through the REAL `_Endpointer` + `_record_cap_stop` loaded
from `scripts/setup/zoe_voice_daemon.py` (same loader as measure_endpointing.py), and
reports what the new recorder would do at the point the old one stopped:

  * a clip that ends while the recorder is still open and the speaker still flowing is a
    capped recording the new rule CONTINUES (`would_continue=true`);
  * a clip the endpointer closes on silence is a control and must be unchanged.

It optionally classifies each clip's logged Moonshine transcript (the `STT_CAPTURE` lines
in ~/.zoe-logs, read locally) with the server's `ends_incomplete` / `addressedness`
rules and prints ONLY booleans plus the last word - never the transcript - so the output
is shareable.

`--vad energy` (default) needs no model: a chunk is speech when its mean |amplitude| is at
least --energy-floor. Cheap and RAM-free, but it is not Silero; treat its numbers as a
sanity check. `--vad silero` runs the live detector (loads torch + the Silero model:
run it under `flock /tmp/zoe-voice-harness.lock`, per AGENTS.md, and not while Kokoro is
loading).

    python3 scripts/perf/measure_long_speech_capture.py                 # the cited clips
    python3 scripts/perf/measure_long_speech_capture.py --vad silero a.wav b.wav
    python3 scripts/perf/measure_long_speech_capture.py --json out.json
    # negative control - the old flat cap must make every long clip "stop":
    python3 scripts/perf/measure_long_speech_capture.py --hard-s 12 --snap-s 0
"""
from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import re
import sys
import wave
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
CORPUS = Path.home() / ".zoe-voice-samples"
LOG_DIR = Path.home() / ".zoe-logs"

# 2026-10-12 evidence (see docs/knowledge/voice-pipeline.md -> Long speech is one turn).
DEFAULT_CLIPS = {
    "172020_735": "sentence cut at a trailing conjunction (6.4 s, closed on silence)",
    "183428_411": "12.32 s cap hit mid-sentence",
    "184728_290": "control: 5.28 s full sentence closed on silence",
    "184758_824": "12.32 s cap hit mid-sentence",
    "184827_246": "12.32 s cap hit mid-sentence",
    "184857_782": "12.32 s cap hit (speaker finishing)",
    "183433_104": "1.2 s leftover tail of a capped recording",
    "184910_027": "1.2 s leftover tail of a capped recording",
}


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def load_daemon():
    probe = _load(REPO / "scripts" / "perf" / "measure_endpointing.py", "_zoe_endpointing_probe")
    return probe.load_daemon()


def read_wav(path: Path, rate: int) -> np.ndarray:
    with wave.open(str(path), "rb") as wf:
        if (wf.getsampwidth(), wf.getnchannels(), wf.getframerate()) != (2, 1, rate):
            raise ValueError(f"{path.name}: need 16-bit mono {rate} Hz")
        return np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)


def logged_transcript(stem: str) -> str | None:
    """The Moonshine transcript the server logged when it captured this clip (local only)."""
    # The server writes the transcript with %r: single quotes normally, double quotes when it holds an apostrophe.
    rx = re.compile(r'STT_CAPTURE file=\S*/' + re.escape(stem)
                    + r'\.wav moonshine=(\'(?:[^\'\\]|\\.)*\'|"(?:[^"\\]|\\.)*")(?:\s|$)')
    if not LOG_DIR.is_dir():
        return None
    for log in sorted(LOG_DIR.glob("*.log")):
        try:
            for line in log.read_text(errors="replace").splitlines():
                m = rx.search(line)
                if m:
                    try:
                        return str(ast.literal_eval(m.group(1)))
                    except (ValueError, SyntaxError):
                        return m.group(1)[1:-1]
        except OSError:
            continue
    return None


def classify(text: str | None) -> dict:
    """Server-side verdicts on a transcript. Booleans and the last word only."""
    if not text:
        return {"transcript": "not found in ~/.zoe-logs"}
    sys.path.insert(0, str(REPO / "services" / "zoe-data"))
    import voice_utterance_shape as us
    words = re.findall(r"[^\W_]+(?:['’][^\W_]+)*", text)
    return {
        "held_by_trailing_continuation": us.ends_incomplete(text),
        "last_word": words[-1].lower() if words else "",
        "side_speech_in_followup": not us.addressedness(text, "followup").addressed,
        "side_speech_in_followup_strict": not us.addressedness(text, "followup", strict=True).addressed,
    }


def replay(mod, audio: np.ndarray, probs: list[float], caps) -> dict:
    """Feed the clip through the real endpointer + cap rule, like the recorder loop does."""
    seq = iter(probs)
    mod._vad_prob = lambda _model, _chunk: next(seq)
    mod.VAD_ENDPOINT_ENABLED = True
    mod._get_silero_vad = lambda: (object(), None)
    chunk = mod.CHUNK_SIZE
    ep = mod._Endpointer()
    n = 0
    for start in range(0, len(audio) - chunk + 1, chunk):
        n += 1
        if ep.push(audio[start:start + chunk].tobytes(), n):
            return {"stop": "silence", "tail": ep.tail_rule, "seconds": round(n * chunk / mod.SAMPLE_RATE, 2),
                    "would_continue": False}
        if mod._record_cap_stop(n, caps, ep):
            return {"stop": "max_duration", "seconds": round(n * chunk / mod.SAMPLE_RATE, 2),
                    "would_continue": False}
    if n < caps[0]:
        # The clip ends before the soft cap and the endpointer never closed it: the cap rule
        # has nothing to say (the saved WAV simply stops short of the live close).
        return {"stop": "clip_ends_before_soft_cap", "seconds": round(n * chunk / mod.SAMPLE_RATE, 2),
                "would_continue": None}
    return {"stop": "clip_ends_recorder_open", "seconds": round(n * chunk / mod.SAMPLE_RATE, 2),
            "still_speaking": ep.still_speaking(mod.RECORD_STILL_SPEAKING_RATIO),
            "mid_speech": ep.mid_speech(),
            # production's _record_cap_stop continues on EITHER signal (a pause-then-resume has low density)
            "would_continue": bool(ep.still_speaking(mod.RECORD_STILL_SPEAKING_RATIO) or ep.mid_speech())}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("wavs", nargs="*", help="WAV paths or corpus stems (default: the 2026-10-12 clips)")
    ap.add_argument("--vad", choices=("energy", "silero"), default="energy")
    ap.add_argument("--energy-floor", type=float, default=None,
                    help="mean |amp| that counts as speech (default: the daemon's RECORD_SILENCE_AMPLITUDE)")
    ap.add_argument("--hard-s", type=float, default=None, help="override the hard cap (negative control)")
    ap.add_argument("--snap-s", type=float, default=None, help="override the snap window (negative control)")
    ap.add_argument("--json", type=Path, default=None)
    args = ap.parse_args()

    mod = load_daemon()
    if args.hard_s is not None:
        mod.RECORD_SECONDS_HARD = max(mod.RECORD_SECONDS, int(args.hard_s))
    if args.snap_s is not None:
        mod.RECORD_SNAP_S = args.snap_s
    caps = mod._record_caps()

    names = args.wavs or list(DEFAULT_CLIPS)
    results = []
    silero = None
    real_prob = mod._vad_prob  # replay() swaps mod._vad_prob for a scripted one
    if args.vad == "silero":
        mod.VAD_ENDPOINT_ENABLED = True
        silero, _ = mod._get_silero_vad()
        if silero is None:
            print("silero unavailable - rerun with --vad energy", file=sys.stderr)
            return 2
    for name in names:
        path = Path(name)
        if not path.exists():
            path = CORPUS / (name if name.endswith(".wav") else f"{name}.wav")
        if not path.exists():
            results.append({"clip": name, "error": "not found"})
            continue
        audio = read_wav(path, mod.SAMPLE_RATE)
        chunk = mod.CHUNK_SIZE
        blocks = [audio[i:i + chunk] for i in range(0, len(audio) - chunk + 1, chunk)]
        if silero is not None:
            # One contiguous stream per clip: a fresh per-stream VAD (daemon >= 2026-10-19), or the
            # shared model reset (older daemon sources, measured with --daemon on an old copy).
            vad = mod._new_vad_stream() if hasattr(mod, "_new_vad_stream") else silero
            if vad is silero and hasattr(silero, "reset_states"):
                silero.reset_states()
            probs = [real_prob(vad, b) for b in blocks]
        else:
            floor = args.energy_floor if args.energy_floor is not None else mod.RECORD_SILENCE_AMPLITUDE
            probs = [0.9 if float(np.abs(b.astype(np.int32)).mean()) >= floor else 0.02
                     for b in blocks]
        row = {"clip": path.stem, "note": DEFAULT_CLIPS.get(path.stem, ""),
               "seconds": round(len(audio) / mod.SAMPLE_RATE, 2),
               "old_cap_s": mod.RECORD_SECONDS, "caps_s": [round(c * mod.CHUNK_SIZE / mod.SAMPLE_RATE, 2) for c in caps],
               "vad": args.vad, **replay(mod, audio, probs, caps), **classify(logged_transcript(path.stem))}
        results.append(row)
        print(json.dumps(row))
    if args.json:
        args.json.write_text(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
