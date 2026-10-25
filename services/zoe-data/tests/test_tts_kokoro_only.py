"""Kokoro is Zoe's ONLY voice by default (owner decision 2026-10-21).

With ZOE_TTS_MODE unset, a Kokoro failure must NOT fall back to the local sidecar,
Edge TTS (which sends reply text to Microsoft) or espeak-ng: /synthesize 503s and
/stream yields its per-chunk error. The negative control proves the recorders can
see a fallback call: with ZOE_TTS_MODE=hybrid set explicitly, Edge IS attempted.
"""

import pytest

pytestmark = pytest.mark.ci_safe  # GitHub-CI opt-in: runs in validate.yml's `-m ci_safe` lane

import asyncio
import json

from fastapi import HTTPException

import routers.voice_tts as vt

CANNED = b"RIFFfake-wav"
FALLBACKS = ("local_service", "edge", "espeak")


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _patch_providers(monkeypatch, calls, *, kokoro_ok, local_ok=True):
    """Record every provider call; fallbacks 'succeed' if reached, so reaching one
    would visibly change the result."""

    def recorder(name, result):
        async def _fake(*args, **kwargs):
            calls.append(name)
            return result
        return _fake

    monkeypatch.setattr(vt, "_synthesize_kokoro_sidecar", recorder("kokoro", CANNED if kokoro_ok else None))
    monkeypatch.setattr(vt, "_synthesize_local_service", recorder("local_service", CANNED if local_ok else None))
    monkeypatch.setattr(vt, "_synthesize_edge_tts", recorder("edge", CANNED))
    monkeypatch.setattr(vt, "_synthesize_espeak", recorder("espeak", CANNED))


def _stream_lines(payload):
    async def _collect():
        resp = await vt.voice_stream(payload, caller={})
        out = []
        async for chunk in resp.body_iterator:
            out.append(chunk if isinstance(chunk, bytes) else chunk.encode())
        return b"".join(out)

    raw = _run(_collect())
    return [line for line in raw.decode().splitlines() if line.strip()]


@pytest.fixture
def default_mode(monkeypatch):
    monkeypatch.delenv("ZOE_TTS_MODE", raising=False)
    # Would enable the local-service step under hybrid; must still be skipped.
    monkeypatch.setenv("ZOE_LOCAL_TTS_URL", "http://127.0.0.1:1")


def test_default_mode_is_kokoro(default_mode):
    assert vt.tts_mode() == vt.TTS_MODE_KOKORO == "kokoro"


HELLO = dict(text="hello there")
TWO_SENTENCES = dict(text="Hello there. How are you today?")


def test_synthesize_default_kokoro_failure_503_and_no_fallback(monkeypatch, default_mode):
    calls: list[str] = []
    _patch_providers(monkeypatch, calls, kokoro_ok=False)

    with pytest.raises(HTTPException) as exc:
        _run(vt.synthesize(dict(HELLO), caller=dict()))

    assert exc.value.status_code == 503
    assert exc.value.detail == "No speech provider available"
    assert calls == ["kokoro"], calls
    assert not any(f in calls for f in FALLBACKS), calls


def test_synthesize_default_kokoro_ok(monkeypatch, default_mode):
    calls: list[str] = []
    _patch_providers(monkeypatch, calls, kokoro_ok=True)

    resp = _run(vt.synthesize(dict(HELLO), caller=dict()))

    assert resp.headers.get("X-Zoe-TTS-Provider") == "kokoro-sidecar"
    assert resp.body == CANNED
    assert calls == ["kokoro"], calls


def test_stream_default_kokoro_failure_errors_and_no_fallback(monkeypatch, default_mode):
    calls: list[str] = []
    _patch_providers(monkeypatch, calls, kokoro_ok=False)

    objs = [json.loads(line) for line in _stream_lines(dict(TWO_SENTENCES))]

    assert len(objs) == 2, objs
    assert all(o.get("error") == "all providers failed" for o in objs), objs
    assert calls == ["kokoro", "kokoro"], calls


def test_stream_default_kokoro_ok(monkeypatch, default_mode):
    calls: list[str] = []
    _patch_providers(monkeypatch, calls, kokoro_ok=True)

    lines = _stream_lines(dict(TWO_SENTENCES))

    headers = [json.loads(line) for line in lines if line.startswith("{")]
    assert len(headers) == 2, headers
    assert all(h.get("provider") == "kokoro-sidecar" for h in headers), headers
    assert calls == ["kokoro", "kokoro"], calls


# -- Negative control: the recorders DO see a fallback when one is configured --
def test_negative_control_hybrid_synthesize_attempts_edge(monkeypatch):
    monkeypatch.setenv("ZOE_TTS_MODE", "hybrid")
    monkeypatch.delenv("ZOE_LOCAL_TTS_URL", raising=False)
    calls: list[str] = []
    _patch_providers(monkeypatch, calls, kokoro_ok=False, local_ok=False)

    resp = _run(vt.synthesize(dict(HELLO), caller=dict()))

    assert "edge" in calls, calls
    assert resp.headers.get("X-Zoe-TTS-Provider") == "edge-tts"


def test_negative_control_hybrid_stream_attempts_edge(monkeypatch):
    monkeypatch.setenv("ZOE_TTS_MODE", "hybrid")
    monkeypatch.delenv("ZOE_LOCAL_TTS_URL", raising=False)
    calls: list[str] = []
    _patch_providers(monkeypatch, calls, kokoro_ok=False)

    lines = _stream_lines(dict(text="Hello there."))

    assert "edge" in calls, calls
    assert json.loads(lines[0]).get("provider") == "edge-tts"


# -- Readiness: default mode = engine kokoro, the sidecar is the only check --
class _Resp:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def _client(get_impl, seen):
    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url, *args, **kwargs):
            seen.append(url)
            return get_impl()

    return _Client


def test_readiness_default_kokoro_checks_sidecar(monkeypatch):
    import main

    monkeypatch.delenv("ZOE_TTS_MODE", raising=False)
    seen: list[str] = []
    ok_resp = _Resp(200, dict(pipeline_loaded=True))
    monkeypatch.setattr(main.httpx, "AsyncClient", _client(lambda: ok_resp, seen))

    report = asyncio.run(main._check_tts_ready())

    assert report["engine"] == "kokoro"
    assert report["mode"] == "kokoro"
    assert report["ok"] is True
    assert report["provider"] == "kokoro-sidecar"
    assert seen and seen[0].endswith("/health"), seen


def test_readiness_default_kokoro_down_is_not_ready_even_with_fallbacks(monkeypatch):
    import main
    from routers import voice_tts

    def _down():
        raise OSError("sidecar down")

    monkeypatch.delenv("ZOE_TTS_MODE", raising=False)
    seen: list[str] = []
    monkeypatch.setattr(main.httpx, "AsyncClient", _client(_down, seen))
    monkeypatch.setattr(voice_tts, "_has_espeak_ng", lambda: True)
    monkeypatch.setattr(voice_tts, "edge_tts_available", lambda: True)

    report = asyncio.run(main._check_tts_ready())

    assert seen, "sidecar was not checked"
    assert report["engine"] == "kokoro"
    assert report["ok"] is False
    assert "provider" not in report
