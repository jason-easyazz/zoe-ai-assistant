#!/usr/bin/env python3
"""
Kokoro TTS sidecar — FastAPI server on port 10201.

Keeps the Kokoro PyTorch model warm in GPU memory and exposes a simple
HTTP endpoint that voice_tts.py calls instead of kokoro-onnx on CPU.
This gives the natural af_sky voice at GPU speed (~150-400ms warm).

Usage:
    python3 kokoro_sidecar.py
    # or via systemd: kokoro-tts.service

Endpoints:
    POST /synthesize  { "text": "...", "voice": "af_sky" }  → audio/wav bytes
    GET  /health                                             → {"status":"ok", ...}

Jetson CUDA notes
-----------------
1. NVML assertion (CUDACachingAllocator.cpp:1131):
   PyTorch's default CUDACachingAllocator makes NVML memory queries that
   Jetson's nvgpu does not fully support, triggering an internal assertion.
   Fix: set PYTORCH_CUDA_ALLOC_CONF=backend:cudaMallocAsync *before* importing
   torch — uses CUDA's native async allocator with no NVML calls.
   This env var is defaulted in-process so the systemd unit doesn't need it.

2. NumPy 2.x incompatibility:
   PyTorch 2.8 was compiled against NumPy 1.x; NumPy 2.2.6 is installed.
   tensor.numpy() raises "Numpy is not available" at runtime.
   Fix: audio conversion uses tensor.tolist() + struct.pack (no numpy needed).
"""
import asyncio
import hashlib
import io
import json
import logging
import os
import struct
import threading
import time
import wave
from contextlib import asynccontextmanager
from pathlib import Path

# Must be set before torch import to bypass Jetson NVML assertion.
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "backend:cudaMallocAsync")

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [kokoro-tts] %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

# ─── Config ───────────────────────────────────────────────────────────────────

_PORT = int(os.environ.get("KOKORO_SIDECAR_PORT", "10201"))
_VOICE = os.environ.get("KOKORO_VOICE", "af_sky").strip() or "af_sky"
_SAMPLE_RATE = 24000  # Kokoro outputs 24 kHz

# Backend: "onnx" (kokoro-onnx on CPU, ~600MB, frees the ~2.3GB GPU the PyTorch
# build held — SAME af_sky weights, identical voice) or "pytorch" (KPipeline on
# CUDA, ~2.3GB, ~150ms). ONNX is the default; set ZOE_KOKORO_BACKEND=pytorch to
# fall back instantly with no other change.
_BACKEND = (os.environ.get("ZOE_KOKORO_BACKEND") or "onnx").strip().lower()
_ONNX_MODEL = os.environ.get("ZOE_KOKORO_MODEL", "/home/zoe/models/kokoro-v1.0.onnx")
_ONNX_VOICES = os.environ.get("ZOE_KOKORO_VOICES", "/home/zoe/models/voices-v1.0.bin")

# ─── Global state ─────────────────────────────────────────────────────────────

_pipeline = None
_device = "cpu"
_pipeline_lock = asyncio.Lock()  # serialise inference; pipeline is not thread-safe

# Pre-synthesised cache for common short phrases (populated during lifespan startup).
# Keys are lowercased stripped text; values are WAV bytes.  Only hits when
# voice == _VOICE and speed == 1.0 to guarantee audio quality matches.
_phrase_cache: dict[str, bytes] = {}

