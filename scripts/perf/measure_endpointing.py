#!/usr/bin/env python3
"""Endpointing probe — the blind spot the replay gate cannot see.

WHY THIS EXISTS
---------------
`scripts/perf/measure_voice.py` (and the replay gate on top of it) replays SAVED
WAV files, so it measures `stt + resolve + brain` and starts AFTER the microphone.
Endpointing — deciding you have stopped talking — happens on the Pi, before any WAV
exists. It is therefore **completely outside the replay gate's boundary**: change the
endpointer, break turn-taking, and the gate still reports green.

Measured on the live panel lane 2026-07-26: the endpoint wait is 800ms
(`VAD_ENDPOINT_SILENCE_S`), against ~550ms STT and 66ms brain TTFT. It is the largest
controllable block before Zoe speaks, and nothing can currently see it. This probe is
the instrument that has to exist BEFORE anyone touches that path (e.g. porting Smart
Turn v3 from `voice_livekit.py` into the daemon's `_Endpointer`).

WHAT IT MEASURES
----------------
Two axes, both with ground truth by construction:

  * **tail** — how much silence the endpointer waits through after real speech ends.
    Lower is better; a human turn-takes at ~200ms.
  * **false cut** — whether it closes the turn during a MID-UTTERANCE pause. This is
    the cost side: any endpointer can be made fast by being trigger-happy, and a cut
    mid-sentence is a said-vs-did failure the replay corpus can never contain (those
    samples were captured post-endpointing, already trimmed).

Streams are built by concatenating real corpus utterances (~/.zoe-voice-samples) with
known silence gaps, so the correct answer is known exactly rather than annotated.

IT TESTS THE SHIPPED FILE. `scripts/setup/zoe_voice_daemon.py` imports pyaudio at
module scope and cannot be imported off the Pi, so this loads the real source with the
Pi-only modules stubbed. That is deliberate: a harness that tests a COPY of the
endpointer proves nothing about the one that runs.

Examples:
    python3 scripts/perf/measure_endpointing.py                    # default sweep
    python3 scripts/perf/measure_endpointing.py --samples 40 --json out.json
    # negative control — prove the probe can go red:
    python3 scripts/perf/measure_endpointing.py --vad-silence-s 0.1
"""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import json
import statistics
import sys
import types
import wave
from pathlib import Path
from typing import Any

import numpy as np

REPO = Path(__file__).resolve().parents[2]
DAEMON = REPO / "scripts" / "setup" / "zoe_voice_daemon.py"
CORPUS = Path.home() / ".zoe-voice-samples"

# Pi-only / side-effecting imports the endpointer itself never uses. Stubbed so the
# real module body can execute off-Pi. Anything the endpointer DOES need (numpy,
# torch/Silero) is deliberately NOT stubbed — stubbing those would make the probe
# measure a fiction.
_STUB_MODULES = ("pyaudio", "openwakeword", "openwakeword.model")


def load_daemon(source: Path = DAEMON) -> types.ModuleType:
    """Execute the real daemon source with Pi-only imports stubbed."""
    saved = {name: sys.modules.get(name) for name in _STUB_MODULES}
    for name in _STUB_MODULES:
        stub = types.ModuleType(name)
        stub.__getattr__ = lambda _attr: types.SimpleNamespace()  # type: ignore[attr-defined]
        sys.modules[name] = stub
    try:
        spec = importlib.util.spec_from_file_location("_zoe_daemon_probe", source)
        if not spec or not spec.loader:
            raise RuntimeError(f"cannot load {source}")
        module = importlib.util.module_from_spec(spec)
        sys.modules["_zoe_daemon_probe"] = module
        spec.loader.exec_module(module)
        return module
    finally:
        for name, prev in saved.items():
            if prev is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = prev


def read_wav_mono16(path: Path, want_rate: int) -> np.ndarray | None:
    """Return int16 mono samples at want_rate, or None if the file is unusable."""
    try:
        with contextlib.closing(wave.open(str(path), "rb")) as wf:
            if wf.getsampwidth() != 2 or wf.getnchannels() != 1:
                return None
            if wf.getframerate() != want_rate:
                return None          # no resampling: a resampler would be a second
                                     # thing under test, and the corpus is already 16k
            raw = wf.readframes(wf.getnframes())
    except Exception:
        return None
    audio = np.frombuffer(raw, dtype=np.int16)
    return audio if audio.size else None


def silence(ms: int, rate: int) -> np.ndarray:
    return np.zeros(int(rate * ms / 1000), dtype=np.int16)


