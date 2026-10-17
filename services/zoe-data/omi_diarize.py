"""Who spoke when in a pendant recording - speaker turns for ``omi_segments`` (flag-dark ``ZOE_OMI_SPEAKER_TAG``).

Design: ``docs/architecture/memories-space.md`` section 3. The choice (measured on this Jetson, record in the PR and
``docs/knowledge/omi-voice-tagging.md``): Silero VAD regions + 2 s / 1 s-hop windows embedded by the SAME pinned WeSpeaker
ResNet34 INT8 file the panel uses (``scripts/setup/zoe_speaker_resnet34.py``, loaded by path: one front end, one pin, one
self-test) + centroid-linkage clustering. Adopted pieces, not rebuilt: the embedder and VAD are the shipped ones; the
clustering cut and the ``min_duration_on/off`` clean-up copy sherpa-onnx 1.13.8's pyannote pipeline (which itself pairs a
segmenter with this very embedding model). sherpa-onnx was not taken because it bundles a second ONNX Runtime (1.28 beside our
1.23.2), never exposes the embeddings we need to compare with the wearer and learned voices, and costs ~3x the embeddings.

Text alignment: Moonshine gives no word times, so the transcriber cuts its chunks AT these turn boundaries (each turn is
transcribed on its own, a long one split at its quietest points as before). The STT model is unchanged.

RAM / CPU (measured 2026-10-17, synthetic audio): +41 MB RSS for the session, ~100 ms and ~0.1 CPU-s per window on ONE thread
(~6 CPU-s per minute of speech). BLAS is pinned to one thread while embedding (``threadpoolctl``): the numpy fbank otherwise
fans out over every core (5x the CPU for the same wall time) beside the live voice stack. The model is loaded per pass and
released after it. Nothing here touches the panel's live speaker-ID path.

Pure apart from ``load_embedder`` (file) and ``diarise`` (reads the WAV); every number is a parameter for the tests.
"""
from __future__ import annotations

import asyncio
import importlib.util
import logging
import os
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np

from typed_env import env_float, env_str

logger = logging.getLogger(__name__)

RATE = 16000
HOP = 512                          # Silero's hop (32 ms)
MODES = ("off", "shadow", "on")
#: ``you`` = the FAR 0.1 % LONG-group bar (#2024): on synthetic two-speaker conversations it bought +2 pt you-precision for -1 pt
#: recall over the short-group 0.309 (knowledge doc, cell D). A false ``you`` is the worse error. ``other`` is the design's placeholder.
#: Both are re-derived on the pendant channel from the owner's per-line corrections.
YOU_MIN, OTHER_MAX = 0.351, 0.15
WINDOW_S, HOP_S = 2.0, 1.0
MIN_EMBED_S = 1.0                  # a speech region shorter than this gets no window
MIN_ON_S, MIN_OFF_S = 0.3, 0.5     # sherpa-onnx defaults: drop blips, bridge short pauses
MIN_SEG_S = 1.0
LINK_MIN = 0.45                    # centroid-linkage cut (cosine of centred embeddings); calibrated, see the knowledge doc
SNAP_S = 0.5
GATE_EVERY = 50                    # ask the transcription gate before every batch of this many windows
REPO = Path(__file__).resolve().parents[2]
EMBEDDER_PATH = REPO / "scripts" / "setup" / "zoe_speaker_resnet34.py"


def mode() -> str:
    """``ZOE_OMI_SPEAKER_TAG`` = off (DEFAULT) | shadow | on. Anything else is off. Shadow and on both tag and store segments."""
    raw = env_str("ZOE_OMI_SPEAKER_TAG", "off").strip().lower()
    return raw if raw in MODES else "off"


def thresholds() -> tuple[float, float]:
    return env_float("ZOE_OMI_TAG_YOU_MIN", YOU_MIN), env_float("ZOE_OMI_TAG_OTHER_MAX", OTHER_MAX)


@dataclass
class Segment:
    start_ms: int
    end_ms: int
    speaker: str                   # you | other | unsure | mixed | unverified
    score: Optional[float] = None
    windows: int = 0
    embedding: Optional[np.ndarray] = field(default=None, repr=False)   # L2 mean of the window embeddings


def l2(x: Any) -> np.ndarray:
    a = np.asarray(x, dtype=np.float64)
    n = np.linalg.norm(a, axis=-1, keepdims=True)
    return (a / np.maximum(n, 1e-12)).astype(np.float32)


