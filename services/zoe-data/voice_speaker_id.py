"""Speaker-ID vector mechanics for the server: cosine similarity of two float32 blobs.

The server computes no speaker embeddings. Resemblyzer (and with it torch, ~360 MB RSS when loaded) was retired in
2026-10: the panel daemon embeds with WeSpeaker ResNet34 (ONNX, see ``speaker_models.py``) and uploads either a finished
profile (``/api/voice/enroll``) or a query vector (``/api/voice/identify``). Numpy only.
"""
from __future__ import annotations


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
