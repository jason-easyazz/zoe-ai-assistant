#!/usr/bin/env python3
"""Smart Turn vs the panel's fixed silence tail, scored OFFLINE on the voice corpus.

WHY THIS EXISTS
---------------
Samantha Initiative lane E, experiment E1 (docs/research/samantha-initiative-2026-11-03.md):
before Smart Turn (pipecat smart-turn-v3.2-cpu.onnx, already used by the dormant LiveKit lane
through ``services/zoe-data/voice_turn.py``) goes anywhere near the Pi, measure whether it
ends turns better than the panel daemon's fixed tail on the owner's real voice. The two
owner pains are scored separately:

  * **cut-in** — the turn closes before the speaker finished (an early cut);
  * **lag**    — how long after the true end of speech the turn closes.

WHAT IT DOES, PER CLIP (endpointing starts after the wake pre-roll, as in record_command)
1. Ground truth end of speech = end of the last 32 ms hop the repo Silero VAD
   (voice_vad.py: ONNX v6 WITH the 64-sample context = the correct 576-sample input) scores
   >= --gt-threshold (0.35, the daemon's VAD_ENDPOINT_THRESHOLD).
2. The clip is extended with its OWN trailing room tone (digital zeros score maximally deep
   and would flatter the deep-quiet rule).
3. Hop probabilities are folded into the daemon's 80 ms chunks as _VadStream.feed does, then
   * fixed arms replay them through the REAL daemon _Endpointer (Pi-only imports stubbed,
     _vad_prob scripted) under the live config and variants;
   * Smart Turn arms: after S ms of quiet following speech the REAL voice_turn scorer reads
     the capture so far; >= T ends the turn, else re-check every R ms, close anyway at H ms.
     Scores are cached per check point, so every threshold reuses the same model calls.
4. Internal pauses > --long-pause-ms, and a synthetic room-tone pause inserted at the
   mid-utterance word boundary (--synthetic-gaps): the corpus was captured behind the live
   endpointer, so long real pauses are rare in it by construction.

5. EXTEND-ONLY arms (``--extend-thresholds``, lane E E1 follow-up): the live fixed tail still
   decides WHEN to consider closing; Smart Turn may only HOLD the turn open past that point
   (``extend_close`` drives the real endpointer and vetoes its closes). It can never close
   earlier than today, so it cannot cut more than today; it is charged its measured inference
   time per check. ``unfinished_trailer`` is the trailer lexicon ("and", "um", "so"...) as a
   pure text rule: the corpus has no transcripts and none are made, so the corpus run measures
   the model half only. Reproduce: ``--extend-only --extend-thresholds 0.3,0.5,0.7,0.85,0.95``.

Writes ONLY aggregate numbers (--work-dir: per-clip numbers, no names/audio/text; keep it
outside the repo). No transcription. Changes nothing live. Limits and results:
docs/knowledge/smart-turn-offline-eval-2026-11-04.md.

Reproduce the recorded run (defaults = its full sweep; it competes with tests):
    nice -n 10 python3 scripts/perf/smart_turn_offline_eval.py \
        --work-dir /tmp/st_eval --lock /tmp/zoe-test-lane.lock --json /tmp/st_eval/summary.json
Each batch takes the lock and re-checks MemAvailable, the night window, the backup and a
voice landing after taking it (unknown = wait); resumes from ``--work-dir``.
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import importlib.util
import json
import os
import statistics
import subprocess
import sys
import time
import types
from pathlib import Path
from typing import Any, Iterable, NamedTuple

import numpy as np

REPO = Path(__file__).resolve().parents[2]
DAEMON = REPO / "scripts" / "setup" / "zoe_voice_daemon.py"
VOICE_VAD = REPO / "services" / "zoe-data" / "voice_vad.py"
VOICE_TURN = REPO / "services" / "zoe-data" / "voice_turn.py"
CORPUS = Path.home() / ".zoe-voice-samples"

RATE = 16000
HOP = 512                 # Silero v6 hop at 16 kHz (voice_vad.HOP_SAMPLES)
CHUNK = 1280              # daemon CHUNK_SIZE (80 ms) — asserted against the daemon at load
CHUNK_MS = 1000.0 * CHUNK / RATE
OVER_WAIT_MS = 1000.0     # "over-wait": the turn closed more than this after the true end

# Live panel config, verified on zoe-pi .env.voice 2026-11-04 (names: VAD_ENDPOINT_ENABLED=1,
# VAD_ENDPOINT_SILENCE_S=0.8, ZOE_VAD_TAIL_MS=640; clean/hesitation tails unset = off).
LIVE_FIXED = {"name": "live 640deep/800", "VAD_ENDPOINT_SILENCE_S": 0.8, "ZOE_VAD_TAIL_MS": 640}
FIXED_VARIANTS = [
    LIVE_FIXED,
    {"name": "plain 800", "VAD_ENDPOINT_SILENCE_S": 0.8, "ZOE_VAD_TAIL_MS": 0},
    {"name": "480deep/800", "VAD_ENDPOINT_SILENCE_S": 0.8, "ZOE_VAD_TAIL_MS": 480},
    {"name": "plain 640", "VAD_ENDPOINT_SILENCE_S": 0.64, "ZOE_VAD_TAIL_MS": 0},
    {"name": "plain 480", "VAD_ENDPOINT_SILENCE_S": 0.48, "ZOE_VAD_TAIL_MS": 0},
    {"name": "plain 1200", "VAD_ENDPOINT_SILENCE_S": 1.2, "ZOE_VAD_TAIL_MS": 0},
]


# ── pure signal metrics (unit-tested on synthetic signals) ─────────────────────────────

def speech_end_sample(hop_probs: list[float], threshold: float, hop: int = HOP,
                      limit: int | None = None) -> int | None:
    """End sample of the last hop scoring >= threshold (only hops ending <= ``limit``)."""
    last = None
    for i, p in enumerate(hop_probs):
        end = (i + 1) * hop
        if limit is not None and end > limit:
            break
        if p >= threshold:
            last = end
    return last


def speech_start_sample(hop_probs: list[float], threshold: float, hop: int = HOP) -> int | None:
    for i, p in enumerate(hop_probs):
        if p >= threshold:
            return i * hop
    return None


def internal_pauses(hop_probs: list[float], threshold: float, hop: int = HOP,
                    limit: int | None = None) -> list[tuple[int, int]]:
    """(start, end) sample ranges of quiet hop runs strictly between the first and the
    last speech hop (ending <= ``limit``). Leading and trailing silence are not pauses."""
    end = speech_end_sample(hop_probs, threshold, hop, limit)
    if end is None:
        return []
    runs: list[tuple[int, int]] = []
    seen_speech = False
    run_start = None
    for i, p in enumerate(hop_probs):
        if (i + 1) * hop > end:
            break
        if p >= threshold:
            if seen_speech and run_start is not None:
                runs.append((run_start, i * hop))
            seen_speech = True
            run_start = None
        elif seen_speech and run_start is None:
            run_start = i * hop
    return runs


def chunk_probs_from_hops(hop_probs: list[float], n_chunks: int, chunk: int = CHUNK,
                          hop: int = HOP) -> list[float]:
    """Fold hop probabilities into the daemon's chunks the way ``_VadStream.feed`` does:
    a chunk scores the MAX of the hops it completes; a chunk completing none answers the
    last completed hop (0.0 before any)."""
    out: list[float] = []
    last = 0.0
    k = 0
    for j in range(n_chunks):
        hi = (j + 1) * chunk
        best = None
        while k < len(hop_probs) and (k + 1) * hop <= hi:
            last = hop_probs[k]
            best = last if best is None else max(best, last)
            k += 1
        out.append(last if best is None else best)
    return out


class TurnPolicy(NamedTuple):
    """Smart Turn policy: first check after ``first_ms`` of quiet that follows speech, then
    every ``recheck_ms``; end when P(complete) >= ``threshold``; close regardless at
    ``cap_ms`` of quiet."""
    first_ms: int
    threshold: float
    recheck_ms: int
    cap_ms: int

    @property
    def name(self) -> str:
        return f"ST S{self.first_ms} T{self.threshold:g} H{self.cap_ms}"


def check_points(chunk_probs: list[float], speech_threshold: float, first_ms: int,
                 recheck_ms: int, cap_ms: int, min_chunks: int = 0,
                 chunk_ms: float = CHUNK_MS) -> list[int]:
    """Chunk indices at which a Smart Turn policy would score (union over all first_ms that
    share the recheck grid is the caller's job). A check happens at quiet-run length q
    chunks where q*chunk_ms >= first_ms, (q*chunk_ms - first_ms) % recheck_ms == 0 and
    q*chunk_ms < cap_ms, after speech has been heard and past the minimum-recording guard."""
    pts: list[int] = []
    spoke = False
    q = 0
    for j, p in enumerate(chunk_probs):
        if p >= speech_threshold:
            spoke, q = True, 0
            continue
        q += 1
        if not spoke or j + 1 <= min_chunks:
            continue
        ms = q * chunk_ms
        if ms >= cap_ms:
            continue
        if ms >= first_ms and round(ms - first_ms) % recheck_ms == 0:
            pts.append(j)
    return pts


def simulate_smart_turn(chunk_probs: list[float], scores: dict[int, float],
                        policy: TurnPolicy, speech_threshold: float, min_chunks: int = 0,
                        chunk_ms: float = CHUNK_MS) -> tuple[int | None, str]:
    """Return (closing chunk index, reason) for a Smart Turn policy over scripted chunk
    probabilities and cached per-chunk end-of-turn scores. reason: 'model' | 'cap'."""
    spoke = False
    q = 0
    for j, p in enumerate(chunk_probs):
        if p >= speech_threshold:
            spoke, q = True, 0
            continue
        q += 1
        if not spoke or j + 1 <= min_chunks:
            continue
        ms = q * chunk_ms
        if ms >= policy.cap_ms:
            return j, "cap"
        if ms >= policy.first_ms and round(ms - policy.first_ms) % policy.recheck_ms == 0:
            score = scores.get(j)
            if score is None:
                raise KeyError(f"no Smart Turn score cached for chunk {j}")
            if score >= policy.threshold:
                return j, "model"
    return None, "never"


class ExtendPolicy(NamedTuple):
    """Extend-only Smart Turn (lane E E1 follow-up): the live fixed tail still decides WHEN to
    consider closing; Smart Turn may only HOLD the turn open past that point. At the live
    close it scores the capture; P(complete) >= ``threshold`` closes there (exactly today),
    otherwise it re-checks every ``recheck_ms`` of further quiet and closes at the first
    ``>= threshold`` or at ``cap_ms`` of total quiet. It can never close earlier than the
    fixed tail, so it can never cut more than today (speech that resumes in the extension
    restarts the quiet run, as in the live endpointer)."""
    threshold: float
    recheck_ms: int
    cap_ms: int

    @property
    def name(self) -> str:
        return f"EXT T{self.threshold:g} H{self.cap_ms}"


# Trailer lexicon: words a speaker does not END a finished thought on. Fillers and
# conjunctions are unambiguous; determiners/prepositions only ever lead into a noun phrase.
# "so" is the exception that proves the rule ("I think so"), handled below.
_FILLERS = frozenset({"um", "uh", "er", "erm", "umm", "uhh", "ah", "hmm", "mm", "like"})
_CONJUNCTIONS = frozenset({"and", "but", "or", "because", "cause", "then", "also", "plus",
                           "if", "when", "while", "although", "though", "whether", "that"})
_LEADERS = frozenset({"the", "a", "an", "my", "your", "his", "her", "their", "our", "to", "of",
                      "for", "with", "at", "from", "into", "about"})
_SO_ENDS_A_THOUGHT = frozenset({"think", "hope", "guess", "believe", "suppose", "said", "do",
                                "did", "does", "so", "why", "how", "if", "and", "not", "i", "we",
                                "you", "they", "he", "she", "it"})


def unfinished_trailer(text: str) -> bool:
    """True when ``text`` ends the way an unfinished thought does: a trailing filler or
    conjunction, an article/preposition that must be followed by more, a dangling ``so``
    (unless it closes "I think so"), or an ellipsis / trailing dash. Pure text rule - the
    caller supplies a transcript; no transcript means no opinion (False)."""
    t = text.strip()
    if not t:
        return False
    if t.endswith(("...", "\u2026", "-", "\u2014", "\u2013", ",")):
        return True
    words = [w for w in "".join(c if c.isalnum() or c in "' " else " " for c in t.lower()).split() if w]
    if not words:
        return False
    last = words[-1].strip("'")
    if last in _FILLERS or last in _CONJUNCTIONS or last in _LEADERS:
        return True
    if last == "so":
        return len(words) < 2 or words[-2] not in _SO_ENDS_A_THOUGHT
    return False


def extend_close(mod: types.ModuleType, chunk_probs: list[float], config: dict[str, Any],
                 policy: ExtendPolicy, score_at, frame_offset: int = 0,
                 speech_threshold: float | None = None, hold_at=None,
                 chunk_ms: float = CHUNK_MS) -> tuple[int | None, str, int]:
    """(closing chunk index, reason, n_model_checks) of the REAL ``_Endpointer`` run under
    ``config`` with ``policy`` allowed to veto its closes. ``score_at(j)`` returns P(complete)
    for the capture through chunk ``j`` (memoised by the caller); ``hold_at(j)`` (optional)
    says a transcript lexicon wants the turn held open at chunk ``j``. reason: 'live'
    (Smart Turn agreed), 'model' (agreed after extending), 'cap'."""
    thr = mod.VAD_ENDPOINT_THRESHOLD if speech_threshold is None else speech_threshold
    mod.VAD_ENDPOINT_SILENCE_S = config["VAD_ENDPOINT_SILENCE_S"]
    mod.ZOE_VAD_TAIL_MS = config["ZOE_VAD_TAIL_MS"]
    mod._vad_prob = lambda vad, _chunk, sample_rate=16000: vad.next()
    ep = mod._Endpointer(vad=_ScriptedVad(chunk_probs))
    if ep.mode != "vad":
        raise RuntimeError("endpointer is not in VAD mode - refusing to measure the amplitude path")
    blank = bytes(2 * CHUNK)
    q = 0
    q_first: int | None = None      # quiet-run length at which the live tail first wanted to close
    checks = 0
    for j, p in enumerate(chunk_probs):
        wants_close = ep.push(blank, frame_offset + j + 1)
        q = 0 if p >= thr else q + 1
        if q == 0:
            q_first = None
        if not wants_close:
            continue
        if q_first is None:
            q_first = q
        q_ms = q * chunk_ms
        if q_ms >= policy.cap_ms:
            return j, "cap", checks
        if round((q - q_first) * chunk_ms) % policy.recheck_ms:
            continue                  # between re-checks: keep holding
        checks += 1
        held = bool(hold_at and hold_at(j))
        if not held and score_at(j) >= policy.threshold:
            return j, ("live" if q == q_first else "model"), checks
    return None, "never", checks


def outcome(close_sample: int | None, speech_end: int, pauses: list[tuple[int, int]],
            rate: int = RATE) -> dict[str, Any]:
    """Score one close against the ground truth: early cut (closed before the speaker
    finished), latency after the true end, and whether the cut fell inside a long pause."""
    if close_sample is None:
        return {"closed": False}
    if close_sample < speech_end:
        in_pause = any(s < close_sample <= e for s, e in pauses)
        return {"closed": True, "cut": True, "cut_in_pause": in_pause}
    return {"closed": True, "cut": False, "latency_ms": 1000.0 * (close_sample - speech_end) / rate}


def pct(values: list[float], q: float) -> float | None:
    """Nearest-rank percentile (q in 0..100), None on empty."""
    if not values:
        return None
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round(q / 100.0 * (len(s) - 1)))))
    return round(s[k], 1)


def summarise(outcomes: list[dict[str, Any]], over_wait_ms: float = OVER_WAIT_MS) -> dict[str, Any]:
    n = len(outcomes)
    lat = [o["latency_ms"] for o in outcomes if o.get("closed") and not o.get("cut")]
    cuts = sum(1 for o in outcomes if o.get("cut"))
    never = sum(1 for o in outcomes if not o.get("closed"))
    over = sum(1 for v in lat if v > over_wait_ms)
    return {
        "n": n, "cuts": cuts, "never_closed": never, "over_waits": over,
        "cut_rate": round(cuts / n, 4) if n else None,
        "correct_rate": round((n - cuts - never - over) / n, 4) if n else None,
        "latency_mean_ms": round(statistics.fmean(lat), 1) if lat else None,
        "latency_p50_ms": pct(lat, 50), "latency_p90_ms": pct(lat, 90),
    }


def tile_room_tone(audio: np.ndarray, speech_end: int, need: int, skip: int = 2 * CHUNK,
                   min_tone: int = 1600) -> tuple[np.ndarray, bool]:
    """``need`` samples of the clip's own trailing room tone (tiled from ``speech_end +
    skip`` so the speech decay is not looped). Falls back to zeros — flagged — when the clip
    has less than ``min_tone`` samples of tail."""
    tone = audio[speech_end + skip:]
    if len(tone) < min_tone:
        return np.zeros(need, dtype=np.int16), False
    reps = -(-need // len(tone))
    return np.tile(tone, reps)[:need].astype(np.int16), True


def mid_boundary(pauses_all: list[tuple[int, int]], start: int, end: int,
                 min_len: int = 2 * HOP) -> int | None:
    """A mid-utterance word boundary: the middle of the internal quiet run (>= min_len)
    closest to the utterance midpoint. None when the clip has no such boundary."""
    mid = (start + end) // 2
    best = None
    for s, e in pauses_all:
        if e - s < min_len:
            continue
        c = (s + e) // 2
        if best is None or abs(c - mid) < abs(best - mid):
            best = c
    return best


# ── loaders: the REAL daemon endpointer, the REAL VAD, the REAL Smart Turn scorer ─────

def load_daemon(source: Path = DAEMON) -> types.ModuleType:
    """Execute the shipped daemon source with Pi-only imports stubbed (pyaudio and
    openwakeword always; requests only when absent). Nothing the endpointer uses is stubbed."""
    stubs: dict[str, types.ModuleType] = {}
    pa = types.ModuleType("pyaudio")
    pa.paInt16 = 8
    pa.PyAudio = object
    stubs["pyaudio"] = pa
    for name in ("openwakeword", "openwakeword.model"):
        m = types.ModuleType(name)
        m.__getattr__ = lambda _a: types.SimpleNamespace()  # type: ignore[attr-defined]
        stubs[name] = m
    with contextlib.suppress(ImportError, ValueError):
        if importlib.util.find_spec("requests") is None:
            stubs["requests"] = types.ModuleType("requests")
    saved = {n: sys.modules.get(n) for n in stubs}
    sys.modules.update(stubs)
    try:
        spec = importlib.util.spec_from_file_location("_zoe_daemon_st_eval", source)
        if not spec or not spec.loader:
            raise RuntimeError(f"cannot load {source}")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        for n, prev in saved.items():
            if prev is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = prev
    if mod.CHUNK_SIZE != CHUNK or mod.SAMPLE_RATE != RATE:
        raise RuntimeError(f"daemon chunk/rate {mod.CHUNK_SIZE}/{mod.SAMPLE_RATE} != {CHUNK}/{RATE}")
    mod.VAD_ENDPOINT_ENABLED = True   # the live panel runs VAD endpointing (code default is off)
    return mod


class _ScriptedVad:
    """Stands in for the endpointer's ``_VadStream``: answers pre-computed chunk probs."""

    def __init__(self, probs: Iterable[float]):
        self._it = iter(probs)

    def next(self) -> float:
        return next(self._it)


