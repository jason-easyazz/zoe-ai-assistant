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

One caveat when measuring the deep-quiet fast tail (--tail-flag-ms): the inserted
gaps are digital zeros, which Silero scores ~0.02 — maximally "deep". Real
mid-utterance pauses score higher (corpus 2026-07-27: internal-pause prob p90
median 0.179 vs 0.062 for true end-of-turn silence), so the false-cut table is a
WORST CASE for the deep-gated mode: it shows the fast tail as if every real pause
were as silent as a wire with no mic on it. The `natural_cuts` counter (closures
during a real utterance's own internal pauses, no synthetic gap involved) is the
realistic complement.

IT TESTS THE SHIPPED FILE. `scripts/setup/zoe_voice_daemon.py` imports pyaudio at
module scope and cannot be imported off the Pi, so this loads the real source with the
Pi-only modules stubbed. That is deliberate: a harness that tests a COPY of the
endpointer proves nothing about the one that runs.

Examples:
    python3 scripts/perf/measure_endpointing.py                    # default sweep
    python3 scripts/perf/measure_endpointing.py --samples 40 --json out.json
    # negative control — prove the probe can go red:
    python3 scripts/perf/measure_endpointing.py --vad-silence-s 0.1
    # before/after for the deep-quiet fast tail (ZOE_VAD_TAIL_MS):
    python3 scripts/perf/measure_endpointing.py --tail-flag-ms 640
    # B1.1 speculative turn-start: cancel rate + saving at each first-verdict tail,
    # against the live 640 ms close, with an optional Smart Turn veto arm:
    python3 scripts/perf/measure_endpointing.py --samples 2000 --tail-flag-ms 640 \
        --speculative-ms 320,400,480,560,640 --smart-turn-veto 0.5
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


def _stub_torchaudio_if_missing() -> None:
    """Let the REAL Silero model load on boxes without torchaudio (the Jetson).

    torch.hub refuses to load silero-vad unless `torchaudio` is importable, but
    the endpointer only ever calls `model(tensor, sr)` — torchaudio is used solely
    by silero's read_audio/save_audio helpers, which this probe never touches
    (WAVs are read with the stdlib `wave` module). Without this, the probe on the
    Jetson silently measured the LEGACY amplitude path while claiming to probe the
    live VAD lane. The stub is installed ONLY when torchaudio is genuinely absent,
    so on the Pi (where it exists) the real module is untouched, and it persists
    for the whole process because Silero loads lazily, after load_daemon returns.
    """
    import importlib.machinery
    import importlib.util
    try:
        if importlib.util.find_spec("torchaudio") is not None:
            return
    except (ImportError, ValueError):
        pass
    stub = types.ModuleType("torchaudio")
    # A real ModuleSpec is required: torch.hub's dependency check calls
    # find_spec(), which raises on a module whose __spec__ is None.
    stub.__spec__ = importlib.machinery.ModuleSpec("torchaudio", loader=None)
    stub.__version__ = "0.0.0-probe-stub"
    sys.modules["torchaudio"] = stub


def load_daemon(source: Path = DAEMON) -> types.ModuleType:
    """Execute the real daemon source with Pi-only imports stubbed."""
    _stub_torchaudio_if_missing()
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
    natural_cuts = 0
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
        if closed < len(utt):
            # Closed DURING the utterance: a real command's own internal pause
            # tripped the endpointer, with no synthetic gap involved. This is a
            # said-vs-did failure, not a tail value — the old max(...,0) folded
            # it into `tails` as a flattering 0ms, hiding exactly the regression
            # a shorter tail would introduce. Count it separately.
            natural_cuts += 1
            continue
        # Wait measured from the END OF SPEECH, which is known by construction.
        tails.append(1000.0 * (closed - len(utt)) / rate)

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
        "natural_cuts": natural_cuts,
        "tail_scored": len(tails),
        "tail_ms": {
            "median": round(statistics.median(tails), 1) if tails else None,
            "p90": round(sorted(tails)[int(0.9 * (len(tails) - 1))], 1) if tails else None,
            "max": round(max(tails), 1) if tails else None,
        },
        "false_cut": false_cuts,
    }