# --- VAD -----------------------------------------------------------------------------------------------------------
def regions(probs: list, *, hop_s: float = HOP / RATE, on: float = 0.5, off: float = 0.35, min_on: float = MIN_ON_S,
            min_off: float = MIN_OFF_S) -> list[tuple[float, float]]:
    """Speech regions (seconds) from per-hop speech probabilities: hysteresis (start at ``on``, stay while >= ``off``), pauses
    under ``min_off`` bridged, regions under ``min_on`` dropped."""
    out: list[list[float]] = []
    active, start = False, 0.0
    for i, p in enumerate(probs):
        t = i * hop_s
        if not active and p >= on:
            active, start = True, t
        elif active and p < off:
            active = False
            out.append([start, t])
    if active:
        out.append([start, len(probs) * hop_s])
    merged: list[list[float]] = []
    for s, e in out:
        if merged and s - merged[-1][1] < min_off:
            merged[-1][1] = e
        else:
            merged.append([s, e])
    return [(s, e) for s, e in merged if e - s >= min_on]


def energy_probs(pcm: bytes, peak_floor: int = 300) -> list[float]:
    """The fallback when Silero is unavailable: a hop is 'speech' when its int16 peak clears the transcriber's silence floor."""
    a = np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype="<i2")
    n = len(a) // HOP
    if not n:
        return []
    return [1.0 if v >= peak_floor else 0.0 for v in np.abs(a[: n * HOP].reshape(n, HOP).astype(np.int32)).max(axis=1)]


def window_spans(regs: list, *, win: float = WINDOW_S, hop: float = HOP_S, min_s: float = MIN_EMBED_S) -> list[tuple[float, float]]:
    spans = []
    for s, e in regs:
        if e - s < min_s:
            continue
        if e - s <= win:
            spans.append((s, e))
            continue
        t = s
        while t + win <= e + 1e-9:
            spans.append((t, t + win))
            t += hop
        if spans[-1][1] < e - hop / 2:        # the region's tail gets its own window
            spans.append((e - win, e))
    return spans


# --- labelling + clustering ----------------------------------------------------------------------------------------
def label(score: Optional[float], you_min: float, other_max: float) -> str:
    if score is None:
        return "unverified"
    return "you" if score >= you_min else ("other" if score < other_max else "unsure")


def link(embs: np.ndarray, min_sim: float = LINK_MIN) -> list[int]:
    """Local speaker ids: consecutive windows first fold into runs (a turn is contiguous), then runs merge by centroid linkage
    while the closest pair of centroids is at least ``min_sim`` cosine. Deterministic; O(k^2) per merge over k runs."""
    n = len(embs)
    if n == 0:
        return []
    run_of, sums, counts = [0], [embs[0].astype(np.float64).copy()], [1]
    for i in range(1, n):
        if float(l2(sums[-1]) @ embs[i]) >= min_sim:
            sums[-1] += embs[i]
            counts[-1] += 1
        else:
            sums.append(embs[i].astype(np.float64).copy())
            counts.append(1)
        run_of.append(len(sums) - 1)
    parent = list(range(len(sums)))
    live = list(range(len(sums)))
    while len(live) > 1:
        C = l2(np.stack([sums[i] for i in live]))
        S = C @ C.T
        np.fill_diagonal(S, -2.0)
        a, b = np.unravel_index(int(np.argmax(S)), S.shape)
        if S[a, b] < min_sim:
            break
        keep, drop = live[min(a, b)], live[max(a, b)]
        sums[keep] = sums[keep] + sums[drop]
        counts[keep] += counts[drop]
        parent[drop] = keep
        live.remove(drop)

    def root(i: int) -> int:
        while parent[i] != i:
            i = parent[i]
        return i

    ids: dict[int, int] = {}
    return [ids.setdefault(root(r), len(ids)) for r in run_of]


def _smooth(ids: list[int]) -> list[int]:
    """3-window mode filter: a single window that disagrees with both neighbours (which agree) takes their id."""
    out = list(ids)
    for i in range(1, len(ids) - 1):
        if ids[i - 1] == ids[i + 1] != ids[i]:
            out[i] = ids[i - 1]
    return out


