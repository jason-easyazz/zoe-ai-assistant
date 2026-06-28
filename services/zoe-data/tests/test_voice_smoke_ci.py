"""GitHub-CI-safe voice smoke test — the live voice path with ZERO models/network.

Almost all of Zoe's voice/intent suite is Jetson-only (it loads Moonshine, Kokoro,
torch, the brain), so the GitHub-hosted runner had no coverage of the live voice
seams at all. This file closes that gap with a *behavioral* smoke test that runs on
the slim CI runner:

- It imports the REAL ``routers.voice_tts`` and ``intent_router`` modules. Both only
  pull slim deps at import time (fastapi / httpx / asyncpg); every heavy engine
  (moonshine_voice, kokoro_onnx, edge_tts, espeak-ng) is imported lazily *inside* a
  function, so we fake those without ever installing or loading a model.
- No DB pool, no network, no real sleeps. STT and every TTS provider are monkeypatched
  to canned values; the brain is never called (we drive the deterministic intent
  classifier directly).

Two invariants are guarded:
  (a) the /synthesize TTS waterfall ORDER — Kokoro (the rock) is attempted before the
      cloud Edge fallback, which is attempted before the espeak-ng last resort;
  (b) a basic STT -> intent -> TTS round-trip wires together end to end with fakes.

See test_canonical_invariants.py for the static rock-default guards; this file proves
the same rocks are wired in the live code path, not just declared.
"""
import asyncio

import pytest

import routers.voice_tts as vt
import intent_router


