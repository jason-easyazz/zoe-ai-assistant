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


# Why the last embedding returned None, for the route's error detail ("" after a success). A short string, never a path to a secret.
_LAST_FAILURE: str = ""
# Set once the in-process import failed for an ENVIRONMENT reason (the dynamic linker, not a missing package): later calls go straight
# to the clean-environment subprocess instead of re-importing a half-loaded torch each time.
_INPROC_BROKEN: bool = False
_EMBED_DIM = 256
_SUBPROCESS_TIMEOUT_S = 180
# Directories the dynamic linker searches anyway, AFTER the wheel's own RUNPATH. Putting one in LD_LIBRARY_PATH reverses that order, so the
# distro's libblas/libopenblas shadow the BLAS bundled inside the torch wheel.
_SYSTEM_LIB_DIRS = frozenset({"/lib", "/lib64", "/usr/lib", "/usr/lib64", "/usr/lib/aarch64-linux-gnu", "/usr/lib/x86_64-linux-gnu",
                              "/lib/aarch64-linux-gnu", "/lib/x86_64-linux-gnu"})
_CLEAN_SCRIPT = (
    "import sys, base64\n"
    "from resemblyzer import VoiceEncoder, preprocess_wav\n"
    "import numpy as np\n"
    "enc = VoiceEncoder(device='cpu', verbose=False)\n"
    "emb = enc.embed_utterance(preprocess_wav(sys.argv[1]))\n"
    "sys.stdout.write(base64.b64encode(emb.astype(np.float32).tobytes()).decode())\n"
)


def last_failure() -> str:
    """Why the most recent embedding request returned None ("" if it did not). For an actionable error, not for control flow."""
    return _LAST_FAILURE


def _set_failure(reason: str) -> None:
    global _LAST_FAILURE
    _LAST_FAILURE = (reason or "")[:300]


def clean_library_env(env: Optional[dict] = None) -> dict:
    """``env`` (default: this process's) with the distro library directories removed from ``LD_LIBRARY_PATH``.

    The live unit sets ``LD_LIBRARY_PATH=/home/zoe/.local/lib:/usr/local/cuda-12.6/lib64:/usr/lib/aarch64-linux-gnu``; the last entry
    makes the linker prefer the system ``libopenblas`` over the one inside the torch wheel, and ``import torch`` then dies with
    ``undefined symbol: sbgemm_`` (reproduced 2026-10-12 against the live process's environment). Those directories are searched by
    default anyway, so dropping them from the variable changes the ORDER only."""
    import os

    out = dict(os.environ if env is None else env)
    kept = [d for d in (out.get("LD_LIBRARY_PATH") or "").split(":") if d and d.rstrip("/") not in _SYSTEM_LIB_DIRS]
    if kept:
        out["LD_LIBRARY_PATH"] = ":".join(kept)
    else:
        out.pop("LD_LIBRARY_PATH", None)
    return out


def _embed_in_clean_subprocess(wav_path: str) -> Optional[bytes]:
    """The embedding computed by a child interpreter with ``clean_library_env``. None (with the reason recorded and logged at
    WARNING) when that fails too."""
    import base64
    import subprocess
    import sys
    import tempfile

    try:
        proc = subprocess.run(
            [sys.executable, "-c", _CLEAN_SCRIPT, wav_path], env=clean_library_env(), cwd=tempfile.gettempdir(),
            capture_output=True, text=True, timeout=_SUBPROCESS_TIMEOUT_S,
        )
    except Exception as exc:  # noqa: BLE001 - timeout, spawn failure
        _set_failure(f"clean-environment embedding could not run: {type(exc).__name__}")
        logger.warning("resemblyzer clean-environment embedding could not run: %s: %s", type(exc).__name__, exc)
        return None
    if proc.returncode != 0:
        tail = (proc.stderr.strip().splitlines() or ["no output"])[-1]
        _set_failure(f"clean-environment embedding failed: {tail}")
        logger.warning("resemblyzer clean-environment embedding failed (exit %s): %s", proc.returncode, tail)
        return None
    try:
        raw = base64.b64decode(proc.stdout.strip(), validate=True)
    except Exception:  # noqa: BLE001
        raw = b""
    if len(raw) != _EMBED_DIM * 4:
        _set_failure("clean-environment embedding returned an unexpected size")
        logger.warning("resemblyzer clean-environment embedding returned %d bytes, expected %d", len(raw), _EMBED_DIM * 4)
        return None
    _set_failure("")  # the in-process import failure was real, but the request succeeded: nothing to explain
    return raw


def _compute_resemblyzer_embedding(wav_path: str) -> Optional[bytes]:
    """Compute a 256-dim resemblyzer voice embedding from a WAV file.

    Returns raw float32 bytes, or None - and then ``last_failure()`` says why. A failure to IMPORT the engine is logged at WARNING
    with its exception type and message (it used to be a DEBUG line, so a broken install and a missing package were both invisible
    and the route's 503 read "not available" for 8 hours of enrolment attempts). An import that fails because of the process's
    ENVIRONMENT (``ImportError``/``OSError`` from the dynamic linker) falls back to a child interpreter with a clean library path.
    """
    global _INPROC_BROKEN
    _set_failure("")
    if _INPROC_BROKEN:
        return _embed_in_clean_subprocess(wav_path)
    try:
        encoder = _get_voice_encoder()
        from resemblyzer import preprocess_wav  # type: ignore
    except (ImportError, OSError) as exc:  # only the LOAD of the engine: a bad WAV below is not an environment problem
        _set_failure(f"speaker engine import failed: {type(exc).__name__}: {exc}")
        logger.warning("resemblyzer import failed in the zoe-data process (%s: %s); trying a clean-library-path child process",
                       type(exc).__name__, exc)
        if isinstance(exc, ModuleNotFoundError):
            return None  # genuinely not installed: a child cannot help, and installing it later still works without a restart
        _INPROC_BROKEN = True
        return _embed_in_clean_subprocess(wav_path)
    try:
        import numpy as np
        wav = preprocess_wav(wav_path)
        embedding = encoder.embed_utterance(wav)  # shape: (256,)
        return embedding.astype(np.float32).tobytes()
    except Exception as exc:
        _set_failure(f"embedding failed: {type(exc).__name__}: {exc}")
        logger.warning("resemblyzer embedding failed: %s: %s", type(exc).__name__, exc)
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