def load_smart_turn(threads: int, model_path: str | None = None):
    """The REAL zoe-data Smart Turn scorer (numpy log-mel + ORT CPU), loaded by
    path so the probe needs neither the service's import graph nor torch.
    Returns None (with a message) when the model or onnxruntime is missing."""
    import os
    os.environ["ZOE_SMART_TURN_THREADS"] = str(max(1, min(4, threads)))
    if model_path:
        os.environ["ZOE_SMART_TURN_MODEL"] = model_path
    src = REPO / "services" / "zoe-data" / "voice_turn.py"
    spec = importlib.util.spec_from_file_location("_zoe_voice_turn_probe", src)
    if not spec or not spec.loader:
        return None
    vt = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(vt)
    det = vt.get_smart_turn()
    if det is None:
        print("Smart Turn unavailable (model file or onnxruntime missing) — veto arm skipped",
              file=sys.stderr)
    return det


def _record_vad_probs(mod: types.ModuleType) -> list[float]:
    """Wrap the daemon's ``_vad_prob`` so the probe sees each chunk's probability
    WITHOUT a second Silero call (Silero is stateful: scoring a chunk twice would
    corrupt the very state under test). Returns the list the wrapper appends to."""
    seen: list[float] = []
    inner = getattr(mod, "_vad_prob_unwrapped", None) or mod._vad_prob
    mod._vad_prob_unwrapped = inner

    def _wrapped(model, chunk_int16, sample_rate: int = 16000) -> float:
        p = inner(model, chunk_int16, sample_rate)
        seen.append(p)
        return p

    mod._vad_prob = _wrapped
    return seen


def speculate_stream(mod: types.ModuleType, stream: np.ndarray, chunk: int, probs: list[float],
                     smart_turn=None, veto_p: float | None = None) -> dict[str, Any]:
    """Run ONE recording through a fresh ``_Endpointer`` exactly as
    ``record_command`` drives it (push, then ``speculative_ready`` when push did
    not close; ``n_frames`` is the CHUNK count, as the daemon passes it).

    Smart Turn veto arm (simulated — the daemon has no veto today): at a fire
    point Smart Turn scores the audio so far; below ``veto_p`` the fire is
    withdrawn and may only re-arm after the deep-quiet run is broken (a new
    pause). A vetoed terminal pause therefore means NO speculation for that turn
    (saving 0), never a later guess inside the same silence.
    """
    reset_vad_state(mod)
    ep = mod._Endpointer()
    fire = close = None
    speech_after_fire = False
    latched = False
    vetoes = 0
    st_ms: list[float] = []
    n_chunks = 0
    for start in range(0, len(stream) - chunk + 1, chunk):
        n_chunks += 1
        del probs[:]
        closed = ep.push(stream[start:start + chunk].tobytes(), n_chunks)
        if fire is not None and probs and probs[-1] >= mod.VAD_ENDPOINT_THRESHOLD:
            speech_after_fire = True
        if closed:
            close = start + chunk
            break
        if fire is not None:
            continue
        if latched:
            if ep._deep_quiet == 0:
                latched = False
            continue
        if ep.speculative_ready(n_chunks):
            idx = start + chunk
            if smart_turn is not None and veto_p is not None:
                import time as _time
                t0 = _time.perf_counter()
                p = smart_turn.end_of_turn_prob(stream[:idx])
                st_ms.append((_time.perf_counter() - t0) * 1000.0)
                if p < veto_p:
                    ep.speculation_fired = False
                    latched = True
                    vetoes += 1
                    continue
            fire = idx
    return {"fire": fire, "close": close, "speech_after_fire": speech_after_fire,
            "resumed": bool(ep.resumed_after_speculation), "vetoes": vetoes, "st_ms": st_ms}


