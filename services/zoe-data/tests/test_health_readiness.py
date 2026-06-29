"""Liveness and readiness contracts for /health and /readyz."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _body(response):
    return json.loads(response.body.decode("utf-8"))


@pytest.fixture(autouse=True)
def _clear_readiness_cache():
    import main

    main._READINESS_CACHE["expires_at"] = 0.0
    main._READINESS_CACHE["report"] = None
    yield
    main._READINESS_CACHE["expires_at"] = 0.0
    main._READINESS_CACHE["report"] = None


def test_health_stays_200_liveness_on_cold_box(monkeypatch):
    import main

    async def fail_if_called():
        raise AssertionError("/health must not run readiness probes")

    monkeypatch.setattr(main, "_build_readiness_report", fail_if_called)

    payload = asyncio.run(main.root_health())

    assert payload == {
        "status": "ok",
        "service": "zoe-data",
        "version": "1.0.0",
        "memory_capture": main._memory_capture_health,
    }


def test_readyz_reports_not_ready_then_ready(monkeypatch):
    import main

    reports = [
        {
            "status": "degraded",
            "service": "zoe-data",
            "version": "1.0.0",
            "memory_capture": main._memory_capture_health,
            "ready": False,
            "dependencies": {"brain": {"ok": False, "error": "ConnectError"}},
        },
        {
            "status": "ok",
            "service": "zoe-data",
            "version": "1.0.0",
            "memory_capture": main._memory_capture_health,
            "ready": True,
            "dependencies": {
                "brain": {"ok": True},
                "stt": {"ok": True},
                "tts": {"ok": True},
            },
        },
    ]

    async def fake_report(*, use_cache=True):
        return reports.pop(0)

    monkeypatch.setattr(main, "_build_readiness_report", fake_report)

    first = asyncio.run(main.root_readyz())
    second = asyncio.run(main.root_readyz())

    assert first.status_code == 503
    assert _body(first)["ready"] is False
    assert second.status_code == 200
    assert _body(second)["ready"] is True


def test_readiness_report_degrades_when_brain_is_dead(monkeypatch):
    import main

    async def brain():
        return {"ok": False, "error": "ConnectError"}

    async def stt():
        return {"ok": True, "engine": "moonshine"}

    async def tts():
        return {"ok": True, "engine": "kokoro"}

    monkeypatch.setattr(main, "_check_brain_ready", brain)
    monkeypatch.setattr(main, "_check_stt_ready", stt)
    monkeypatch.setattr(main, "_check_tts_ready", tts)

    report = asyncio.run(main._build_readiness_report(use_cache=False))

    assert report["status"] == "degraded"
    assert report["ready"] is False
    assert report["dependencies"]["brain"]["error"] == "ConnectError"


def test_tts_mode_edge_counts_ready_without_kokoro(monkeypatch):
    import main

    monkeypatch.setenv("ZOE_TTS_MODE", "edge")

    report = asyncio.run(main._check_tts_ready())

    assert report["ok"] is True
    assert report["provider"] == "edge"


def test_readiness_cache_reuses_short_lived_report(monkeypatch):
    import main

    calls = {"count": 0}

    async def uncached():
        calls["count"] += 1
        return {
            "status": "ok",
            "service": "zoe-data",
            "version": "1.0.0",
            "memory_capture": main._memory_capture_health,
            "ready": True,
            "dependencies": {},
        }

    main._READINESS_CACHE["expires_at"] = 0.0
    main._READINESS_CACHE["report"] = None
    monkeypatch.setenv("ZOE_READINESS_CACHE_TTL_S", "5")
    monkeypatch.setattr(main, "_build_readiness_report_uncached", uncached)

    first = asyncio.run(main._build_readiness_report(use_cache=True))
    second = asyncio.run(main._build_readiness_report(use_cache=True))

    assert first["ready"] is True
    assert second["ready"] is True
    assert calls["count"] == 1


def test_canonical_gemma_model_requires_gemma_e4b():
    import main

    assert main._canonical_gemma_model("/models/gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf")
    assert not main._canonical_gemma_model("/models/gemma-4-E2B.gguf")
    assert not main._canonical_gemma_model("/models/llama-3.2.gguf")


def test_gemma_base_url_strips_version_suffix_with_trailing_slash(monkeypatch):
    import main

    monkeypatch.setenv("GEMMA_SERVER_URL", "http://127.0.0.1:11434/v1/")

    assert main._gemma_base_url() == "http://127.0.0.1:11434"