def fixed_close(mod: types.ModuleType, chunk_probs: list[float], config: dict[str, Any],
                frame_offset: int = 0) -> int | None:
    """Closing chunk index of the REAL ``_Endpointer`` under ``config``, or None.
    ``frame_offset``: chunks already in the recording (the pre-roll) — ``record_command``
    passes ``len(frames)``, which counts them."""
    mod.VAD_ENDPOINT_SILENCE_S = config["VAD_ENDPOINT_SILENCE_S"]
    mod.ZOE_VAD_TAIL_MS = config["ZOE_VAD_TAIL_MS"]
    mod._vad_prob = lambda vad, _chunk, sample_rate=16000: vad.next()
    ep = mod._Endpointer(vad=_ScriptedVad(chunk_probs))
    if ep.mode != "vad":
        raise RuntimeError("endpointer is not in VAD mode — refusing to measure the amplitude path")
    blank = bytes(2 * CHUNK)
    for j in range(len(chunk_probs)):
        if ep.push(blank, frame_offset + j + 1):
            return j
    return None


def load_path(name: str, path: Path) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if not spec or not spec.loader:
        raise RuntimeError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class UnscoredClip(Exception):
    """The VAD returned fewer hops than the audio owes: a clip is UNSCORED, never read as silence or as an early end of speech."""


