"""Unit tests for the Kokoro ONNX Runtime backend (B5.1) and its sidecar wiring.

Pure logic — no model load, no onnxruntime, no CUDA, no network. The backend module
lazy-imports onnxruntime/kokoro_onnx/misaki inside the engine, and the sidecar's
top-level imports are slim-dep-green (see test_kokoro_cache.py), so both import on
the GitHub runner. numpy (installed in the unit lane) is needed only by the WAV /
voice-pack helpers and is importorskip'd there.

What these lock in:
- the default backend stays ``pytorch`` — the ONNX engine is strictly opt-in;
- ``/health`` keeps its contract (status/voice/device/pipeline_loaded, degraded on
  fallback) and names the backend;
- ``/synthesize`` and ``/synthesize_stream`` return the same shapes (audio/wav with
  X-Cache; raw S16_LE 24 kHz PCM) when the engine is ONNX;
- the CUDA provider list is bounded (arena cap, no EXHAUSTIVE conv search, no TRT);
- the KPipeline chunking port keeps chunks ≤510 phonemes and cuts at punctuation.
"""
import asyncio
import importlib.util
import io
import pathlib
import sys
import wave
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.ci_safe

_SETUP = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "setup"


def _load(name: str, filename: str, env: dict | None = None, monkeypatch=None):
    if env and monkeypatch:
        for k, v in env.items():
            monkeypatch.setenv(k, v)
    spec = importlib.util.spec_from_file_location(name, _SETUP / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def kb():
    sys.path.insert(0, str(_SETUP))
    try:
        yield importlib.import_module("kokoro_onnx_backend")
    finally:
        sys.path.remove(str(_SETUP))


# ─── config / providers ──────────────────────────────────────────────────────

def test_config_defaults(kb):
    cfg = kb.config_from_env({})
    assert cfg.provider == "cuda"
    assert cfg.g2p == "misaki"  # KPipeline-parity G2P by default
    assert cfg.model_path.endswith("kokoro-v1.0.fp16.onnx")
    assert cfg.voices_path.endswith("voices-v1.0.bin")
    assert cfg.gpu_mem_limit_mb == 1024


def test_voices_fall_back_to_the_catalogue_bin(kb):
    assert kb.config_from_env({"ZOE_KOKORO_VOICES": "/v/cat.bin"}).voices_path == "/v/cat.bin"
    assert kb.config_from_env({"ZOE_KOKORO_VOICES": "/v/cat.bin",
                               "ZOE_KOKORO_ONNX_VOICES": "/v/onnx.bin"}).voices_path == "/v/onnx.bin"


def test_config_rejects_garbage(kb):
    cfg = kb.config_from_env({
        "ZOE_KOKORO_ONNX_PROVIDER": "tensorrt",
        "ZOE_KOKORO_ONNX_G2P": "bogus",
        "ZOE_KOKORO_ONNX_GPU_MEM_LIMIT_MB": "-5",
        "ZOE_KOKORO_ONNX_THREADS": "x",
    })
    assert (cfg.provider, cfg.g2p, cfg.gpu_mem_limit_mb, cfg.intra_op_threads) == ("cuda", "misaki", 1024, 4)


def test_cpu_provider_is_cpu_only(kb):
    assert kb.build_providers(kb.config_from_env({"ZOE_KOKORO_ONNX_PROVIDER": "CPU"})) == ["CPUExecutionProvider"]


def test_cuda_provider_is_bounded_and_never_tensorrt(kb):
    providers = kb.build_providers(kb.config_from_env({"ZOE_KOKORO_ONNX_GPU_MEM_LIMIT_MB": "512"}))
    names = [p[0] if isinstance(p, tuple) else p for p in providers]
    assert names == ["CUDAExecutionProvider", "CPUExecutionProvider"]
    opts = providers[0][1]
    assert opts["gpu_mem_limit"] == 512 * 1024 * 1024
    assert opts["arena_extend_strategy"] == "kSameAsRequested"
    assert opts["cudnn_conv_algo_search"] != "EXHAUSTIVE"
    assert "TensorrtExecutionProvider" not in names


# ─── KPipeline chunking port ─────────────────────────────────────────────────

def _tok(ps, ws=True):
    return SimpleNamespace(phonemes=ps, whitespace=" " if ws else "")


def test_chunk_short_text_is_one_chunk(kb):
    tokens = [_tok("hˈɛlO"), _tok("wˈɜɹld", ws=False), _tok(".", ws=False)]
    assert list(kb.chunk_phonemes(tokens)) == ["hˈɛlO wˈɜɹld."]


def test_chunk_none_phonemes_are_blank(kb):
    assert list(kb.chunk_phonemes([_tok(None), _tok("ˈA", ws=False)])) == ["ˈA"]


def test_chunk_long_text_splits_at_sentence_mark_under_limit(kb):
    word = "wˈɜɹd"  # 5 phonemes + space
    tokens = []
    for _ in range(12):
        tokens += [_tok(word) for _ in range(9)] + [_tok(word, ws=False), _tok(".")]
    chunks = list(kb.chunk_phonemes(tokens))
    assert len(chunks) >= 2
    assert all(len(c) <= kb.MAX_PHONEMES for c in chunks)
    assert all(c.endswith(".") for c in chunks[:-1]), "should cut after a sentence mark"
    assert "".join(chunks).replace(" ", "") == "".join(t.phonemes for t in tokens).replace(" ", "")


# ─── audio / voices ──────────────────────────────────────────────────────────

def test_samples_to_wav_matches_pytorch_scaling(kb):
    np = pytest.importorskip("numpy")
    wav = kb.samples_to_wav(np.array([0.0, 0.5, -0.5, 2.0, -2.0], dtype=np.float32))
    with wave.open(io.BytesIO(wav)) as wf:
        assert (wf.getnchannels(), wf.getsampwidth(), wf.getframerate()) == (1, 2, 24000)
        pcm = np.frombuffer(wf.readframes(wf.getnframes()), dtype="<i2")
    # clamp to [-1, 1], x32767, truncate toward zero — as the torch path does.
    assert pcm.tolist() == [0, 16383, -16383, 32767, -32767]


def test_resolve_voice_pack_single_blend_and_missing(kb):
    np = pytest.importorskip("numpy")
    voices = {"af_sky": np.ones((510, 1, 256), np.float32), "af_bella": np.zeros((510, 1, 256), np.float32)}
    assert kb.resolve_voice_pack(voices, "af_sky") is voices["af_sky"]
    assert float(kb.resolve_voice_pack(voices, "af_sky, af_bella").mean()) == pytest.approx(0.5)
    with pytest.raises(ValueError):
        kb.resolve_voice_pack(voices, "zz_nope")


# ─── sidecar wiring ──────────────────────────────────────────────────────────

class _FakeEngine:
    device = "cuda"
    degraded_reason = None

    def __init__(self):
        self.calls = []

    def synthesize_wav(self, text, voice, speed):
        self.calls.append((text, voice, speed))
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(24000)
            wf.writeframes(b"\x01\x00" * 240)
        return buf.getvalue()


def test_default_backend_is_pytorch(monkeypatch):
    monkeypatch.delenv("ZOE_KOKORO_BACKEND", raising=False)
    side = _load("kokoro_sidecar_default_t", "kokoro_sidecar.py")
    assert side._BACKEND == "pytorch"
    monkeypatch.setenv("ZOE_KOKORO_BACKEND", "tensorrt")
    assert _load("kokoro_sidecar_bad_t", "kokoro_sidecar.py")._BACKEND == "pytorch"


@pytest.fixture
def onnx_sidecar(monkeypatch, tmp_path):
    side = _load(
        "kokoro_sidecar_onnx_t", "kokoro_sidecar.py",
        env={"ZOE_KOKORO_BACKEND": "onnx", "ZOE_KOKORO_CACHE_DIR": str(tmp_path)},
        monkeypatch=monkeypatch,
    )
    engine = _FakeEngine()
    side._pipeline = engine
    side._device = "cuda"
    return side, engine


def test_onnx_health_keeps_contract(onnx_sidecar):
    side, _ = onnx_sidecar
    body = asyncio.run(side.health())
    assert body == {"status": "ok", "voice": side._VOICE, "device": "cuda",
                    "backend": "onnx", "pipeline_loaded": True}
    side._degraded_reason = "ONNX CUDA EP unavailable"
    body = asyncio.run(side.health())
    assert body["degraded"] is True and "CUDA" in body["degraded_reason"]


def test_onnx_synthesize_returns_wav_and_caches(onnx_sidecar):
    side, engine = onnx_sidecar
    req = side.SynthRequest(text="Lights are on.")
    first = asyncio.run(side.synthesize(req))
    assert first.media_type == "audio/wav" and first.headers["X-Cache"] == "miss"
    assert first.body[:4] == b"RIFF"
    second = asyncio.run(side.synthesize(req))
    assert second.headers["X-Cache"] == "hit" and second.body == first.body
    assert engine.calls == [("Lights are on.", side._VOICE, 1.0)]


def test_onnx_stream_is_raw_pcm(onnx_sidecar):
    side, engine = onnx_sidecar

    async def _collect():
        resp = await side.synthesize_stream(side.SynthRequest(text="Streaming now.", speed=1.1))
        assert resp.media_type == "audio/L16; rate=24000; channels=1"
        return b"".join([chunk async for chunk in resp.body_iterator])

    pcm = asyncio.run(_collect())
    assert pcm == b"\x01\x00" * 240  # WAV container stripped
    assert engine.calls[-1] == ("Streaming now.", side._VOICE, 1.1)
