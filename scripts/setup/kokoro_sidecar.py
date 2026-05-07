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
import io
import logging
import os
import struct
import wave
from contextlib import asynccontextmanager

# Must be set before torch import to bypass Jetson NVML assertion.
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "backend:cudaMallocAsync")

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
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

# ─── Global state ─────────────────────────────────────────────────────────────

_pipeline = None
_device = "cpu"
_pipeline_lock = asyncio.Lock()  # serialise inference; pipeline is not thread-safe


# ─── Pipeline loading ─────────────────────────────────────────────────────────

def _load_pipeline():
    """Load and return the Kokoro pipeline (blocking; run once in thread pool)."""
    global _device
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

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _pipeline
    loop = asyncio.get_event_loop()
    try:
        _pipeline = await loop.run_in_executor(None, _load_pipeline)
        # One warmup synthesis compiles the CUDA graph so the first real
        # request doesn't pay the ~500ms JIT compilation cost.
        logger.info("Warming up Kokoro CUDA graph…")
        await _run_synthesis("Zoe is ready.", _VOICE, speed=1.0)
        logger.info("Kokoro sidecar ready on port %d (device=%s).", _PORT, _device)
    except Exception as exc:
        logger.error("Failed to initialise Kokoro: %s", exc)
        raise
    yield
    logger.info("Kokoro sidecar shutting down.")


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


def _blocking_synthesize(text: str, voice: str, speed: float) -> bytes:
    """Run Kokoro inference synchronously (called inside run_in_executor)."""
    import torch
    chunks: list = []
    for result in _pipeline(text, voice=voice, speed=speed):
        if result.audio is not None and result.audio.numel() > 0:
            chunks.append(result.audio.detach())
    if not chunks:
        raise RuntimeError("Kokoro produced no audio")
    return _pcm_to_wav(torch.cat(chunks))


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
    try:
        wav_bytes = await _run_synthesis(text, voice, req.speed)
    except Exception as exc:
        logger.warning("Kokoro synthesis failed voice=%s: %s", voice, exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return Response(content=wav_bytes, media_type="audio/wav")


# ─── Entry point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=_PORT, log_level="info")