VAD_INCOMPLETE = [0]      # clips refused for a short hop sequence (per process; each batch records its own delta)


def hop_probs_for(vad_mod: types.ModuleType, audio: np.ndarray) -> list[float]:
    vad = vad_mod.create_vad()
    if vad is None:
        raise RuntimeError("Silero VAD (voice_vad) unavailable — model file or onnxruntime missing")
    probs = vad.process_hops(audio.astype(np.int16).tobytes())
    # process_hops never raises: on an inference failure it stops and returns the completed PREFIX, which would read as
    # "speech ended here" (and chunk_probs_from_hops would pad the rest with the last value). Short = unscored.
    if len(probs) < len(audio) // HOP:
        raise UnscoredClip(f"VAD returned {len(probs)} of {len(audio) // HOP} hops")
    return probs


def read_wav_mono16(path: Path) -> np.ndarray | None:
    import wave
    try:
        with contextlib.closing(wave.open(str(path), "rb")) as wf:
            if wf.getsampwidth() != 2 or wf.getnchannels() != 1 or wf.getframerate() != RATE:
                return None
            raw = wf.readframes(wf.getnframes())
    except Exception:
        return None
    a = np.frombuffer(raw, dtype=np.int16)
    return a if a.size else None


# ── per-clip evaluation ────────────────────────────────────────────────────────────