# Phrases to pre-warm at startup.  Chosen to cover the most frequent short Zoe
# responses so they return in <1ms instead of ~450ms.
_WARM_PHRASES = [
    "Sure!",
    "Done!",
    "Got it!",
    "Of course!",
    "Turning on the lights.",
    "Turning off the lights.",
    "Lights are on.",
    "Lights are off.",
    "I'll remind you.",
    "Reminder set.",
    "I'm not sure about that.",
    "Let me check.",
    "I can help with that.",
    "Playing music now.",
    "Music paused.",
    "Sorry, I didn't catch that.",
    "Could you say that again?",
    "I don't have access to that right now.",
    "Good morning!",
    "Good evening!",
    # Period forms — the first-turn-of-day greeting (voice_greeting.apply_greeting)
    # prepends these as their own leading sentence ("Good morning. It's 14 …"), so
    # the sentence-streamed TTS renders "Good morning." with a period, not "!".
    "Good morning.",
    "Good afternoon.",
    "Good evening.",
    "What can I help you with?",
    "On it.",
    "All done.",
    "No problem!",
    "Happy to help!",
    # Canonical confirmations the voice/fast-path actually emits (verified in
    # voice_tts.py / main.py) — these are spoken verbatim after common actions,
    # so caching them means real replies hit the cache, not just generic ones.
    "Okay, cancelled.",
    "Cancelled.",
    "Event saved.",
    "List saved.",
    "Got it, updated.",
    "Good afternoon!",
    "Yes?",
    "Mmm?",
    "Goodbye!",
    "You're welcome!",
] + [
    # Buffer / "thinking" phrases — played on the panel the instant a command is
    # captured, to fill the STT+brain+TTS gap with natural variation instead of
    # dead air ("Hey Zoe, what's the weather" → "Let me check" → real answer).
    # The panel fetches these once at startup and plays one at random per turn.
    p for p in [
        "Let me check.",
        "One moment.",
        "Just a second.",
        "Let me see.",
        "Let me look that up.",
        "Checking now.",
        "Give me a moment.",
        "Looking into that.",
        "Let me find out.",
        "One sec.",
        "Hold on a moment.",
        "Let me get that for you.",
    ]
]

# Runtime LRU cache bounds: any af_sky / speed-1.0 phrase shorter than this is
# stored after first synthesis, so repeated replies (which dominate real usage)
# are served from cache (~2ms) instead of re-synthesised (~1-2.5s).
_CACHE_MAX_ENTRIES = 400
_CACHE_MAX_TEXT_LEN = 240


def _env_int(name: str, default: int) -> int:
    """Parse a positive int env var, falling back to default on missing/garbage/≤0.

    All callers (disk-entry / byte budgets, flush interval) require a positive
    value; a 0 or negative override would either busy-loop the flusher or make
    _select_within_budget evict almost the whole restart cache, so we treat
    non-positive as garbage and use the documented default.  To disable
    persistence entirely, use ZOE_KOKORO_CACHE_PERSIST=0.
    """
    try:
        value = int(str(os.environ.get(name, default)).strip())
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off", "")


# ─── Persistent, frequency-ranked cache layer ──────────────────────────────────
#
# The in-memory LRU above is fast but lost on every restart, re-seeded only from
# the static _WARM_PHRASES list.  This layer persists the *hot* set to disk and
# reloads it on startup, so the first render of a real phrase after a restart is
# a cache hit (~2ms) instead of a fresh 1-2.5s synth, and the prewarm self-seeds
# from observed usage.  Everything here is fail-open: any disk error, or
# ZOE_KOKORO_CACHE_PERSIST=0, degrades to today's byte-identical in-memory LRU.
#
#   Layout (under _CACHE_DIR):
#     <sha256(key)>.wav   one file per cached phrase
#     manifest.json       {"entries": {key: {"hits", "last_used", "bytes"}}}
_CACHE_PERSIST = _env_flag("ZOE_KOKORO_CACHE_PERSIST", True)
_CACHE_DIR = Path(
    os.environ.get("ZOE_KOKORO_CACHE_DIR") or (Path.home() / ".zoe" / "kokoro_cache")
).expanduser()
# Disk budget: cap both entry count and total bytes; evict coldest (lowest hits,
# then oldest) when over either.  Default ~1000 entries / 256 MB.
_CACHE_MAX_DISK = _env_int("ZOE_KOKORO_CACHE_MAX_DISK", 1000)
_CACHE_MAX_DISK_BYTES = _env_int("ZOE_KOKORO_CACHE_MAX_DISK_BYTES", 256 * 1024 * 1024)
_CACHE_FLUSH_INTERVAL_S = float(_env_int("ZOE_KOKORO_CACHE_FLUSH_INTERVAL_S", 60))
_MANIFEST_NAME = "manifest.json"

