"""ONNX Runtime backend for the Kokoro TTS sidecar (B5.1).

Selected by ``ZOE_KOKORO_BACKEND=onnx`` in ``kokoro_sidecar.py``; the sidecar keeps
every endpoint, the phrase cache, the brain-health wait and the ``/health`` contract,
and only swaps the engine that turns text into samples. The default backend stays
``pytorch`` (KPipeline) — nothing changes unless the env var is set.

Same model, same voices: the graph is the kokoro-onnx re-export of ``hexgrad/Kokoro-82M``
v1.0 (the weights the live KPipeline loads) and ``voices-v1.0.bin`` carries the same
voice packs (``af_sky`` etc.).

Parity with KPipeline is deliberate, not incidental:

* **G2P** — ``misaki`` (the G2P KPipeline uses, with the same espeak fallback), so the
  model sees the SAME phoneme string for the same text. ``ZOE_KOKORO_ONNX_G2P=espeak``
  uses kokoro-onnx's own espeak-ng phonemizer instead (no spaCy, lighter, but it
  pronounces differently from the live voice).
* **Chunking** — KPipeline's ``en_tokenize`` / ``waterfall_last`` (≤510 phonemes per
  chunk, split at sentence then clause punctuation), ported below as pure functions.
* **Style row** — ``pack[len(ps) - 1]``, as ``KPipeline.infer``.
* **No trimming, no inserted pauses** — KPipeline concatenates raw chunk audio.

Execution providers: ``ZOE_KOKORO_ONNX_PROVIDER=cuda`` (default) asks for the CUDA EP
with a bounded arena; ORT silently falls back to CPU when the CUDA EP cannot initialise,
so the engine reports what the session ACTUALLY got (``device`` + ``degraded_reason``).
TensorRT is never requested (engine builds are slow and memory-hungry on the Orin).
"""
from __future__ import annotations

import io
import logging
import os
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Sequence

logger = logging.getLogger(__name__)

SAMPLE_RATE = 24000
MAX_PHONEMES = 510  # KPipeline / Kokoro context limit per chunk

_MODEL_DIR = Path.home() / "models" / "kokoro-onnx"
DEFAULT_MODEL = _MODEL_DIR / "kokoro-v1.0.fp16.onnx"
DEFAULT_VOICES = _MODEL_DIR / "voices-v1.0.bin"


# ─── Config ──────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class OnnxConfig:
    model_path: str
    voices_path: str
    provider: str  # "cuda" | "cpu"
    g2p: str  # "misaki" | "espeak"
    gpu_mem_limit_mb: int
    intra_op_threads: int


def config_from_env(env: dict | None = None) -> OnnxConfig:
    """Read the backend config from the environment (pure; unit-tested)."""
    env = os.environ if env is None else env

    def _choice(name: str, default: str, allowed: tuple[str, ...]) -> str:
        value = (env.get(name) or default).strip().lower()
        if value not in allowed:
            logger.warning("%s=%r not in %s — using %r", name, value, allowed, default)
            return default
        return value

    def _pos_int(name: str, default: int) -> int:
        try:
            value = int(str(env.get(name, default)).strip())
        except (TypeError, ValueError):
            return default
        return value if value > 0 else default

    return OnnxConfig(
        model_path=str(env.get("ZOE_KOKORO_ONNX_MODEL") or DEFAULT_MODEL),
        # Falls back to ZOE_KOKORO_VOICES — the NPZ zoe-data's voice catalogue lists —
        # so the engine can speak every voice the picker offers (incl. zoe_* blends).
        voices_path=str(env.get("ZOE_KOKORO_ONNX_VOICES") or env.get("ZOE_KOKORO_VOICES") or DEFAULT_VOICES),
        provider=_choice("ZOE_KOKORO_ONNX_PROVIDER", "cuda", ("cuda", "cpu")),
        g2p=_choice("ZOE_KOKORO_ONNX_G2P", "misaki", ("misaki", "espeak")),
        gpu_mem_limit_mb=_pos_int("ZOE_KOKORO_ONNX_GPU_MEM_LIMIT_MB", 1024),
        intra_op_threads=_pos_int("ZOE_KOKORO_ONNX_THREADS", 4),
    )


