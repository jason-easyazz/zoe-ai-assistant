"""
Voice Orchestrator
===================

Phase 5: Coordinates the voice pipeline:
    Wake word -> STT -> Trust Gate -> Intent Pipeline -> TTS -> Speaker

Latency targets:
- Smart home command: <1.5s total
- Simple question: <2s total
- Complex query: ~3-4s to start speaking

The orchestrator manages:
1. STT service communication (audio -> text)
2. Routing transcribed text through the chat pipeline
3. TTS service communication (text -> audio, streaming)
4. Audio playback coordination
"""

import logging
import httpx
import os
import time
import asyncio
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

# Service URLs (configurable via environment)
STT_URL = os.getenv("ZOE_STT_URL", "http://zoe-stt:8020")
TTS_URL = os.getenv("ZOE_TTS_URL", "http://zoe-tts-simple:8021")
CORE_URL = os.getenv("ZOE_CORE_URL", "http://localhost:8000")

# Model configuration
STT_MODEL = os.getenv("ZOE_STT_MODEL", "tiny")  # tiny for speed, base for accuracy
TTS_VOICE = os.getenv("ZOE_TTS_VOICE", "en_US-lessac-medium")


class VoiceOrchestrator:
    """Orchestrates the voice pipeline."""

    def __init__(self):
        self._client = httpx.AsyncClient(timeout=30.0)
        self._stt_available = False
        self._tts_available = False
        self._stats = {
            "stt_calls": 0,
            "tts_calls": 0,
            "avg_stt_ms": 0,
            "avg_tts_ms": 0,
            "total_voice_interactions": 0,
        }

    async def check_services(self) -> Dict[str, bool]:
        """Check if STT and TTS services are available."""
        results = {"stt": False, "tts": False}

        try:
            resp = await self._client.get(f"{STT_URL}/health", timeout=3.0)
            results["stt"] = resp.status_code == 200
            self._stt_available = results["stt"]
        except Exception:
            self._stt_available = False

        try:
            resp = await self._client.get(f"{TTS_URL}/health", timeout=3.0)
            results["tts"] = resp.status_code == 200
            self._tts_available = results["tts"]
        except Exception:
            self._tts_available = False

        logger.info(f"Voice services: STT={results['stt']}, TTS={results['tts']}")
        return results

    async def transcribe(self, audio_data: bytes, model: str = None) -> Optional[str]:
        """Send audio to the STT service for transcription.

        Args:
            audio_data: Raw audio bytes (WAV format)
            model: Whisper model to use ("tiny" or "base")

        Returns:
            Transcribed text or None on failure
        """
        if not self._stt_available:
            logger.warning("STT service not available")
            return None

        model = model or STT_MODEL
        start = time.time()

        try:
            resp = await self._client.post(
                f"{STT_URL}/transcribe",
                files={"audio": ("audio.wav", audio_data, "audio/wav")},
                data={"model": model},
                timeout=10.0,
            )

            if resp.status_code == 200:
                result = resp.json()
                elapsed_ms = (time.time() - start) * 1000

                self._stats["stt_calls"] += 1
                self._stats["avg_stt_ms"] = (
                    (self._stats["avg_stt_ms"] * (self._stats["stt_calls"] - 1) + elapsed_ms)
                    / self._stats["stt_calls"]
                )

                text = result.get("text", "").strip()
                logger.info(f"STT: '{text}' ({elapsed_ms:.0f}ms, model: {model})")
                return text

            logger.error(f"STT error: HTTP {resp.status_code}")
            return None

        except Exception as e:
            logger.error(f"STT failed: {e}")
            return None

    async def synthesize(self, text: str, voice: str = None) -> Optional[bytes]:
        """Send text to the TTS service for synthesis.

        Args:
            text: Text to synthesize
            voice: Piper voice to use

        Returns:
            Audio bytes (WAV format) or None on failure
        """
        if not self._tts_available:
            logger.warning("TTS service not available")
            return None

        voice = voice or TTS_VOICE
        start = time.time()

        try:
            resp = await self._client.post(
                f"{TTS_URL}/synthesize",
                json={"text": text, "voice": voice},
                timeout=15.0,
            )

            if resp.status_code == 200:
                elapsed_ms = (time.time() - start) * 1000

                self._stats["tts_calls"] += 1
                self._stats["avg_tts_ms"] = (
                    (self._stats["avg_tts_ms"] * (self._stats["tts_calls"] - 1) + elapsed_ms)
                    / self._stats["tts_calls"]
                )

                logger.info(f"TTS: {len(text)} chars -> {len(resp.content)} bytes ({elapsed_ms:.0f}ms)")
                return resp.content

            logger.error(f"TTS error: HTTP {resp.status_code}")
            return None

        except Exception as e:
            logger.error(f"TTS failed: {e}")
            return None

    async def process_voice_input(
        self,
        audio_data: bytes,
        user_id: str = "default",
        session_id: str = "voice",
    ) -> Dict[str, Any]:
        """Full voice pipeline: audio -> text -> response -> audio.

        Args:
            audio_data: Raw audio bytes
            user_id: The Zoe user
            session_id: Session identifier

        Returns:
            Dict with transcription, response text, and audio bytes
        """
        pipeline_start = time.time()
        result = {
            "transcription": None,
            "response_text": None,
            "audio": None,
            "timings": {},
        }

        # Step 1: STT
        text = await self.transcribe(audio_data)
        result["transcription"] = text
        result["timings"]["stt_ms"] = (time.time() - pipeline_start) * 1000

        if not text:
            return result

        # Step 2: Process through chat pipeline
        chat_start = time.time()
        try:
            resp = await self._client.post(
                f"{CORE_URL}/api/chat",
                json={
                    "message": text,
                    "context": {
                        "source_type": "web",
                        "source_value": user_id,
                        "channel": "voice",
                    },
                    "user_id": user_id,
                    "session_id": session_id,
                },
                headers={"X-Session-ID": "dev-localhost"},
                timeout=30.0,
            )

            if resp.status_code == 200:
                chat_data = resp.json()
                result["response_text"] = chat_data.get("response", "")
        except Exception as e:
            logger.error(f"Chat pipeline failed: {e}")
            result["response_text"] = "I'm sorry, I couldn't process that."

        result["timings"]["chat_ms"] = (time.time() - chat_start) * 1000

        # Step 3: TTS
        if result["response_text"]:
            tts_start = time.time()
            audio = await self.synthesize(result["response_text"])
            result["audio"] = audio
            result["timings"]["tts_ms"] = (time.time() - tts_start) * 1000

        result["timings"]["total_ms"] = (time.time() - pipeline_start) * 1000
        self._stats["total_voice_interactions"] += 1

        logger.info(f"Voice pipeline: {result['timings']}")
        return result

    def get_stats(self) -> Dict[str, Any]:
        """Get voice pipeline statistics."""
        return {
            **self._stats,
            "stt_available": self._stt_available,
            "tts_available": self._tts_available,
            "stt_model": STT_MODEL,
            "tts_voice": TTS_VOICE,
        }


# Singleton
voice_orchestrator = VoiceOrchestrator()