def plan(spans: list, embs: list, scores: list, duration_s: float, *, you_min: float = YOU_MIN, other_max: float = OTHER_MAX,
         min_sim: float = LINK_MIN, min_seg_s: float = MIN_SEG_S, snap: Optional[Callable[[float, float, float], float]] = None
         ) -> list[Segment]:
    """Turns covering ``[0, duration]`` contiguously (so no audio is left untranscribed): runs of one local speaker, cut midway
    between their windows (``snap`` moves a cut to the quietest point nearby), short runs folded into a neighbour, then labelled
    from their windows' scores: ``mixed`` when >= 25 % of windows say ``you`` AND >= 25 % say ``other``."""
    dur_ms = int(round(duration_s * 1000))
    keep = [i for i, e in enumerate(embs) if e is not None and np.all(np.isfinite(e))]
    if not keep:
        return [Segment(0, dur_ms, "unverified")]
    E = l2(np.stack([embs[i] for i in keep]))
    ids = _smooth(link(E, min_sim))
    groups: list[list[int]] = []
    gid: list[int] = []
    for j, k in enumerate(keep):
        if groups and ids[j] == gid[-1]:
            groups[-1].append(k)
        else:
            groups.append([k])
            gid.append(ids[j])
    cuts = [0.0]
    for g, h in zip(groups, groups[1:]):
        t = (sum(spans[g[-1]]) / 2 + sum(spans[h[0]]) / 2) / 2
        cuts.append(snap(t, sum(spans[g[-1]]) / 2, sum(spans[h[0]]) / 2) if snap else t)
    cuts.append(duration_s)
    segs = [[cuts[i], cuts[i + 1], g] for i, g in enumerate(groups)]
    i = 0
    while len(segs) > 1 and i < len(segs):          # fold a turn shorter than min_seg_s into its longer neighbour
        if segs[i][1] - segs[i][0] < min_seg_s:
            j = i - 1 if i == len(segs) - 1 or (i > 0 and segs[i - 1][1] - segs[i - 1][0] >= segs[i + 1][1] - segs[i + 1][0]) else i + 1
            lo, hi = sorted((i, j))
            segs[lo] = [segs[lo][0], segs[hi][1], segs[lo][2] + segs[hi][2]]
            del segs[hi]
            i = 0
            continue
        i += 1
    out = []
    for s, e, ws in segs:
        sc = [scores[w] for w in ws if scores[w] is not None]
        mean = float(np.mean(sc)) if sc else None
        labels = [label(scores[w], you_min, other_max) for w in ws]
        n = len(labels)
        if sc and labels.count("you") / n >= 0.25 and labels.count("other") / n >= 0.25:
            spk = "mixed"
        else:
            spk = label(mean, you_min, other_max)
        out.append(Segment(int(round(s * 1000)), int(round(e * 1000)), spk, None if mean is None else round(mean, 4), n,
                           l2(np.mean(np.stack([embs[w] for w in ws]), axis=0))))
    out[0].start_ms, out[-1].end_ms = 0, dur_ms
    return out


