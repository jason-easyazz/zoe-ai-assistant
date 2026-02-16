"""
Zoe Voice Pipeline
===================

Phase 5: Voice input/output pipeline optimized for conversational latency.

Architecture:
- STT: Faster-Whisper with CUDA (external service)
- TTS: Piper TTS with CUDA (external service)
- Wake word: OpenWakeWord on host (not in Docker)
- Orchestrator: Coordinates STT -> Intent Pipeline -> TTS
"""
