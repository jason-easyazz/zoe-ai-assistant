# Voice Pipeline Architecture

## Overview

The voice pipeline converts spoken commands to actions and responses to speech,
optimized for conversational latency on Jetson hardware.

## Latency Budget (Target: <4s end-to-end)

```
User speaks (1-3s of speech)
    |
    v
[STT] Faster-Whisper tiny/base, CUDA: ~0.5-1.0s
    |
    v
[TRUST GATE] Allowlist check: <5ms
    |
    v
[INTENT] Tier 0-2 (HassIL/keywords): <20ms
    OR
[SKILLS] Tier 3-4 (LLM): ~1.5-2.5s
    |
    v
[TTS] Piper, CUDA, streaming: first audio <300ms
    |
    v
User hears response
```

## Latency Targets

- Smart home command: **<1.5s** (Tier 0, no LLM)
- Simple question: **<2s** (Tier 1 keyword, API call)
- Complex query: **~3-4s** to start speaking (Tier 4 LLM, streams)

## Services

- `zoe-stt`: Faster-Whisper with CUDA (STT)
- `zoe-tts-simple`: Piper TTS with CUDA (TTS)
- Wake word: OpenWakeWord on host (not in Docker)
- Voice orchestrator: `services/zoe-core/voice/orchestrator.py`

## API Endpoints

- `POST /api/voice-pipeline/transcribe` -- Audio to text
- `POST /api/voice-pipeline/synthesize` -- Text to audio
- `POST /api/voice-pipeline/process` -- Full pipeline
- `GET /api/voice-pipeline/status` -- Service status

## Key Optimizations

1. Models stay resident in memory (no cold start)
2. Streaming TTS (speak while still generating)
3. Intent system bypasses LLM for voice commands
4. Wake word detection runs on CPU continuously