# --- the model + the pass ------------------------------------------------------------------------------------------
def _embedder_module():
    spec = importlib.util.spec_from_file_location("zoe_speaker_resnet34_omi", EMBEDDER_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def model_id() -> str:
    import speaker_models
    return speaker_models.RESNET34_MODEL_ID


def load_embedder() -> Optional[Any]:
    """The pinned ResNet34 (sha256 + centring pin + self-test), ONE intra-op thread, or None (WARNING) - then nothing is
    tagged. Model dir ``ZOE_OMI_TAG_MODEL_DIR`` (default ``~/.cache/zoe-speaker-resnet34``, where ``fetch_speaker_models.sh build``
    writes on the Jetson); the centring artifacts are the COMMITTED file beside the module, never the build's review copy."""
    try:
        zr = _embedder_module()
        d = env_str("ZOE_OMI_TAG_MODEL_DIR", "").strip() or os.path.expanduser("~/.cache/zoe-speaker-resnet34")
        return zr.Resnet34Embedder.load(d, artifacts_path=str(EMBEDDER_PATH.parent / zr.ARTIFACTS_FILENAME), threads=1)
    except Exception as exc:  # noqa: BLE001 - Unavailable / SelfTestFailed / ImportError: refuse rather than trust
        logger.warning("OMI_TAG_UNAVAILABLE reason=%s", type(exc).__name__)
        return None


def own_vad() -> Optional[Any]:
    """A PRIVATE single-threaded Silero session for this pass (``voice_vad``'s wrapper and model file, NOT the live voice path's
    shared session, whose default thread pool fans out over every core). None = the energy fallback."""
    try:
        import onnxruntime as ort
        import voice_vad
        path = voice_vad._model_path()
        if not os.path.isfile(path):
            return None
        so = ort.SessionOptions()
        so.intra_op_num_threads, so.inter_op_num_threads = 1, 1
        return voice_vad.SileroVAD(ort.InferenceSession(path, so, providers=["CPUExecutionProvider"]))
    except Exception as exc:  # noqa: BLE001
        logger.info("omi_diarize: Silero unavailable (%s), energy VAD", type(exc).__name__)
        return None


def _one_thread():
    try:
        from threadpoolctl import threadpool_limits
        return threadpool_limits(limits=1)
    except Exception:  # noqa: BLE001
        import contextlib
        return contextlib.nullcontext()


def _quietest(w: Any, t: float, lo: float, hi: float, span: float = SNAP_S) -> float:
    a, b = max(lo, t - span), min(hi, t + span)
    if b - a < 0.2:
        return t
    w.setpos(int(a * RATE))
    x = np.abs(np.frombuffer(w.readframes(int((b - a) * RATE)), dtype="<i2").astype(np.int32))
    win = RATE // 10
    if len(x) < win:
        return t
    e = np.convolve(x, np.ones(win, dtype=np.int64), mode="valid")[::160]
    return a + (int(np.argmin(e)) * 160 + win // 2) / RATE


async def diarise(path: str, embedder: Any, profile: Optional[np.ndarray], *, gate: Optional[Callable] = None,
                  vad_factory: Optional[Callable] = None) -> list[Segment]:
    """The pass over one WAV (streamed: one second for VAD, one window for an embedding - never the whole file). ``profile`` is
    the wearer's centred unit vector in the SAME model (None: every segment is ``unverified``). ``gate()`` (sync or async) is
    asked before each batch of windows; a closed gate raises the transcriber's ``GateClosed``."""
    from omi_recordings import GateClosed

    if vad_factory is None:
        vad_factory = own_vad
    you_min, other_max = thresholds()
    with wave.open(path) as w:
        dur = w.getnframes() / RATE
        vad = vad_factory()
        probs: list = []
        carry = [b""]                          # the energy fallback's partial hop between one-second blocks (RATE is not a multiple of HOP)

        def _vad_minute() -> bool:            # off the event loop: a 30-minute file is ~56k Silero hops
            for _ in range(60):
                block = w.readframes(RATE)
                if not block:
                    return False
                if vad is not None:
                    probs.extend(vad.process_hops(block))
                else:                          # without the carry every block dropped 128 samples and the timeline drifted 8 ms per second
                    buf = carry[0] + block
                    whole = len(buf) // (2 * HOP) * (2 * HOP)
                    probs.extend(energy_probs(buf[:whole]))
                    carry[0] = buf[whole:]
            return True
        while True:
            if gate is not None:
                verdict = gate()
                ok, why = (await verdict) if asyncio.iscoroutine(verdict) else verdict
                if not ok:
                    raise GateClosed(why)
            if not await asyncio.to_thread(_vad_minute):
                break
        del vad
        spans = window_spans(regions(probs))
        embs: list = []
        scores: list = []
        for b in range(0, len(spans), GATE_EVERY):
            if gate is not None:
                verdict = gate()
                ok, why = (await verdict) if asyncio.iscoroutine(verdict) else verdict
                if not ok:
                    raise GateClosed(why)
            clips = []
            for s, e in spans[b:b + GATE_EVERY]:
                w.setpos(int(s * RATE))
                clips.append(np.frombuffer(w.readframes(int((e - s) * RATE)), dtype="<i2").astype(np.float32) / 32768.0)

            def _embed(batch=clips):
                with _one_thread():
                    return [embedder.embed(c) for c in batch]
            for v in await asyncio.to_thread(_embed):
                embs.append(v)
                scores.append(None if v is None or profile is None else float(l2(v) @ profile))
            await asyncio.sleep(0)
        return plan(spans, embs, scores, dur, you_min=you_min, other_max=other_max,
                    min_sim=env_float("ZOE_OMI_DIAR_LINK_MIN", LINK_MIN), snap=lambda t, lo, hi: _quietest(w, t, lo, hi))
