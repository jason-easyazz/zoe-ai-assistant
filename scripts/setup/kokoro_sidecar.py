"""
Kokoro TTS sidecar — keeps the Kokoro PyTorch model warm in GPU memory.

Exposes a minimal HTTP API on ZOE_KOKORO_SIDECAR_PORT (default 10201):
  POST /synthesize  {"text": "...", "voice": "af_sky"}  → WAV audio bytes
  GET  /health                                          → {"status": "ok"}

Jetson CUDA notes:
  - PyTorch 2.8 is compiled against NumPy 1.x; NumPy 2.x is installed.
    All audio conversion uses struct.pack (no numpy) to avoid the
    "Numpy is not available" RuntimeError at inference time.
  - Jetson's nvgpu does not support all NVML memory queries, triggering an
    internal assertion in PyTorch's default CUDACachingAllocator. The fix is
    PYTORCH_CUDA_ALLOC_CONF=backend:cudaMallocAsync (set before torch import),
    which uses CUDA's built-in async allocator and avoids NVML calls entirely.
  - Falls back to CPU automatically if CUDA still fails; CPU inference is
    ~5s which is unusable for voice — a restart will re-attempt CUDA.
"""
import io
import logging
import os
import struct
import wave

# ── Must be set before torch import to bypass Jetson NVML assertion ──────────
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "backend:cudaMallocAsync")

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_VOICE = os.environ.get("KOKORO_VOICE", "af_sky").strip() or "af_sky"
_PORT = int(os.environ.get("KOKORO_SIDECAR_PORT", "10201"))
_SAMPLE_RATE = 24000  # Kokoro PyTorch output sample rate

app = FastAPI(title="Kokoro TTS Sidecar")

# ─── Model loading ────────────────────────────────────────────────────────────

_pipeline = None
_device = "cpu"


def _load_pipeline():
    global _pipeline, _device
    if _pipeline is not None:
        return _pipeline

    import torch
    from kokoro import KPipeline

    device = "cuda" if torch.cuda.is_available() else "cpu"
    logger.info("Loading Kokoro KPipeline (lang=a device=%s)…", device)
    try:
        _pipeline = KPipeline(lang_code="a", device=device)
        _device = device
        logger.info("Kokoro pipeline ready on %s", device)
    except Exception as exc:
        logger.warning("CUDA load failed (%s), falling back to CPU", exc)
        _pipeline = KPipeline(lang_code="a", device="cpu")
        _device = "cpu"
        logger.info("Kokoro pipeline ready on cpu (fallback)")
    return _pipeline


# Load at startup — keeps model in GPU memory.
try:
    _load_pipeline()
except Exception as exc:
    logger.error("Kokoro pipeline load failed at startup: %s — will retry on request", exc)


# ─── Audio helpers ────────────────────────────────────────────────────────────

def _tensor_to_wav(audio_tensor) -> bytes:
    """Convert a torch.FloatTensor of PCM samples to WAV bytes.

    Avoids numpy entirely because PyTorch 2.8 is compiled against NumPy 1.x
    but NumPy 2.x is installed — tensor.numpy() raises 'Numpy is not available'.
    Using struct.pack (pure Python) is slightly slower but always correct.
    """
    import torch
    # Clamp, scale to int16, move to CPU, flatten
    audio_int16 = (
        audio_tensor.detach().float().clamp(-1.0, 1.0)
        .mul(32767)
        .to(torch.int16)
        .cpu()
        .reshape(-1)
    )
    n = audio_int16.shape[0]
    # struct.pack with little-endian signed shorts — no numpy needed
    pcm_bytes = struct.pack(f"<{n}h", *audio_int16.tolist())

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(_SAMPLE_RATE)
        wf.writeframes(pcm_bytes)
    return buf.getvalue()


# ─── Routes ───────────────────────────────────────────────────────────────────

class SynthRequest(BaseModel):
    text: str
    voice: str = _VOICE
    speed: float = 1.0


@app.get("/health")
def health():
    return {
        "status": "ok",
        "voice": _VOICE,
        "device": _device,
        "pipeline_loaded": _pipeline is not None,
    }


@app.post("/synthesize")
def synthesize(req: SynthRequest):
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="text is required")

    try:
        pipeline = _load_pipeline()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Pipeline unavailable: {exc}")

    voice = (req.voice or _VOICE).strip() or _VOICE
    import torch

    try:
        chunks: list = []
        for result in pipeline(text, voice=voice, speed=req.speed):
            if result.audio is not None and result.audio.numel() > 0:
                chunks.append(result.audio.detach())

        if not chunks:
            raise HTTPException(status_code=500, detail="No audio generated")

        combined = torch.cat(chunks)
        wav_bytes = _tensor_to_wav(combined)
        return Response(content=wav_bytes, media_type="audio/wav")

    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Synthesis failed voice=%s: %s", voice, exc)
        raise HTTPException(status_code=500, detail=str(exc))


# ─── Entry point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=_PORT, log_level="info")