def measure_speculation(mod: types.ModuleType, recordings: list[tuple[np.ndarray, int]],
                        spec_ms: int, live_tail_ms: int, tail_ms: int, rate: int, chunk: int,
                        smart_turn=None, veto_p: float | None = None) -> dict[str, Any]:
    """B1.1 offline cancel-rate estimate at one first-verdict tail.

    Each recording is the UNTRIMMED corpus clip (so its real end-of-turn room
    silence is scored, not a digital-zero stand-in) plus ``tail_ms`` of zeros so
    every stream closes. Outcomes, per the daemon's own verdict rules:

      commit        fired, nothing but deep quiet followed  → held audio released
      resolve_quiet fired, a non-deep chunk followed but no chunk crossed the
                    speech threshold → ``resolve``; almost surely transcript-
                    equivalent (released after one extra STT pass)
      cancel        fired, then a chunk crossed the speech threshold → the user
                    was still talking; ``resolve`` → non-equivalent → cancel
      no_fire       the first verdict never came before the close

    ``cancel_rate`` = cancel / (commit + resolve_quiet + cancel) — the doc's gate
    ratio; ``cancel_rate_conservative`` also counts every resolve_quiet as a cancel.
    Saving = close − fire on released turns (minus the Smart Turn scoring time on
    the veto arm, since the daemon would pay it before firing).
    ``live_cuts`` = the LIVE endpointer itself closed before the clip's last speech
    (a pre-existing cut speculation neither causes nor fixes).
    """
    mod.ZOE_SPECULATIVE_TURN = True
    mod.ZOE_SPECULATIVE_TAIL_MS = spec_ms
    mod.ZOE_VAD_TAIL_MS = live_tail_ms
    probs = _record_vad_probs(mod)
    counts = {"commit": 0, "resolve_quiet": 0, "cancel": 0, "no_fire": 0, "never_closed": 0}
    savings: list[float] = []
    live_cuts = vetoes = 0
    st_all: list[float] = []
    for audio, speech_end in recordings:
        stream = np.concatenate([audio, silence(tail_ms, rate)])
        r = speculate_stream(mod, stream, chunk, probs, smart_turn, veto_p)
        vetoes += r["vetoes"]
        st_all.extend(r["st_ms"])
        if r["close"] is None:
            counts["never_closed"] += 1
            continue
        if r["close"] < speech_end:
            live_cuts += 1
        if r["fire"] is None:
            counts["no_fire"] += 1
            continue
        if r["speech_after_fire"]:
            counts["cancel"] += 1
            continue
        counts["resolve_quiet" if r["resumed"] else "commit"] += 1
        st_cost = r["st_ms"][-1] if r["st_ms"] else 0.0
        savings.append(1000.0 * (r["close"] - r["fire"]) / rate - st_cost)
    decided = counts["commit"] + counts["resolve_quiet"] + counts["cancel"]
    n = len(recordings)
    return {
        "speculative_ms": spec_ms, "live_tail_ms": live_tail_ms,
        "smart_turn_veto": veto_p, "n": n, **counts,
        "fired": decided,
        "cancel_rate": round(counts["cancel"] / decided, 4) if decided else None,
        "cancel_rate_conservative": (round((counts["cancel"] + counts["resolve_quiet"]) / decided, 4)
                                     if decided else None),
        "median_saving_ms": round(statistics.median(savings), 1) if savings else None,
        "mean_saving_per_turn_ms": round(sum(savings) / n, 1) if n else None,
        "live_cuts": live_cuts, "vetoes": vetoes,
        "smart_turn_calls": len(st_all),
        "smart_turn_ms_median": round(statistics.median(st_all), 1) if st_all else None,
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
    ap.add_argument("--tail-flag-ms", type=int,
                    help="set ZOE_VAD_TAIL_MS — measure the deep-quiet fast tail (0 = flag off, "
                         "which must reproduce the flagless baseline exactly)")
    ap.add_argument("--tail-deep-prob", type=float,
                    help="set ZOE_VAD_TAIL_DEEP_PROB (deep-silence threshold for the fast tail)")
    ap.add_argument("--silence-timeout-s", type=float,
                    help="override SILENCE_TIMEOUT_S — the amplitude-mode knob (negative control)")
    ap.add_argument("--amplitude-mode", action="store_true",
                    help="measure the LEGACY amplitude endpointer instead of the live VAD one")
    ap.add_argument("--speculative-ms",
                    help="B1.1: comma-separated first-verdict tails (ZOE_SPECULATIVE_TAIL_MS) to sweep; "
                         "measures cancel rate + saving against the --tail-flag-ms close (default 640, "
                         "the live panel value) on untrimmed recordings, and skips the tail/false-cut table")
    ap.add_argument("--smart-turn-veto",
                    help="with --speculative-ms: comma-separated Smart Turn thresholds for a simulated "
                         "veto arm (fire only if P(complete) >= p)")
    ap.add_argument("--smart-turn-threads", type=int, default=4,
                    help="ORT intra-op threads for the veto arm (clamped to 1..4)")
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
    if args.tail_flag_ms is not None:
        # Same patch-the-global route the env flag takes at daemon import, so the
        # probe exercises the identical code path the Pi will run with the flag set.
        mod.ZOE_VAD_TAIL_MS = args.tail_flag_ms
        print(f"ZOE_VAD_TAIL_MS -> {args.tail_flag_ms}ms")
    if args.tail_deep_prob is not None:
        mod.ZOE_VAD_TAIL_DEEP_PROB = args.tail_deep_prob
        print(f"ZOE_VAD_TAIL_DEEP_PROB -> {args.tail_deep_prob}")
        # The instrument DELIBERATELY bypasses the daemon's clamp — negative
        # controls need to measure configurations production refuses. But a
        # measurement of a config prod would clamp must SAY so, or the number
        # gets shipped as if it were reachable (Codex, #1573).
        if not (0.0 < args.tail_deep_prob < mod.VAD_ENDPOINT_THRESHOLD):
            print(f"WARNING: {args.tail_deep_prob} is outside (0, "
                  f"{mod.VAD_ENDPOINT_THRESHOLD}) — the DAEMON WOULD CLAMP this "
                  f"to its default; this measurement is a negative control, not "
                  f"a deployable configuration.")

    _is_speech = speech_predicate(mod, chunk)
    if args.speculative_ms:
        return run_speculation_sweep(args, mod, rate, chunk, _is_speech)
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
          + (f", NEVER CLOSED {result['never_closed']}" if result["never_closed"] else "")
          + (f", NATURAL CUTS {result['natural_cuts']}" if result["natural_cuts"] else "") + ")")
    print("  false cuts on a mid-utterance pause (lower is better):")
    for gap, row in result["false_cut"].items():
        pct = "n/a" if row["rate"] is None else f"{row['rate']:.0%}"
        print(f"    pause {gap:>6}: {row['cuts']}/{row['considered']}  ({pct})")

    if args.json:
        args.json.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(f"\nWrote {args.json}")
    return 0