def speech_predicate(mod: types.ModuleType, chunk: int):
    """Return `is_speech(block) -> bool` using the SAME detector as the mode under test.

    Ground truth has to be measured with the detector whose behaviour is being
    scored. Trimming by raw amplitude while the endpointer runs Silero shifts the
    boundary in both directions: quiet-but-VAD-detectable speech below the
    amplitude floor gets trimmed away (inflating the measured tail), and steady
    noise above the floor is kept as "speech" (hiding one). The trim must agree
    with the endpointer or the ms it reports are against the wrong zero.
    """
    if getattr(mod, "VAD_ENDPOINT_ENABLED", False):
        model, _ = mod._get_silero_vad()
        if model is not None:
            threshold = mod.VAD_ENDPOINT_THRESHOLD

            def _vad_is_speech(block: np.ndarray) -> bool:
                return mod._vad_prob(model, block) >= threshold

            return _vad_is_speech
    floor = mod.RECORD_SILENCE_AMPLITUDE

    def _amp_is_speech(block: np.ndarray) -> bool:
        return bool(np.abs(block.astype(np.int32)).mean() >= floor)

    return _amp_is_speech


def trim_to_speech_end(audio: np.ndarray, chunk: int, is_speech) -> np.ndarray:
    """Cut the trailing silence the ORIGINAL endpointer already waited through.

    Corpus WAVs are whole recordings: each one ends with the ~0.8-1.5s of silence
    that caused the live endpointer to close the turn. Treating end-of-file as
    end-of-speech therefore measures a tail of ~0ms — the probe would report the
    endpointer as instantaneous because the wait is baked into the fixture. Trim
    back to the last chunk the SAME detector calls speech (see speech_predicate).
    """
    last_voiced = -1
    for start in range(0, len(audio) - chunk + 1, chunk):
        if is_speech(audio[start:start + chunk]):
            last_voiced = start + chunk
    return audio[:last_voiced] if last_voiced > 0 else audio


def reset_vad_state(mod: types.ModuleType) -> None:
    """Clear Silero's hidden state between streams.

    Silero VAD is STATEFUL (an RNN), and `_get_silero_vad()` hands out ONE cached
    module that every `_Endpointer` shares. Without an explicit reset, whatever
    audio ran through it last — the trimming pass, or the previous sample —
    carries hidden state into the next measurement, so results would depend on
    fixture selection and ORDER rather than on the stream under test. A probe
    whose numbers move when you reorder the corpus is not measuring the
    endpointer.
    """
    try:
        model, _ = mod._get_silero_vad()
        if model is not None and hasattr(model, "reset_states"):
            model.reset_states()
    except Exception:
        pass


def run_stream(mod: types.ModuleType, audio: np.ndarray, chunk: int) -> int | None:
    """Feed audio through a fresh _Endpointer; return the closing sample index."""
    reset_vad_state(mod)
    ep = mod._Endpointer()
    n_frames = 0
    for start in range(0, len(audio) - chunk + 1, chunk):
        block = audio[start:start + chunk]
        n_frames += chunk
        if ep.push(block.tobytes(), n_frames):
            return start + chunk
    return None