def _run(coro):
    """Drive a coroutine to completion without depending on a pytest-asyncio plugin
    or a session loop — a fresh loop per call keeps each smoke case isolated."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _patch_all_tts_providers(monkeypatch, order, *, succeed="espeak"):
    """Replace every TTS provider coroutine with a recorder.

    Each fake appends its short name to ``order`` when invoked. All return None
    (waterfall keeps falling through) except the one named ``succeed``, which
    returns canned WAV bytes so the route resolves and we observe the FULL chain.
    """
    CANNED = b"RIFFfake-wav"

    def recorder(name):
        async def _fake(*args, **kwargs):
            order.append(name)
            return CANNED if name == succeed else None
        return _fake

    # Names mirror the live waterfall in synthesize() (Kokoro-first since PR #872):
    #   Kokoro sidecar (GPU) -> Kokoro ONNX -> local override -> Edge (cloud) -> espeak
    monkeypatch.setattr(vt, "_synthesize_local_service", recorder("local_service"))
    monkeypatch.setattr(vt, "_synthesize_kokoro_sidecar", recorder("kokoro_sidecar"))
    monkeypatch.setattr(vt, "_synthesize_kokoro", recorder("kokoro_onnx"))
    monkeypatch.setattr(vt, "_synthesize_edge_tts", recorder("edge"))
    monkeypatch.setattr(vt, "_synthesize_espeak", recorder("espeak"))
    return CANNED


# ── (a) TTS waterfall ordering invariant ─────────────────────────────────────
def test_tts_waterfall_order_kokoro_before_edge_before_espeak(monkeypatch):
    """The live /synthesize waterfall must attempt providers in the canonical order.

    Kokoro is the primary rock and is tried FIRST (sidecar then ONNX, since PR #872);
    the optional ``ZOE_LOCAL_TTS_URL`` override sits after it, Edge TTS is the cloud
    fallback, and espeak-ng is the last resort. We let every provider miss so the route
    walks the entire chain, then assert the recorded order. (We clear ``ZOE_LOCAL_TTS_URL``
    to keep the run self-contained; the local-service step still records in-place when
    reached, proving it stays behind the rock, never ahead of it.)
    """
    monkeypatch.setenv("ZOE_TTS_MODE", "hybrid")  # hybrid exercises the full chain
    monkeypatch.delenv("ZOE_LOCAL_TTS_URL", raising=False)
    order: list[str] = []
    _patch_all_tts_providers(monkeypatch, order, succeed="espeak")

    resp = _run(vt.synthesize({"text": "hello there"}, caller={}))

    # Fell all the way through to the last resort → full chain was walked.
    assert resp.headers.get("X-Zoe-TTS-Provider") == "espeak-ng"
    # Kokoro is the primary rock and runs FIRST — ahead of the optional local sidecar
    # override (PR #872). espeak-ng stays the last resort.
    assert order == ["kokoro_sidecar", "kokoro_onnx", "local_service", "edge", "espeak"], order

    # The load-bearing sub-orderings (what actually keeps the rock the rock):
    assert order.index("kokoro_sidecar") < order.index("kokoro_onnx"), "GPU sidecar must precede ONNX fallback"
    assert max(order.index("kokoro_sidecar"), order.index("kokoro_onnx")) < order.index("edge"), "Kokoro before Edge"
    assert order.index("edge") < order.index("espeak"), "Edge before espeak"
    assert order[-1] == "espeak", "espeak-ng must be the final last-resort provider"


def test_tts_picks_kokoro_first_when_available(monkeypatch):
    """When the Kokoro sidecar answers, the waterfall stops there — the cloud/espeak
    fallbacks must NOT be reached. Guards against the rock being demoted below a fallback."""
    monkeypatch.setenv("ZOE_TTS_MODE", "hybrid")
    monkeypatch.delenv("ZOE_LOCAL_TTS_URL", raising=False)
    order: list[str] = []
    _patch_all_tts_providers(monkeypatch, order, succeed="kokoro_sidecar")

    resp = _run(vt.synthesize({"text": "what time is it"}, caller={}))

    assert resp.headers.get("X-Zoe-TTS-Provider") == "kokoro-sidecar"
    assert "edge" not in order and "espeak" not in order, f"fallbacks reached despite Kokoro success: {order}"
    assert order[-1] == "kokoro_sidecar"


# ── (b) STT -> intent -> TTS round-trip with fakes ───────────────────────────
def test_stt_intent_tts_round_trip(monkeypatch):
    """End-to-end voice seam on the slim runner: fake STT model, REAL deterministic
    intent classifier, fake TTS engine. No models, no brain, no network."""
    # 1) STT: fake the Moonshine backend so _transcribe_audio runs the real wrapper
    #    (wake-strip, backend tagging) without loading the ONNX model.
    async def _fake_moonshine(wav_path):
        return "what time is it"
    monkeypatch.setattr(vt, "_run_moonshine", _fake_moonshine)

    # Read the per-turn backend tag inside the SAME task context the transcribe ran in
    # (a ContextVar set in the task's context doesn't propagate back to the caller).
    async def _stt():
        text = await vt._transcribe_audio("/nonexistent.wav")
        return text, vt._stt_backend_var.get()

    transcript, backend = _run(_stt())
    assert transcript == "what time is it"
    # The real wrapper tags which backend produced the transcript → Moonshine, the rock.
    assert backend.startswith("moonshine:"), backend

    # 2) INTENT: the real, pure, deterministic classifier (no LLM) routes the transcript.
    intent = intent_router.detect_intent(transcript, log_miss=False)
    assert intent is not None and intent.name == "time_query", intent

    # 3) TTS: synthesize a canned reply through the real waterfall with a faked engine.
    monkeypatch.setenv("ZOE_TTS_MODE", "hybrid")
    monkeypatch.delenv("ZOE_LOCAL_TTS_URL", raising=False)
    order: list[str] = []
    canned = _patch_all_tts_providers(monkeypatch, order, succeed="kokoro_sidecar")

    reply = "It is half past noon."
    resp = _run(vt.synthesize({"text": reply}, caller={}))

    assert resp.body == canned
    assert resp.headers.get("X-Zoe-TTS-Provider") == "kokoro-sidecar"
    assert resp.media_type == "audio/wav"
