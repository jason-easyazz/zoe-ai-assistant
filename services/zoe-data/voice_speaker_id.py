import logging
from typing import Optional


logger = logging.getLogger(__name__)


def _compute_resemblyzer_embedding(wav_path: str) -> Optional[bytes]:
    """Compute a 256-dim resemblyzer voice embedding from a WAV file.

    Returns raw float32 bytes or None if resemblyzer is not installed.
    """
    try:
        from resemblyzer import VoiceEncoder, preprocess_wav  # type: ignore
        import numpy as np
        encoder = VoiceEncoder()
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