def measure(mod: types.ModuleType, samples: list[np.ndarray], gaps_ms: list[int],
            tail_ms: int, rate: int, chunk: int) -> dict[str, Any]:
    tails: list[float] = []
    never_closed = 0
    for utt in samples:
        stream = np.concatenate([utt, silence(tail_ms, rate)])
        closed = run_stream(mod, stream, chunk)
        if closed is None:
            # The endpointer never closed within the appended silence. That is a
            # DISTINCT outcome, not a tail value — folding the utterance duration
            # into `tails` would silently corrupt the median with a number that
            # measures the fixture, not the endpointer (a long recording would
            # even look like a long wait). Count it and keep it out of the stat.
            never_closed += 1
            continue
        # Wait measured from the END OF SPEECH, which is known by construction.
        tails.append(1000.0 * max(closed - len(utt), 0) / rate)

    false_cuts: dict[str, dict[str, Any]] = {}
    for gap in gaps_ms:
        cuts = 0
        considered = 0
        for utt in samples:
            if len(utt) < 2 * chunk:
                continue
            mid = (len(utt) // 2 // chunk) * chunk
            stream = np.concatenate([utt[:mid], silence(gap, rate), utt[mid:], silence(tail_ms, rate)])
            resume = mid + len(silence(gap, rate))
            considered += 1
            closed = run_stream(mod, stream, chunk)
            # A cut anywhere before speech resumes is a mid-utterance cut: the user
            # paused for breath and Zoe stopped listening.
            if closed is not None and closed <= resume:
                cuts += 1
        false_cuts[f"{gap}ms"] = {
            "cuts": cuts, "considered": considered,
            "rate": round(cuts / considered, 3) if considered else None,
        }

    return {
        "mode": mod._Endpointer().mode,
        "n_samples": len(samples),
        # Surfaced, never hidden: a high never_closed count means the tail median
        # is computed over a SUBSET, and the reader has to know that.
        "never_closed": never_closed,
        "tail_scored": len(tails),
        "tail_ms": {
            "median": round(statistics.median(tails), 1) if tails else None,
            "p90": round(sorted(tails)[int(0.9 * (len(tails) - 1))], 1) if tails else None,
            "max": round(max(tails), 1) if tails else None,
        },
        "false_cut": false_cuts,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--samples", type=int, default=25, help="corpus utterances to use")
    ap.add_argument("--corpus", type=Path, default=CORPUS)
    ap.add_argument("--daemon", type=Path, default=DAEMON,
                    help="daemon source to measure (on zoe-pi: /home/pi/.zoe-voice/zoe_voice_daemon.py)")
    ap.add_argument("--gaps", default="200,400,600,800", help="mid-utterance pause lengths (ms)")
    ap.add_argument("--tail-ms", type=int, default=3000, help="trailing silence appended to each stream")
    ap.add_argument("--vad-silence-s", type=float, help="override VAD_ENDPOINT_SILENCE_S (negative control)")
    ap.add_argument("--silence-timeout-s", type=float,
                    help="override SILENCE_TIMEOUT_S — the amplitude-mode knob (negative control)")
    ap.add_argument("--amplitude-mode", action="store_true",
                    help="measure the LEGACY amplitude endpointer instead of the live VAD one")
    ap.add_argument("--json", type=Path, help="write results here")
    args = ap.parse_args()

    if not args.daemon.exists():
        print(f"daemon source missing: {args.daemon}", file=sys.stderr)
        return 2
    mod = load_daemon(args.daemon)
    rate, chunk = mod.SAMPLE_RATE, mod.CHUNK_SIZE

    # VAD_ENDPOINT_ENABLED defaults to FALSE in the daemon source, but the live panel
    # runs it ON (/home/pi/.zoe-voice/.env.voice: VAD_ENDPOINT_ENABLED=1). Taking the
    # code default here would silently measure the legacy amplitude path — the exact
    # read-a-flag-from-its-default trap this probe exists to avoid. Default to LIVE.
    mod.VAD_ENDPOINT_ENABLED = not args.amplitude_mode

    if args.silence_timeout_s is not None:
        mod.SILENCE_TIMEOUT_S = args.silence_timeout_s
        print(f"[negative control] SILENCE_TIMEOUT_S -> {args.silence_timeout_s}s")
    if args.vad_silence_s is not None:
        # Constants are read in _Endpointer.__init__, so patching the module global
        # before construction is enough — and keeps the probe honest about WHICH knob
        # it moved rather than editing the shipped file.
        mod.VAD_ENDPOINT_SILENCE_S = args.vad_silence_s
        print(f"[negative control] VAD_ENDPOINT_SILENCE_S -> {args.vad_silence_s}s")

    _is_speech = speech_predicate(mod, chunk)
    files = sorted(args.corpus.glob("*.wav"))[-args.samples * 4:]
    loaded: list[np.ndarray] = []
    for path in reversed(files):
        audio = read_wav_mono16(path, rate)
        if audio is not None:
            audio = trim_to_speech_end(audio, chunk, _is_speech)
            reset_vad_state(mod)
            if len(audio) >= 4 * chunk:
                loaded.append(audio)
        if len(loaded) >= args.samples:
            break
    if not loaded:
        print(f"no usable {rate}Hz mono16 samples in {args.corpus}", file=sys.stderr)
        return 2

    gaps = [int(g) for g in args.gaps.split(",") if g.strip()]
    result = measure(mod, loaded, gaps, args.tail_ms, rate, chunk)

    # Silero needs torchaudio, which exists in the Pi's venv but not on the Jetson.
    # _Endpointer falls back to amplitude mode SILENTLY, so without this the probe
    # would report tidy numbers for the legacy path while the caller believed it had
    # measured the live one. A fallback is not a measurement of the thing requested.
    result["requested_mode"] = "amplitude" if args.amplitude_mode else "vad"
    result["mode_as_requested"] = result["mode"] == result["requested_mode"]
    if not result["mode_as_requested"]:
        print(f"\n*** WARNING: asked for {result['requested_mode']} mode, measured "
              f"{result['mode']} — Silero is unavailable here (torchaudio missing?).\n"
              f"    These numbers describe the LEGACY path, not the live panel lane.\n"
              f"    Run this on zoe-pi with the daemon's venv to measure the real one.",
              file=sys.stderr)

    print(f"\nEndpointing probe — mode={result['mode']}  n={result['n_samples']}"
          f"  (rate={rate} chunk={chunk})")
    t = result["tail_ms"]
    print(f"  tail after speech ends : median={t['median']}ms  p90={t['p90']}ms  max={t['max']}ms"
          f"   (scored {result['tail_scored']}/{result['n_samples']}"
          + (f", NEVER CLOSED {result['never_closed']}" if result["never_closed"] else "") + ")")
    print("  false cuts on a mid-utterance pause (lower is better):")
    for gap, row in result["false_cut"].items():
        pct = "n/a" if row["rate"] is None else f"{row['rate']:.0%}"
        print(f"    pause {gap:>6}: {row['cuts']}/{row['considered']}  ({pct})")

    if args.json:
        args.json.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f"\nWrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