def build_providers(cfg: OnnxConfig) -> list:
    """ORT provider list for the config (pure; unit-tested).

    CUDA arena is capped and grows only by what is requested, and cuDNN conv search
    is HEURISTIC without the max-workspace grab: Kokoro's input length varies per
    request, and EXHAUSTIVE search re-benchmarks (with large workspaces) on every
    new shape — slow and exactly the kind of NvMap burst that starves the brain.
    """
    if cfg.provider == "cpu":
        return ["CPUExecutionProvider"]
    cuda_opts = {
        "device_id": 0,
        "arena_extend_strategy": "kSameAsRequested",
        "gpu_mem_limit": cfg.gpu_mem_limit_mb * 1024 * 1024,
        "cudnn_conv_algo_search": "HEURISTIC",
        "cudnn_conv_use_max_workspace": "0",
        "do_copy_in_default_stream": "1",
    }
    return [("CUDAExecutionProvider", cuda_opts), "CPUExecutionProvider"]


# ─── KPipeline-parity chunking (pure) ────────────────────────────────────────

def _tokens_to_ps(tokens: Sequence) -> str:
    return "".join(t.phonemes + (" " if t.whitespace else "") for t in tokens).strip()


def _waterfall_last(tokens: Sequence, next_count: int) -> int:
    """Port of ``KPipeline.waterfall_last``: where to cut an over-long chunk."""
    for marks in ("!.?…", ":;", ",—"):
        z = next((i for i, t in reversed(list(enumerate(tokens))) if t.phonemes in set(marks)), None)
        if z is None:
            continue
        z += 1
        if z < len(tokens) and tokens[z].phonemes in (")", "”"):
            z += 1
        if next_count - len(_tokens_to_ps(tokens[:z])) <= MAX_PHONEMES:
            return z
    return len(tokens)


def chunk_phonemes(tokens: Iterable) -> Iterator[str]:
    """Port of ``KPipeline.en_tokenize``: yield ≤510-phoneme chunks of misaki tokens.

    Tokens only need ``.phonemes`` (str | None) and ``.whitespace`` (str/bool).
    """
    tks: list = []
    pcount = 0
    for t in tokens:
        t.phonemes = "" if t.phonemes is None else t.phonemes
        next_ps = t.phonemes + (" " if t.whitespace else "")
        next_pcount = pcount + len(next_ps.rstrip())
        if next_pcount > MAX_PHONEMES:
            z = _waterfall_last(tks, next_pcount)
            ps = _tokens_to_ps(tks[:z])
            if ps:
                yield ps
            tks = tks[z:]
            pcount = len(_tokens_to_ps(tks))
            if not tks:
                next_ps = next_ps.lstrip()
        tks.append(t)
        pcount += len(next_ps)
    if tks:
        ps = _tokens_to_ps(tks)
        if ps:
            yield ps


# ─── Audio helpers (pure) ────────────────────────────────────────────────────

def samples_to_wav(samples, sample_rate: int = SAMPLE_RATE) -> bytes:
    """float32 PCM in [-1, 1] → 16-bit mono WAV, same scaling as the PyTorch path
    (clamp, ×32767, truncate toward zero)."""
    import numpy as np

    arr = np.clip(np.asarray(samples, dtype=np.float32).reshape(-1), -1.0, 1.0)
    pcm = (arr * 32767.0).astype("<i2").tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    return buf.getvalue()


def resolve_voice_pack(voices, voice: str):
    """Voice pack for ``voice``; a comma list averages packs like ``KPipeline.load_voice``."""
    import numpy as np

    names = [v.strip() for v in voice.split(",") if v.strip()]
    if not names:
        raise ValueError("empty voice")
    missing = [n for n in names if n not in voices]
    if missing:
        raise ValueError(f"Voice {missing[0]} not found in available voices")
    if len(names) == 1:
        return voices[names[0]]
    return np.mean(np.stack([voices[n] for n in names]), axis=0)