WAKE_PREROLL_CHUNKS = 20      # daemon PREROLL_CHUNKS (wake path)
FOLLOWUP_LOOKBACK_CHUNKS = 4  # daemon FOLLOWUP_LOOKBACK_CHUNKS (follow-up path)


def auto_preroll(vad_mod, audio: np.ndarray, threshold: float) -> int:
    """The corpus WAVs carry no lane metadata, so infer it: a wake-path capture always has
    command speech AFTER its 20-chunk pre-roll (the wake fired at the end of it); a clip
    whose speech all sits inside the first 20 chunks can only be a follow-up capture (4
    lookback chunks). A long follow-up misread as wake-path only hides pauses in its first
    1.6 s from every arm alike."""
    probs = hop_probs_for(vad_mod, audio[WAKE_PREROLL_CHUNKS * CHUNK:])
    end = speech_end_sample(probs, threshold)
    start = speech_start_sample(probs, threshold)
    if end is not None and start is not None and end - start >= 4 * HOP:
        return WAKE_PREROLL_CHUNKS
    return FOLLOWUP_LOOKBACK_CHUNKS


def evaluate_stream(*args, **kw) -> dict[str, Any] | None:
    """``_evaluate_stream``, with a clip whose VAD run came back short returned as None (unscored, counted in ``VAD_INCOMPLETE``)."""
    try:
        return _evaluate_stream(*args, **kw)
    except UnscoredClip:
        VAD_INCOMPLETE[0] += 1
        return None


