"""Server-side speaker embedding (resemblyzer) for /voice/enroll + /voice/identify.

Memory contract: resemblyzer pulls torch (~360 MB RSS measured on the Jetson,
2026-09-27), and speaker ID is RARE here — enrolment is a one-off and identify
normally arrives with a daemon-computed ``embedding_base64``. So nothing heavy
is imported at module import (importing this module, ``routers.voice_tts`` or
``main`` must never pull ``resemblyzer``/``torch``; pinned by
``tests/test_speaker_id_lazy_load.py``). The first embedding request loads the
encoder ONCE behind a lock and reuses it; before this it rebuilt
``VoiceEncoder()`` (weights read from disk) on every call.

The encoder is pinned to CPU. ``VoiceEncoder()`` defaults to CUDA whenever
``torch.cuda.is_available()`` — and the box's torch is a CUDA build — which
would open a CUDA context inside zoe-data, on the unified memory the live
brain and Kokoro depend on (NvMap is outside every cgroup guard). A 256-dim
LSTM embedding of a few seconds of audio does not need a GPU, and CPU matches
the voice daemon, which computes the same embedding on the Pi's CPU.
"""
import logging
import threading
from typing import Any, Optional


logger = logging.getLogger(__name__)

_ENCODER: Any = None
_ENCODER_LOCK = threading.Lock()


def _get_voice_encoder() -> Any:
    """Return the process-wide resemblyzer ``VoiceEncoder``, loading it on first use.

    Thread-safe double-checked singleton. Import/load errors propagate to the
    caller unchanged (``ImportError`` when resemblyzer is not installed) and are
    NOT cached, so installing the package later works without a restart —
    the same retry-every-call semantics the per-call construction had.
    """
    global _ENCODER
    encoder = _ENCODER
    if encoder is not None:
        return encoder
    with _ENCODER_LOCK:
        if _ENCODER is None:
            from resemblyzer import VoiceEncoder  # type: ignore

            _ENCODER = VoiceEncoder(device="cpu", verbose=False)
            logger.info("resemblyzer VoiceEncoder loaded (cpu, cached for process lifetime)")
        return _ENCODER


def _compute_resemblyzer_embedding(wav_path: str) -> Optional[bytes]:
    """Compute a 256-dim resemblyzer voice embedding from a WAV file.

    Returns raw float32 bytes or None if resemblyzer is not installed.
    """
    try:
        encoder = _get_voice_encoder()
        from resemblyzer import preprocess_wav  # type: ignore
        import numpy as np
        wav = preprocess_wav(wav_path)
        embedding = encoder.embed_utterance(wav)  # shape: (256,)
        return embedding.astype(np.float32).tobytes()
    except ImportError:
        logger.debug("resemblyzer not installed; speaker ID unavailable")
        return None
    except Exception as exc:
        logger.warning("resemblyzer embedding failed: %s", exc)
        return None


def _cosine_similarity(a: bytes, b: bytes) -> float:
    """Cosine similarity between two float32 byte blobs."""
    try:
        import numpy as np
        va = np.frombuffer(a, dtype=np.float32)
        vb = np.frombuffer(b, dtype=np.float32)
        na = np.linalg.norm(va)
        nb = np.linalg.norm(vb)
        if na == 0 or nb == 0:
            return 0.0
        return float(np.dot(va, vb) / (na * nb))
    except Exception:
        return 0.0
