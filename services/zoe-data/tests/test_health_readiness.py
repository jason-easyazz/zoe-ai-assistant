"""Readiness contract for /health."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_readiness_report_keeps_liveness_keys_when_ready(monkeypatch):
    import main

    async def brain():
        return {"ok": True, "model": "gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf"}

    async def stt():
        return {"ok": True, "engine": "moonshine"}

    async def tts():
        return {"ok": True, "engine": "kokoro"}

    monkeypatch.setattr(main, "_check_brain_ready", brain)
    monkeypatch.setattr(main, "_check_stt_ready", stt)
    monkeypatch.setattr(main, "_check_tts_ready", tts)

    report = asyncio.run(main._build_readiness_report())

    assert report["status"] == "ok"
    assert report["service"] == "zoe-data"
    assert report["version"] == "1.0.0"
    assert "memory_capture" in report
    assert report["ready"] is True
    assert set(report["dependencies"]) == {"brain", "stt", "tts"}


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

    report = asyncio.run(main._build_readiness_report())

    assert report["status"] == "degraded"
    assert report["ready"] is False
    assert report["dependencies"]["brain"]["error"] == "ConnectError"


def test_canonical_gemma_model_requires_gemma_e4b():
    import main

    assert main._canonical_gemma_model("/models/gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf")
    assert not main._canonical_gemma_model("/models/gemma-4-E2B.gguf")
    assert not main._canonical_gemma_model("/models/llama-3.2.gguf")