# Per-key usage stats (hits + last-used wall clock), tracked in memory and
# persisted to the manifest.  Survives LRU eviction so a phrase that fell out of
# memory keeps its frequency rank on disk.
_cache_meta: dict[str, dict] = {}
# Set whenever the in-memory cache/meta changes; the periodic flusher only writes
# when dirty, so an idle sidecar does zero disk I/O.
_cache_dirty = False

# Serialises the actual disk write across worker threads so two flushes can never
# interleave their file writes / manifest os.replace().  Combined with the
# no-cancel shutdown drain below, this guarantees the final shutdown flush is the
# last writer (no stale-snapshot rollback from a lingering periodic worker).
_flush_disk_lock = threading.Lock()


def _note_hit(key: str, wav_len: int | None = None) -> None:
    """Record a request for ``key`` (frequency + recency); marks the cache dirty."""
    global _cache_dirty
    meta = _cache_meta.get(key)
    if meta is None:
        meta = {"hits": 0, "last_used": 0.0, "bytes": 0}
        _cache_meta[key] = meta
    meta["hits"] += 1
    meta["last_used"] = time.time()
    if wav_len is not None:
        meta["bytes"] = wav_len
    _cache_dirty = True


def _cache_get(key: str) -> bytes | None:
    """Return cached WAV and mark it most-recently-used (dict insertion order = LRU)."""
    wav = _phrase_cache.get(key)
    if wav is not None:
        _phrase_cache.pop(key, None)
        _phrase_cache[key] = wav
        _note_hit(key, len(wav))
    return wav


def _cache_store(key: str, wav: bytes) -> None:
    """Store WAV, evicting the oldest entry when over the bound."""
    _phrase_cache[key] = wav
    _note_hit(key, len(wav))
    while len(_phrase_cache) > _CACHE_MAX_ENTRIES:
        # pop oldest (first-inserted) key
        oldest = next(iter(_phrase_cache))
        _phrase_cache.pop(oldest, None)