# ─── Engine ──────────────────────────────────────────────────────────────────

class OnnxKokoroEngine:
    """Loaded ORT session + voices + G2P. ``synthesize_wav`` is the sidecar's hook."""

    def __init__(self, cfg: OnnxConfig):
        import onnxruntime as ort
        from kokoro_onnx import Kokoro

        self.cfg = cfg
        so = ort.SessionOptions()
        so.intra_op_num_threads = cfg.intra_op_threads
        so.log_severity_level = 3  # errors only; ORT warns per-node on CPU fallback
        session = ort.InferenceSession(cfg.model_path, sess_options=so, providers=build_providers(cfg))
        self.providers = session.get_providers()
        self.device = "cuda" if "CUDAExecutionProvider" in self.providers else "cpu"
        self.degraded_reason: str | None = None
        if cfg.provider == "cuda" and self.device != "cuda":
            self.degraded_reason = f"ONNX CUDA EP unavailable (session providers={self.providers})"
        self._kokoro = Kokoro.from_session(session, cfg.voices_path)
        self._g2p = self._load_misaki() if cfg.g2p == "misaki" else None

    @staticmethod
    def _load_misaki():
        # The exact G2P KPipeline(lang_code="a") builds, without importing torch.
        from misaki import en, espeak

        try:
            fallback = espeak.EspeakFallback(british=False)
        except Exception as exc:  # KPipeline tolerates a missing fallback too
            logger.warning("misaki espeak fallback unavailable: %s", exc)
            fallback = None
        return en.G2P(trf=False, british=False, fallback=fallback, unk="")

    @property
    def label(self) -> str:
        return f"onnx/{self.device} ({Path(self.cfg.model_path).name}, g2p={self.cfg.g2p})"

    def synthesize_samples(self, text: str, voice: str, speed: float = 1.0):
        import numpy as np

        pack = resolve_voice_pack(self._kokoro.voices, voice)
        common = dict(voice=pack, speed=speed, trim=False, sentence_pause=0.0, clause_pause=0.0)
        if self._g2p is None:
            # kokoro-onnx's own espeak-ng phonemizer + ≤510 splitting.
            audio, _ = self._kokoro.create(text, lang="en-us", **common)
            parts = [audio] if audio is not None and len(audio) else []
        else:
            # KPipeline parity: split on newlines, misaki G2P, en_tokenize chunks, and
            # the style row pack[len(ps) - 1] (create() picks it from the token count,
            # which equals len(ps) because misaki only emits in-vocabulary phonemes).
            parts = []
            for segment in (s for s in text.strip().split("\n") if s.strip()):
                _, tokens = self._g2p(segment)
                for ps in chunk_phonemes(tokens):
                    audio, _ = self._kokoro.create(ps[:MAX_PHONEMES], is_phonemes=True, **common)
                    if audio is not None and len(audio):
                        parts.append(audio)
        if not parts:
            raise RuntimeError("Kokoro produced no audio")
        return np.concatenate(parts)

    def synthesize_wav(self, text: str, voice: str, speed: float = 1.0) -> bytes:
        return samples_to_wav(self.synthesize_samples(text, voice, speed))


def load_engine(env: dict | None = None) -> OnnxKokoroEngine:
    cfg = config_from_env(env)
    logger.info(
        "Loading Kokoro ONNX (model=%s voices=%s provider=%s g2p=%s gpu_mem_limit=%dMB)…",
        cfg.model_path, cfg.voices_path, cfg.provider, cfg.g2p, cfg.gpu_mem_limit_mb,
    )
    engine = OnnxKokoroEngine(cfg)
    logger.info("Kokoro ONNX ready: %s providers=%s", engine.label, engine.providers)
    return engine
