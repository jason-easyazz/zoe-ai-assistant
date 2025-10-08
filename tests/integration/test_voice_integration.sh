#!/bin/bash
# Voice Integration Tests

echo "Testing Whisper STT..."
curl -X GET http://localhost:9001/health || exit 1

echo "Testing Coqui TTS..."
curl -X GET http://localhost:9002/health || exit 1

echo "Testing TTS synthesis..."
curl -X POST http://localhost:9002/synthesize \
  -H "Content-Type: application/json" \
  -d '{"text": "Hello, this is a test"}' \
  --output test_audio.wav

if [ -f test_audio.wav ]; then
    echo "✅ TTS test passed"
    rm test_audio.wav
else
    echo "❌ TTS test failed"
    exit 1
fi