def _key_filename(key: str) -> str:
    """Deterministic on-disk filename for a cache key (sha256 avoids unsafe chars)."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest() + ".wav"


def _manifest_path(cache_dir: Path) -> Path:
    return cache_dir / _MANIFEST_NAME


def _coerce_num(value, cast, default):
    """Safely coerce a manifest scalar; garbage (e.g. "many") falls back to default."""
    try:
        return cast(value)
    except (TypeError, ValueError):
        return default


def _read_manifest(cache_dir: Path) -> dict:
    """Load the manifest's entry map; returns {} on any error (fail-open).

    Every returned entry is normalised to ``{"hits": int, "last_used": float,
    "bytes": int}`` with garbage scalars coerced to safe defaults, so no
    downstream int()/float() on manifest data can raise and slip past the flush
    path's OSError-only guard (which would leave _cache_dirty cleared but the
    flush never completed).
    """
    try:
        raw = json.loads(_manifest_path(cache_dir).read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    entries = raw.get("entries", {})
    if not isinstance(entries, dict):
        return {}
    # Skip malformed entries (non-dict values) so one bad row can't AttributeError
    # its way through _flush_to_disk / _reload_from_disk, and normalise scalars so
    # a value like {"hits": "many"} can't ValueError there either.
    return {
        k: {
            "hits": _coerce_num(v.get("hits", 0), int, 0),
            "last_used": _coerce_num(v.get("last_used", 0.0), float, 0.0),
            "bytes": _coerce_num(v.get("bytes", 0), int, 0),
        }
        for k, v in entries.items()
        if isinstance(v, dict)
    }


def _write_manifest(cache_dir: Path, entries: dict) -> None:
    """Atomically write the manifest (temp file + os.replace)."""
    path = _manifest_path(cache_dir)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"entries": entries}, ensure_ascii=False), "utf-8")
    os.replace(tmp, path)


def _select_within_budget(entries: dict, max_disk: int, max_bytes: int) -> tuple[list[str], list[str]]:
    """Split keys into (keep, evict) keeping the hottest within count+byte budget.

    Ranking: hits desc, then last_used desc (a tie breaks toward more-recent).
    Guarantees at least the single hottest entry is kept if any exist, so a lone
    over-budget entry never wipes the cache entirely.
    """
    ranked = sorted(
        entries.items(),
        key=lambda kv: (kv[1].get("hits", 0), kv[1].get("last_used", 0.0)),
        reverse=True,
    )
    keep: list[str] = []
    evict: list[str] = []
    total = 0
    for key, meta in ranked:
        size = int(meta.get("bytes", 0))
        if len(keep) < max_disk and (total + size <= max_bytes or not keep):
            keep.append(key)
            total += size
        else:
            evict.append(key)
    return keep, evict


def _flush_to_disk(
    cache_dir: Path,
    phrase_cache: dict,
    meta: dict,
    max_disk: int,
    max_bytes: int,
) -> bool:
    """Persist the hot set to ``cache_dir`` and enforce the disk budget.

    Runs OFF the synth hot path (periodic task / shutdown), never inside a
    /synthesize request.  Fail-open: any error is logged and swallowed so a
    broken disk can never break synthesis.  Returns True on a completed flush,
    False if the manifest could not be written (so the caller can re-arm the
    dirty flag and retry next cycle instead of dropping learned phrases).

    The whole write sequence is serialised on ``_flush_disk_lock`` so two flush
    workers can never interleave file writes / manifest replace — whoever holds
    the lock completes fully before the next starts.
    """
    with _flush_disk_lock:
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
            existing = _read_manifest(cache_dir)
            # Merge: start from what's already on disk, overlay fresh stats, and
            # (re)write WAV bytes for anything currently in memory.
            combined: dict[str, dict] = {}
            for key, m in existing.items():
                fn = _key_filename(key)
                if (cache_dir / fn).exists():
                    combined[key] = {
                        "hits": int(m.get("hits", 0)),
                        "last_used": float(m.get("last_used", 0.0)),
                        "bytes": int(m.get("bytes", 0)),
                    }
            for key, m in meta.items():
                entry = combined.setdefault(key, {"hits": 0, "last_used": 0.0, "bytes": 0})
                entry["hits"] = int(m.get("hits", entry["hits"]))
                entry["last_used"] = float(m.get("last_used", entry["last_used"]))
            for key, wav in phrase_cache.items():
                try:
                    (cache_dir / _key_filename(key)).write_bytes(wav)
                except OSError as exc:
                    logger.warning("Kokoro cache: failed to write %r: %s", key, exc)
                    continue
                entry = combined.setdefault(key, {"hits": 0, "last_used": 0.0, "bytes": 0})
                entry["bytes"] = len(wav)
            # Drop manifest entries whose WAV never made it to disk.
            combined = {k: v for k, v in combined.items() if (cache_dir / _key_filename(k)).exists()}
            keep, evict = _select_within_budget(combined, max_disk, max_bytes)
            for key in evict:
                combined.pop(key, None)
                try:
                    (cache_dir / _key_filename(key)).unlink()
                except OSError:
                    pass
            _write_manifest(cache_dir, {k: combined[k] for k in keep})
            return True
        except OSError as exc:
            logger.warning("Kokoro cache flush skipped (disk error): %s", exc)
            return False


def _reload_from_disk(cache_dir: Path, max_entries: int) -> tuple[dict, dict]:
    """Load the persisted hot set, sorted by hits desc, bounded by ``max_entries``.

    Returns (phrase_cache, meta): bytes for the top-N warmed into memory, and
    stats for ALL manifest entries (so on-disk-only phrases keep their rank).
    Fail-open: any error yields empty dicts.
    """
    phrase_cache: dict[str, bytes] = {}
    meta: dict[str, dict] = {}
    entries = _read_manifest(cache_dir)
    if not entries:
        return phrase_cache, meta
    ranked = sorted(
        entries.items(),
        key=lambda kv: (kv[1].get("hits", 0), kv[1].get("last_used", 0.0)),
        reverse=True,
    )
    for key, m in ranked:
        stats = {
            "hits": int(m.get("hits", 0)),
            "last_used": float(m.get("last_used", 0.0)),
            "bytes": int(m.get("bytes", 0)),
        }
        meta[key] = stats
        if len(phrase_cache) >= max_entries:
            continue
        try:
            wav = (cache_dir / _key_filename(key)).read_bytes()
        except OSError:
            continue
        phrase_cache[key] = wav
        stats["bytes"] = len(wav)
    return phrase_cache, meta


# ─── Pipeline loading ─────────────────────────────────────────────────────────

def _load_pipeline():
    """Load and return the Kokoro pipeline (blocking; run once in thread pool)."""
    global _device

    if _BACKEND == "onnx":
        from kokoro_onnx import Kokoro  # type: ignore
        logger.info("Loading Kokoro ONNX (model=%s voices=%s voice=%s)…",
                    _ONNX_MODEL, _ONNX_VOICES, _VOICE)
        pipeline = Kokoro(_ONNX_MODEL, _ONNX_VOICES)
        _device = "cpu (onnx)"
        logger.info("Kokoro ONNX pipeline ready (CPU) — same af_sky weights, ~600MB, no GPU.")
        return pipeline

    # ── PyTorch / CUDA fallback (ZOE_KOKORO_BACKEND=pytorch) ──────────────────
    import torch
    from kokoro import KPipeline  # type: ignore

    device = "cuda" if torch.cuda.is_available() else "cpu"
    logger.info("Loading Kokoro KPipeline (lang=a device=%s voice=%s)…", device, _VOICE)
    try:
        pipeline = KPipeline(lang_code="a", device=device)
        _device = device
        logger.info("Kokoro pipeline ready on %s.", device)
    except Exception as exc:
        logger.warning("CUDA load failed (%s) — falling back to CPU.", exc)
        pipeline = KPipeline(lang_code="a", device="cpu")
        _device = "cpu"
        logger.info("Kokoro pipeline ready on cpu (fallback).")
    return pipeline


# ─── Lifespan: load + CUDA graph warmup ───────────────────────────────────────

async def _flush_cache_async(loop, force: bool = False) -> None:
    """Snapshot the in-memory cache and flush it to disk in a worker thread.

    Never called on the /synthesize path.  Clears the dirty flag first so
    concurrent hits during the flush re-arm it for the next cycle.  ``force``
    flushes even when the dirty flag is clear — used by the shutdown path so an
    in-flight periodic flush that was cancelled (dirty already cleared) can't make
    the final flush return early and drop recently learned phrases.
    """
    global _cache_dirty
    if not _CACHE_PERSIST:
        return
    if not (_cache_dirty or force):
        return
    # Clear BEFORE the flush so concurrent hits during it re-arm the flag for the
    # next cycle; if the flush itself fails, re-arm so we retry rather than drop
    # the learned phrases.
    _cache_dirty = False
    snapshot = dict(_phrase_cache)
    meta = {k: dict(v) for k, v in _cache_meta.items()}
    ok = await loop.run_in_executor(
        None,
        _flush_to_disk,
        _CACHE_DIR,
        snapshot,
        meta,
        _CACHE_MAX_DISK,
        _CACHE_MAX_DISK_BYTES,
    )
    if not ok:
        _cache_dirty = True


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _pipeline
    loop = asyncio.get_event_loop()
    flush_task: asyncio.Task | None = None
    stop_flush = asyncio.Event()
    try:
        _pipeline = await loop.run_in_executor(None, _load_pipeline)
        # One warmup synthesis primes the engine so the first real request is fast.
        logger.info("Warming up Kokoro…")
        await _run_synthesis("Zoe is ready.", _VOICE, speed=1.0)
        # Reload the persisted hot set FIRST — this restores real, frequency-ranked
        # phrases into memory (instant next request after a restart) and self-seeds
        # the prewarm from observed usage.  Fail-open if persistence is off/broken.
        if _CACHE_PERSIST:
            try:
                reloaded, meta = await loop.run_in_executor(
                    None, _reload_from_disk, _CACHE_DIR, _CACHE_MAX_ENTRIES
                )
                _phrase_cache.update(reloaded)
                _cache_meta.update(meta)
                if reloaded:
                    logger.info(
                        "Kokoro cache: reloaded %d hot phrases from %s (top by hit count).",
                        len(reloaded), _CACHE_DIR,
                    )
            except Exception as _reload_exc:  # never block startup on cache reload
                logger.warning("Kokoro cache reload skipped: %s", _reload_exc)
        # Pre-cache common phrases in the BACKGROUND — on CPU each synth is
        # ~0.3-0.5s, so caching ~50 inline would block the :%d bind for 20-40s.
        # _WARM_PHRASES is now a FLOOR: only synth phrases not already reloaded
        # from disk, so the static list backfills gaps without redoing hot work.
        async def _bg_precache():
            cached = 0
            skipped = 0
            for phrase in _WARM_PHRASES:
                key = phrase.strip().lower()
                if key in _phrase_cache:
                    skipped += 1
                    continue
                try:
                    _cache_store(key, await _run_synthesis(phrase, _VOICE, speed=1.0))
                    cached += 1
                except Exception as _cache_exc:
                    logger.warning("Phrase cache skip %r: %s", phrase, _cache_exc)
            logger.info(
                "Phrase cache warmed in background: %d synthesised, %d already hot, %d total.",
                cached, skipped, len(_WARM_PHRASES),
            )
            await _flush_cache_async(loop)
        asyncio.create_task(_bg_precache())

        # Periodic off-hot-path flush: persist the hot set as it evolves so a
        # crash/restart loses at most one interval of learning.  The loop wakes on
        # either the interval OR the shutdown event, and is stopped by SETTING the
        # event and AWAITING the task (never cancel) so any in-flight flush runs to
        # completion before the final shutdown flush — guaranteeing shutdown is the
        # last writer.
        async def _periodic_flush():
            while not stop_flush.is_set():
                try:
                    await asyncio.wait_for(stop_flush.wait(), timeout=_CACHE_FLUSH_INTERVAL_S)
                except asyncio.TimeoutError:
                    pass
                try:
                    await _flush_cache_async(loop)
                except Exception as _flush_exc:
                    logger.warning("Kokoro cache periodic flush error: %s", _flush_exc)
        if _CACHE_PERSIST:
            flush_task = asyncio.create_task(_periodic_flush())

        logger.info("Kokoro sidecar ready on port %d (device=%s); phrase cache warming in background.", _PORT, _device)
    except Exception as exc:
        logger.error("Failed to initialise Kokoro: %s", exc)
        raise
    yield
    logger.info("Kokoro sidecar shutting down.")
    stop_flush.set()
    if flush_task is not None:
        # Await (do NOT cancel) so any in-flight _flush_to_disk worker finishes —
        # cancelling would abandon the await while the detached thread kept writing,
        # letting a late os.replace() roll the manifest back to a stale snapshot.
        await asyncio.gather(flush_task, return_exceptions=True)
    # Final flush so the latest learning survives the restart.  force=True in case
    # the periodic loop already cleared the dirty flag; it runs strictly after the
    # drained periodic flush and holds _flush_disk_lock, so it is the last writer.
    try:
        await _flush_cache_async(loop, force=True)
    except Exception as _final_exc:
        logger.warning("Kokoro cache final flush error: %s", _final_exc)


app = FastAPI(title="Kokoro TTS Sidecar", lifespan=lifespan)


# ─── Audio helpers ────────────────────────────────────────────────────────────

def _pcm_to_wav(audio_tensor, sample_rate: int = _SAMPLE_RATE) -> bytes:
    """Convert a torch.FloatTensor of PCM samples to WAV bytes.

    Uses struct.pack instead of tensor.numpy() because PyTorch 2.8 was
    compiled against NumPy 1.x but NumPy 2.x is installed — the numpy
    bridge raises 'Numpy is not available' at runtime.
    """
    import torch
    audio_int16 = (
        audio_tensor.detach().float().clamp(-1.0, 1.0)
        .mul(32767)
        .to(torch.int16)
        .cpu()
        .reshape(-1)
    )
    n = audio_int16.shape[0]
    pcm_bytes = struct.pack(f"<{n}h", *audio_int16.tolist())
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)
    return buf.getvalue()


def _samples_to_wav(samples, sample_rate: int = _SAMPLE_RATE) -> bytes:
    """Convert kokoro-onnx float32 PCM samples (numpy array) to WAV bytes."""
    import numpy as np
    arr = np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0)
    pcm_bytes = (arr * 32767.0).astype("<i2").tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)
    return buf.getvalue()


_MAX_OOM_RETRIES = 2  # 2 retries × 500ms sleep = max ~1.5s extra; HTTP conn stays open


def _blocking_synthesize(text: str, voice: str, speed: float) -> bytes:
    if _BACKEND == "onnx":
        # kokoro-onnx: same af_sky weights, CPU, returns (float32 samples, sample_rate)
        samples, sr = _pipeline.create(text, voice=voice, speed=speed, lang="en-us")
        return _samples_to_wav(samples, sr)
    return _blocking_synthesize_pytorch(text, voice, speed)


def _blocking_synthesize_pytorch(text: str, voice: str, speed: float) -> bytes:
    """Run Kokoro inference synchronously (called inside run_in_executor).

    Calls torch.cuda.empty_cache() before every attempt to release any
    cached-but-unreserved CUDA blocks from the previous synthesis.  On
    Jetson, the first request after warmup can raise 'Allocation on device'
    because the warmup left allocations in the cache; empty_cache() prevents
    this.  If the error still occurs, we retry up to _MAX_OOM_RETRIES times
    with a 500ms pause between attempts.

    The HTTP connection from voice_tts stays open during retries (httpx
    timeout=15s >> max retry wait ~1.5s), so voice_tts never sees a 500 for
    a transient OOM and never falls through to wyoming-piper.
    """
    import time
    import torch

    def _run_inference():
        torch.cuda.empty_cache()
        chunks: list = []
        for result in _pipeline(text, voice=voice, speed=speed):
            if result.audio is not None and result.audio.numel() > 0:
                chunks.append(result.audio.detach())
        if not chunks:
            raise RuntimeError("Kokoro produced no audio")
        return _pcm_to_wav(torch.cat(chunks))

    last_exc: Exception = RuntimeError("unknown")
    for attempt in range(1 + _MAX_OOM_RETRIES):
        try:
            return _run_inference()
        except RuntimeError as exc:
            if "Allocation" in str(exc) or "memory" in str(exc).lower():
                last_exc = exc
                logger.warning(
                    "CUDA memory pressure (attempt %d/%d): %s",
                    attempt + 1, 1 + _MAX_OOM_RETRIES, exc,
                )
                torch.cuda.empty_cache()
                time.sleep(0.5)
            else:
                raise
    raise RuntimeError(f"CUDA OOM after {_MAX_OOM_RETRIES} retries") from last_exc


async def _run_synthesis(text: str, voice: str, speed: float = 1.0) -> bytes:
    """Async wrapper: runs blocking inference in thread pool under the lock."""
    loop = asyncio.get_event_loop()
    async with _pipeline_lock:
        return await loop.run_in_executor(None, _blocking_synthesize, text, voice, speed)


# ─── Routes ───────────────────────────────────────────────────────────────────

class SynthRequest(BaseModel):
    text: str
    voice: str = _VOICE
    speed: float = 1.0


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "voice": _VOICE,
        "device": _device,
        "pipeline_loaded": _pipeline is not None,
    }


@app.post("/synthesize")
async def synthesize(req: SynthRequest):
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")
    if _pipeline is None:
        raise HTTPException(status_code=503, detail="Kokoro model not loaded")

    voice = (req.voice or _VOICE).strip() or _VOICE

    # Fast path: serve pre-synthesised / previously-synthesised WAV instantly.
    cacheable = voice == _VOICE and req.speed == 1.0 and len(text) <= _CACHE_MAX_TEXT_LEN
    cache_key = text.lower()
    if cacheable:
        hit = _cache_get(cache_key)
        if hit is not None:
            return Response(content=hit, media_type="audio/wav", headers={"X-Cache": "hit"})

    try:
        wav_bytes = await _run_synthesis(text, voice, req.speed)
    except Exception as exc:
        logger.warning("Kokoro synthesis failed voice=%s: %s", voice, exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    # Populate the runtime cache so this phrase is instant next time.
    if cacheable:
        _cache_store(cache_key, wav_bytes)

    return Response(content=wav_bytes, media_type="audio/wav", headers={"X-Cache": "miss"})


# ─── Streaming synthesis ──────────────────────────────────────────────────────

_STREAM_MEDIA_TYPE = "audio/L16; rate=24000; channels=1"


def _float32_to_pcm16_le(samples) -> bytes:
    """Convert a float32 numpy array of PCM samples to signed 16-bit LE bytes."""
    import numpy as np
    arr = np.clip(np.asarray(samples, dtype=np.float32), -1.0, 1.0)
    return (arr * 32767.0).astype("<i2").tobytes()


def _wav_to_pcm16_le(wav_bytes: bytes) -> bytes:
    """Strip the WAV container, returning raw S16_LE mono 24kHz PCM frames."""
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        return wf.readframes(wf.getnframes())


@app.post("/synthesize_stream")
async def synthesize_stream(req: SynthRequest):
    """Stream raw S16_LE / 24000 Hz / mono PCM as Kokoro synthesises it, so the
    caller hears first audio long before the full reply is done. Playable via
    `aplay -f S16_LE -r 24000 -c 1 -`.

    Concurrency: synthesis runs in a producer task that holds ``_pipeline_lock``
    only for the duration of the model inference, feeding an unbounded queue; the
    response yields from the queue WITHOUT the lock. So a slow-reading client can
    never keep the pipeline locked — only the inference itself serialises against
    other /synthesize calls (the kokoro_onnx pipeline is not concurrency-safe).
    """
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")
    if _pipeline is None:
        raise HTTPException(status_code=503, detail="Kokoro model not loaded")
    voice = (req.voice or _VOICE).strip() or _VOICE
    speed = req.speed
    _DONE = object()

    async def _gen():
        queue: asyncio.Queue = asyncio.Queue()  # unbounded: producer never blocks on a slow client

        async def _produce():
            try:
                if _BACKEND == "onnx":
                    produced = False
                    async with _pipeline_lock:
                        async for samples, _sr in _pipeline.create_stream(
                            text, voice=voice, speed=speed, lang="en-us"
                        ):
                            if samples is not None and len(samples):
                                produced = True
                                queue.put_nowait(_float32_to_pcm16_le(samples))
                    if not produced:
                        queue.put_nowait(RuntimeError("Kokoro stream produced no audio"))
                else:
                    queue.put_nowait(_wav_to_pcm16_le(await _run_synthesis(text, voice, speed)))
            except Exception as exc:  # logged here, surfaced to the client generator below
                logger.warning("Kokoro stream synthesis failed voice=%s: %s", voice, exc)
                queue.put_nowait(exc)
            finally:
                queue.put_nowait(_DONE)

        task = asyncio.create_task(_produce())
        try:
            while True:
                item = await queue.get()
                if item is _DONE:
                    break
                if isinstance(item, Exception):
                    raise item
                yield item
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    return StreamingResponse(_gen(), media_type=_STREAM_MEDIA_TYPE)


# ─── Entry point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=_PORT, log_level="info")