def run_speculation_sweep(args, mod: types.ModuleType, rate: int, chunk: int, is_speech) -> int:
    live_tail = args.tail_flag_ms if args.tail_flag_ms is not None else 640
    spec_list = [int(x) for x in args.speculative_ms.split(",") if x.strip()]
    vetoes = [None] + [float(x) for x in (args.smart_turn_veto or "").split(",") if x.strip()]
    smart_turn = load_smart_turn(args.smart_turn_threads) if len(vetoes) > 1 else None
    if smart_turn is None:
        vetoes = [None]
    files = sorted(args.corpus.glob("*.wav"))[-args.samples:]
    recordings: list[tuple[np.ndarray, int]] = []
    for path in files:
        audio = read_wav_mono16(path, rate)
        if audio is None or len(audio) < 4 * chunk:
            continue
        speech_end = len(trim_to_speech_end(audio, chunk, is_speech))
        reset_vad_state(mod)
        recordings.append((audio, speech_end))
    if not recordings:
        print(f"no usable {rate}Hz mono16 samples in {args.corpus}", file=sys.stderr)
        return 2
    if mod._Endpointer().mode != "vad":
        print("*** Silero unavailable — the speculative hook never fires in amplitude mode; "
              "refusing to report a fiction.", file=sys.stderr)
        return 2
    rows = []
    print(f"\nB1.1 speculative turn-start — n={len(recordings)} recordings, live close "
          f"ZOE_VAD_TAIL_MS={live_tail} ms (rate={rate} chunk={chunk})")
    print(f"  {'spec_ms':>7} {'veto':>5} {'fired':>5} {'commit':>6} {'res_q':>5} {'cancel':>6} "
          f"{'cancel%':>7} {'cons%':>6} {'med_save':>8} {'mean/turn':>9} {'vetoes':>6} {'st_ms':>6}")
    for veto in vetoes:
        for spec in spec_list:
            row = measure_speculation(mod, recordings, spec, live_tail, args.tail_ms, rate, chunk,
                                      smart_turn if veto is not None else None, veto)
            rows.append(row)
            pct = lambda v: "n/a" if v is None else f"{v:.1%}"  # noqa: E731
            print(f"  {spec:>7} {('-' if veto is None else veto):>5} {row['fired']:>5} "
                  f"{row['commit']:>6} {row['resolve_quiet']:>5} {row['cancel']:>6} "
                  f"{pct(row['cancel_rate']):>7} {pct(row['cancel_rate_conservative']):>6} "
                  f"{str(row['median_saving_ms']):>8} {str(row['mean_saving_per_turn_ms']):>9} "
                  f"{row['vetoes']:>6} {str(row['smart_turn_ms_median'] or '-'):>6}")
    print(f"  live-endpointer cuts before the clip's last speech (pre-existing, not caused by "
          f"speculation): {rows[0]['live_cuts']}/{rows[0]['n']}")
    if args.json:
        args.json.write_text(json.dumps({"mode": "speculation", "rows": rows}, indent=2), encoding="utf-8")
        print(f"\nWrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
