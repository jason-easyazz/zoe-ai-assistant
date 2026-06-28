"""The wake word must warm Moonshine STT (not just the brain), so the FIRST command
after idle isn't decoded on a cold/swapped-out STT. See _prewarm_stt_on_wake."""
import asyncio

from routers import voice_tts


def test_prewarm_runs_a_dummy_inference(monkeypatch):
    calls = {"ensure": 0, "transcribe": 0}

    class _FakeTr:
        def transcribe_without_streaming(self, audio, sr):
            calls["transcribe"] += 1
            assert sr == 16000 and len(audio) > 0  # a real (silent) buffer, warm path
            return object()

    def _fake_ensure():
        calls["ensure"] += 1
        return _FakeTr()

    monkeypatch.setattr(voice_tts, "_ensure_moonshine", _fake_ensure)
    monkeypatch.setenv("ZOE_STT_BACKEND", "moonshine")
    monkeypatch.setenv("ZOE_STT_PREWARM_ON_WAKE", "1")

    asyncio.run(voice_tts._prewarm_stt_on_wake())
    assert calls["ensure"] == 1 and calls["transcribe"] == 1


def test_prewarm_respects_disable_flag(monkeypatch):
    monkeypatch.setattr(voice_tts, "_ensure_moonshine",
                        lambda: (_ for _ in ()).throw(AssertionError("must not warm when disabled")))
    monkeypatch.setenv("ZOE_STT_PREWARM_ON_WAKE", "0")
    asyncio.run(voice_tts._prewarm_stt_on_wake())  # no-op, no exception


def test_prewarm_skips_non_moonshine_backend(monkeypatch):
    monkeypatch.setattr(voice_tts, "_ensure_moonshine",
                        lambda: (_ for _ in ()).throw(AssertionError("only Moonshine is warmed")))
    monkeypatch.setenv("ZOE_STT_PREWARM_ON_WAKE", "1")
    monkeypatch.setenv("ZOE_STT_BACKEND", "whisper.cpp")
    asyncio.run(voice_tts._prewarm_stt_on_wake())  # no-op


def test_prewarm_never_raises_on_failure(monkeypatch):
    def _boom():
        raise RuntimeError("model load failed")
    monkeypatch.setattr(voice_tts, "_ensure_moonshine", _boom)
    monkeypatch.setenv("ZOE_STT_PREWARM_ON_WAKE", "1")
    monkeypatch.setenv("ZOE_STT_BACKEND", "moonshine")
    asyncio.run(voice_tts._prewarm_stt_on_wake())  # swallowed — wake ack must never break