def _evaluate_stream(mod, vad_mod, det, audio: np.ndarray, *, gt_threshold: float,
                    speech_threshold: float, policies: list[TurnPolicy], cap_max_ms: int,
                    first_ms_set: set[int], recheck_ms: int, long_pause_ms: int,
                    preroll_chunks: int = 0, insert_ms: int = 0,
                    st_times: list[float] | None = None,
                    extend_policies: list[ExtendPolicy] | None = None) -> dict[str, Any] | None:
    """Score every arm on one clip, optionally with a ``insert_ms`` pause of room tone
    inserted at its mid-utterance word boundary. None = nothing to score (no speech after
    the pre-roll, or no word boundary for the synthetic arm).

    Like ``record_command``, the endpointer never sees the first ``preroll_chunks`` chunks
    (the wake pre-roll the recorder prepends): it gets a FRESH VAD stream starting after
    them, and ``n_frames`` counts them. Ground truth, pauses and every close are measured
    on that same post-pre-roll stream; Smart Turn, like the live frame buffer, scores the
    whole capture so far (pre-roll included, last 8 s)."""
    if preroll_chunks < 0:
        preroll_chunks = auto_preroll(vad_mod, audio, gt_threshold)
    off = preroll_chunks * CHUNK
    if len(audio) <= off + 8 * HOP:
        return None
    probs = hop_probs_for(vad_mod, audio[off:])
    end = speech_end_sample(probs, gt_threshold)
    start = speech_start_sample(probs, gt_threshold)
    if end is None or start is None or end - start < 4 * HOP:
        return None
    gap_n = int(insert_ms * RATE / 1000)
    pad_need = int((cap_max_ms + 1500) * RATE / 1000)
    tail, real_tone = tile_room_tone(audio, off + end, pad_need + gap_n)
    if gap_n:
        at = mid_boundary(internal_pauses(probs, gt_threshold), start, end)
        if at is None:
            return None
        full = np.concatenate([audio[:off + at], tail[:gap_n], audio[off + at:off + end], tail[gap_n:]])
        end_gt = end + gap_n
        long_pauses = [(at, at + gap_n)]
    else:
        full = np.concatenate([audio, tail[:pad_need]])
        end_gt = end
    ep_audio = full[off:]
    hop_probs = hop_probs_for(vad_mod, ep_audio)   # same fresh stream: the clip prefix matches `probs`
    cprobs = chunk_probs_from_hops(hop_probs, len(ep_audio) // CHUNK)
    if not gap_n:
        long_pauses = [(s, e) for s, e in internal_pauses(hop_probs, gt_threshold, limit=end_gt)
                       if 1000.0 * (e - s) / RATE > long_pause_ms]

    def close_sample(j: int | None) -> int | None:
        return None if j is None else (j + 1) * CHUNK

    row: dict[str, Any] = {"long_pause": bool(long_pauses), "real_tone": real_tone,
                           "preroll": preroll_chunks, "arms": {}}
    for cfg in FIXED_VARIANTS:
        j = fixed_close(mod, cprobs, cfg, frame_offset=preroll_chunks)
        row["arms"][cfg["name"]] = outcome(close_sample(j), end_gt, long_pauses)
    if det is not None and extend_policies:
        memo: dict[int, float] = {}

        def score_at(j: int) -> float:
            if j not in memo:
                t0 = time.perf_counter()
                memo[j] = det.end_of_turn_prob(full[: off + (j + 1) * CHUNK])
                if st_times is not None:
                    st_times.append(1000.0 * (time.perf_counter() - t0))
            return memo[j]

        for pol in extend_policies:
            j, reason, n_checks = extend_close(mod, cprobs, LIVE_FIXED, pol, score_at,
                                               frame_offset=preroll_chunks)
            o = outcome(close_sample(j), end_gt, long_pauses)
            o.update(checks=n_checks, reason=reason)
            row["arms"][pol.name] = o
    if det is not None and policies:
        # the endpointer's own minimum-recording guard, counted in frames incl. the pre-roll
        min_chunks = max(0, int(0.5 * RATE / CHUNK) - preroll_chunks)
        pts: set[int] = set()
        for f in first_ms_set:
            pts.update(check_points(cprobs, speech_threshold, f, recheck_ms, cap_max_ms, min_chunks))
        scores: dict[int, float] = {}
        for j in sorted(pts):
            t0 = time.perf_counter()
            scores[j] = det.end_of_turn_prob(full[: off + (j + 1) * CHUNK])
            if st_times is not None:
                st_times.append(1000.0 * (time.perf_counter() - t0))
        for pol in policies:
            j, _reason = simulate_smart_turn(cprobs, scores, pol, speech_threshold, min_chunks)
            row["arms"][pol.name] = outcome(close_sample(j), end_gt, long_pauses)
    return row


def build_policies(firsts: list[int], thresholds: list[float], caps: list[int], recheck: int) -> list[TurnPolicy]:
    return [TurnPolicy(f, t, recheck, c) for c in caps for f in firsts for t in thresholds]


# ── resource guard ────────────────────────────────────────────────────────────────

def mem_available_mb() -> int:
    with open("/proc/meminfo", encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
    return 0


BATCH_BUDGET_MIN = 20     # what one scored batch may take: the night gate refuses a start it could still be running through at 01:50


def night_gate_busy(budget_min: int = BATCH_BUDGET_MIN) -> str | None:
    """The voice night gate (scripts/AGENTS.md "Brain-window quiet hours"): ``harness_quiet.night_pause_verdict`` = ``night_busy.sh
    voice`` (the measured 12B window / nightly voice regression / waking hours), falling back to the fixed 01:50-04:55 span, and
    refusing a start that a ``budget_min`` batch would still be alive through at 01:50. The night window treats the harness lock as
    a landing in progress, so this runs BEFORE the lock is taken and again before every batch. Fails closed."""
    try:
        spec = importlib.util.spec_from_file_location("harness_quiet_st_eval", REPO / "scripts" / "maintenance" / "harness_quiet.py")
        hq = importlib.util.module_from_spec(spec)
        sys.modules["harness_quiet_st_eval"] = hq              # its dataclass resolves its own module by name
        spec.loader.exec_module(hq)
        v = hq.night_pause_verdict("smart_turn_offline_eval", budget_min=budget_min)
    except Exception as exc:  # noqa: BLE001
        return f"cannot read the voice night gate ({type(exc).__name__})"
    return None if v.allowed else v.reason


def box_busy(min_mem_mb: int) -> str | None:
    """Why this batch must wait, or None. Mirrors scripts/landing/night_busy.sh `test`
    (night window or backup running) + a voice landing + the RAM floor. Fails CLOSED: a
    probe that cannot answer is a reason to wait, never a green light."""
    if (why := night_gate_busy()):
        return why
    for unit in ("zoe-night-window.service", "zoe-backup.service"):
        try:
            r = subprocess.run(["systemctl", "--user", "is-active", unit],
                               capture_output=True, text=True, timeout=5)
        except Exception as exc:  # noqa: BLE001
            return f"cannot probe {unit} ({exc})"
        state = r.stdout.strip()
        if state in ("active", "activating", "deactivating"):
            return f"{unit} {state}"
        if state not in ("inactive", "failed", "unknown"):
            return f"cannot read {unit} state ({state or r.returncode})"
    try:
        r = subprocess.run(["pgrep", "-f", r"^bash .*/land_voice_pr\.sh"], capture_output=True, timeout=5)
    except Exception as exc:  # noqa: BLE001
        return f"cannot probe for a voice landing ({exc})"
    if r.returncode != 1:          # 0 = found, >1 = pgrep error: both wait
        return "voice landing running" if r.returncode == 0 else f"pgrep failed ({r.returncode})"
    try:
        mem = mem_available_mb()
    except OSError as exc:
        return f"cannot read MemAvailable ({exc})"
    if mem < min_mem_mb:
        return f"MemAvailable {mem} MB < {min_mem_mb} MB"
    return None


def acquire_quiet_lock(lock_path: Path | None, min_mem_mb: int, label: str,
                       busy=box_busy, wait_s: float = 60.0):
    """Take the lock, then RE-CHECK the box: the lock wait can be long, and a night window,
    backup, landing or RAM dip that began during it must still stop the batch. Returns the
    held lock file (None when no lock was asked for)."""
    while True:
        if (why := busy(min_mem_mb)):
            print(f"{label}: waiting ({why})", file=sys.stderr, flush=True)
            time.sleep(wait_s)
            continue
        fh = open(lock_path, "a") if lock_path else None  # noqa: SIM115
        if fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
        if not (why := busy(min_mem_mb)):
            return fh
        if fh:
            fh.close()                 # release for others while we wait
        print(f"{label}: waiting after lock ({why})", file=sys.stderr, flush=True)
        time.sleep(wait_s)


def guarded(lock_path: Path | None, min_mem_mb: int, label: str, work,
            busy=box_busy, wait_s: float = 60.0):
    """Run ``work()`` only while holding the quiet guard. Everything heavy (the Smart Turn ONNX
    session) is created INSIDE ``work``, so a deferred run holds no model while it waits."""
    fh = acquire_quiet_lock(lock_path, min_mem_mb, label, busy=busy, wait_s=wait_s)
    try:
        return work()
    finally:
        if fh:
            fh.close()


def batch_key(files: list[Path], b0: int, batch_size: int, every: int) -> str:
    """The exact input a batch scored: its start, the batch geometry, and each clip's name and
    size - hashed, so the work dir names no clip. A cached batch for any other slice (another
    --limit, --corpus or --batch-size) is never reused."""
    ident = [b0, batch_size, every, [[f.name, f.stat().st_size] for f in files]]
    return hashlib.sha256(json.dumps(ident).encode()).hexdigest()


def cached_batch(part: Path | None, fingerprint: str, key: str) -> dict[str, Any] | None:
    if not part or not part.exists():
        return None
    try:
        data = json.loads(part.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if data.get("fingerprint") != fingerprint or data.get("slice") != key:
        return None        # another configuration or another slice of the corpus: recompute
    return data


# ── aggregation + CLI ─────────────────────────────────────────────────────────────

def extend_stats(rows: list[dict[str, Any]], name: str, inference_ms: float = 0.0) -> dict[str, Any]:
    """Extend-only arm vs the live tail, clip by clip: how much later it closes (model time
    excluded and included), how many live cut-offs it rescued, how often it held at all.
    ``inference_ms`` (measured p50 per Smart Turn check) is charged once per check."""
    live = LIVE_FIXED["name"]
    added, added_inc, rescued, extended, checks = [], [], 0, 0, 0
    for r in rows:
        a, b = r["arms"].get(live), r["arms"].get(name)
        if not a or not b or not b.get("closed"):
            continue
        checks += b.get("checks", 0)
        if a.get("cut") and not b.get("cut"):
            rescued += 1
        if a.get("cut") or b.get("cut"):
            continue
        d = b["latency_ms"] - a["latency_ms"]
        added.append(d)
        added_inc.append(d + inference_ms * b.get("checks", 0))
        extended += d > 0
    n = len(added)
    return {"extended_turns": extended, "extended_rate": round(extended / n, 4) if n else None,
            "cuts_rescued": rescued, "checks_per_turn": round(checks / len(rows), 2) if rows else None,
            "added_wait_mean_ms": round(statistics.fmean(added), 1) if added else None,
            "added_wait_p50_ms": pct(added, 50), "added_wait_p90_ms": pct(added, 90),
            "added_incl_inference_mean_ms": round(statistics.fmean(added_inc), 1) if added_inc else None,
            "added_incl_inference_p50_ms": pct(added_inc, 50)}


def aggregate(rows: list[dict[str, Any]], inference_ms: float = 0.0) -> dict[str, Any]:
    arms: dict[str, list[dict[str, Any]]] = {}
    long_arms: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        for name, o in r["arms"].items():
            arms.setdefault(name, []).append(o)
            if r["long_pause"]:
                long_arms.setdefault(name, []).append(o)
    out = {"n_clips": len(rows), "n_long_pause_clips": sum(1 for r in rows if r["long_pause"]),
           "zero_padded": sum(1 for r in rows if not r["real_tone"]),
           "preroll_counts": {str(k): sum(1 for r in rows if r.get("preroll") == k)
                              for k in sorted({r.get("preroll") for r in rows}, key=str)},
           "arms": {}}
    for name, os_ in arms.items():
        s = summarise(os_)
        lp = long_arms.get(name, [])
        s["long_pause_held"] = sum(1 for o in lp if not o.get("cut_in_pause"))
        s["long_pause_cut"] = sum(1 for o in lp if o.get("cut_in_pause"))
        if name.startswith("EXT "):
            s.update(extend_stats(rows, name, inference_ms))
        out["arms"][name] = s
    return out


def print_table(title: str, agg: dict[str, Any]) -> None:
    print(f"\n{title}: n={agg['n_clips']} clips, {agg['n_long_pause_clips']} with a long pause, "
          f"{agg['zero_padded']} zero-padded, pre-roll chunks {agg['preroll_counts']}")
    print(f"  {'arm':<26} {'cuts':>5} {'cut%':>6} {'over':>5} {'correct%':>8} {'mean':>6} {'p50':>6} "
          f"{'p90':>6} {'LPheld':>6} {'LPcut':>5}")
    for name, s in agg["arms"].items():
        f = lambda v: "-" if v is None else f"{v:.0f}"  # noqa: E731
        print(f"  {name:<26} {s['cuts']:>5} {100 * (s['cut_rate'] or 0):>5.1f}% {s['over_waits']:>5} "
              f"{100 * (s['correct_rate'] or 0):>7.1f}% {f(s['latency_mean_ms']):>6} {f(s['latency_p50_ms']):>6} "
              f"{f(s['latency_p90_ms']):>6} {s['long_pause_held']:>6} {s['long_pause_cut']:>5}")


def print_extend(agg: dict[str, Any]) -> None:
    ext = {n: a for n, a in agg["arms"].items() if n.startswith("EXT ")}
    if not ext:
        return
    print(f"  {'extend-only arm':<18} {'extended':>8} {'rescued':>7} {'chk/turn':>8} {'+mean':>6} {'+p50':>6} "
          f"{'+p90':>6} {'+p50 w/ inference':>18}")
    f = lambda v: "-" if v is None else f"{v:.0f}"  # noqa: E731
    for n, a in ext.items():
        print(f"  {n:<18} {100 * (a['extended_rate'] or 0):>7.1f}% {a['cuts_rescued']:>7} {a['checks_per_turn']:>8} "
              f"{f(a['added_wait_mean_ms']):>6} {f(a['added_wait_p50_ms']):>6} {f(a['added_wait_p90_ms']):>6} "
              f"{f(a['added_incl_inference_p50_ms']):>18}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus", type=Path, default=CORPUS)
    ap.add_argument("--limit", type=int, default=0, help="newest N clips only (0 = all)")
    ap.add_argument("--batch-size", type=int, default=100)
    ap.add_argument("--work-dir", type=Path, help="per-batch numeric results (resume); keep OUTSIDE the repo")
    ap.add_argument("--lock", type=Path, help="flock this file per batch (e.g. /tmp/zoe-test-lane.lock)")
    ap.add_argument("--min-mem-mb", type=int, default=1500)
    ap.add_argument("--gt-threshold", type=float, default=0.35)
    ap.add_argument("--first-ms", default="160,320,480", help="Smart Turn first-check quiet (ms)")
    ap.add_argument("--recheck-ms", type=int, default=160)
    ap.add_argument("--thresholds", default="0.3,0.5,0.7,0.85,0.95")
    ap.add_argument("--caps", default="800,1600", help="Smart Turn fallback close (ms of quiet)")
    ap.add_argument("--long-pause-ms", type=int, default=600)
    ap.add_argument("--preroll-chunks", type=int, default=-1,
                    help="chunks the endpointer never sees: -1 = infer per clip (20 = wake pre-roll "
                         "when command speech follows it, else 4 = follow-up lookback); 0 = whole clip")
    ap.add_argument("--synthetic-gaps", default="1000,2000", help="inserted mid-utterance pauses (ms); '' = off")
    ap.add_argument("--synthetic-every", type=int, default=2, help="synthetic arm on every Nth clip")
    ap.add_argument("--threads", type=int, default=2, help="ORT threads for Smart Turn (1..4)")
    ap.add_argument("--extend-thresholds", default="", help="extend-only Smart Turn arms (E1 follow-up): "
                    "thresholds, e.g. 0.3,0.5,0.7,0.85,0.95 ('' = none)")
    ap.add_argument("--extend-caps", default="1000,1200,1600", help="extend-only hold ceiling (ms of total quiet)")
    ap.add_argument("--extend-only", action="store_true", help="skip the standalone Smart Turn arms")
    ap.add_argument("--no-smart-turn", action="store_true", help="fixed arms only (negative control)")
    ap.add_argument("--json", type=Path, help="write the aggregate summary here")
    args = ap.parse_args()

    os.environ["ZOE_SMART_TURN_THREADS"] = str(max(1, min(4, args.threads)))
    mod = load_daemon()
    vad_mod = load_path("_zoe_voice_vad_st_eval", VOICE_VAD)
    det_box: dict[str, Any] = {}       # Smart Turn, loaded lazily INSIDE the guard (first batch to score)
    firsts = [int(x) for x in args.first_ms.split(",") if x.strip()]
    caps = [int(x) for x in args.caps.split(",") if x.strip()]
    for f in firsts:
        if (f - firsts[0]) % args.recheck_ms:
            print("--first-ms values must share the --recheck-ms grid", file=sys.stderr)
            return 2
    policies = [] if (args.no_smart_turn or args.extend_only) else build_policies(
        firsts, [float(x) for x in args.thresholds.split(",") if x.strip()], caps, args.recheck_ms)
    gaps = [int(x) for x in args.synthetic_gaps.split(",") if x.strip()]
    ext_caps = [int(x) for x in args.extend_caps.split(",") if x.strip()]
    extend_policies = [] if args.no_smart_turn else [
        ExtendPolicy(float(t), args.recheck_ms, c) for c in ext_caps
        for t in args.extend_thresholds.split(",") if t.strip()]
    kw = dict(gt_threshold=args.gt_threshold, speech_threshold=mod.VAD_ENDPOINT_THRESHOLD,
              policies=policies, extend_policies=extend_policies,
              cap_max_ms=max(caps + ext_caps + [1200]), first_ms_set=set(firsts),
              recheck_ms=args.recheck_ms, long_pause_ms=args.long_pause_ms,
              preroll_chunks=args.preroll_chunks)

    fingerprint = json.dumps({**{k: (sorted(v) if isinstance(v, set) else v) for k, v in kw.items()
                                 if k not in ("policies", "extend_policies")},
                              "policies": [p.name for p in policies],
                              "extend": [p.name for p in extend_policies],
                              "gaps": gaps, "every": args.synthetic_every,
                              "fixed": FIXED_VARIANTS}, sort_keys=True)
    files = sorted(args.corpus.glob("*.wav"))
    if args.limit:
        files = files[-args.limit:]
    if args.work_dir:
        args.work_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    synth: dict[int, list[dict[str, Any]]] = {g: [] for g in gaps}
    st_times: list[float] = []
    skipped = vad_incomplete = 0
    for b0 in range(0, len(files), args.batch_size):
        part = args.work_dir / f"batch_{b0:05d}.json" if args.work_dir else None
        key = batch_key(files[b0:b0 + args.batch_size], b0, args.batch_size, args.synthetic_every)
        data = cached_batch(part, fingerprint, key)
        if data is None:
            def score_batch(b0: int = b0, key: str = key) -> dict[str, Any] | None:
                if not args.no_smart_turn and "det" not in det_box:
                    det_box["det"] = load_path("_zoe_voice_turn_st_eval", VOICE_TURN).get_smart_turn()
                det = det_box.get("det")
                if det is None and not args.no_smart_turn:
                    return None
                data = {"rows": [], "synth": {str(g): [] for g in gaps}, "st_ms": [], "skipped": 0,
                        "fingerprint": fingerprint, "slice": key}
                inc0 = VAD_INCOMPLETE[0]
                for i, path in enumerate(files[b0:b0 + args.batch_size], start=b0):
                    audio = read_wav_mono16(path)
                    r = None if audio is None else evaluate_stream(
                        mod, vad_mod, det, audio, st_times=data["st_ms"], **kw)
                    if r is None:
                        data["skipped"] += 1
                        continue
                    data["rows"].append(r)
                    if gaps and i % args.synthetic_every == 0:
                        for g in gaps:
                            s = evaluate_stream(mod, vad_mod, det, audio, insert_ms=g, **kw)
                            if s is not None:
                                data["synth"][str(g)].append(s)
                data["vad_incomplete"] = VAD_INCOMPLETE[0] - inc0
                return data
            data = guarded(args.lock, args.min_mem_mb, f"batch {b0}", score_batch)
            if data is None:
                print("Smart Turn model or onnxruntime missing (ZOE_SMART_TURN_MODEL, default "
                      "/home/zoe/models/smart-turn-v3.2-cpu.onnx) — refusing to report a fixed-only run "
                      "as a comparison; pass --no-smart-turn to measure the fixed arms alone.", file=sys.stderr)
                return 2
            if part:
                part.write_text(json.dumps(data), encoding="utf-8")
            print(f"batch {b0}: {len(data['rows'])} scored, {data['skipped']} skipped", file=sys.stderr, flush=True)
        rows.extend(data["rows"])
        skipped += data["skipped"]
        vad_incomplete += data.get("vad_incomplete", 0)
        st_times.extend(data["st_ms"])
        for g in gaps:
            synth[g].extend(data["synth"].get(str(g), []))

    if vad_incomplete:
        print(f"WARNING: {vad_incomplete} clip(s) UNSCORED - the VAD returned fewer hops than the audio owes", file=sys.stderr)
        if not rows:
            print("every clip was unscored: the VAD cannot infer - refusing to report an empty run as a measurement", file=sys.stderr)
            return 2
    inf_p50 = pct(st_times, 50) or 0.0
    summary = {"corpus_clips": len(files), "skipped_no_speech_or_format": skipped, "unscored_vad_incomplete": vad_incomplete,
               "config": {k: (sorted(v) if isinstance(v, set) else v) for k, v in kw.items() if k not in ("policies", "extend_policies")},
               "policies": [p.name for p in policies], "extend": [p.name for p in extend_policies],
               "natural": aggregate(rows, inf_p50),
               "synthetic": {str(g): aggregate(v, inf_p50) for g, v in synth.items()},
               "smart_turn_ms": {"calls": len(st_times), "p50": pct(st_times, 50), "p90": pct(st_times, 90)}}
    print_table("Natural clips (true end = last Silero hop >= %.2f)" % args.gt_threshold, summary["natural"])
    print_extend(summary["natural"])
    for g, agg in summary["synthetic"].items():
        print_table(f"Synthetic {g} ms mid-utterance pause (LP = the inserted pause)", agg)
        print_extend(agg)
    print(f"\nSmart Turn inference on this box: {summary['smart_turn_ms']}")
    if args.json:
        args.json.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"Wrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
