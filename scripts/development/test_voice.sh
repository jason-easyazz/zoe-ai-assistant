#!/bin/bash

echo "🎤 Zoe Voice Test Script"

# Test TTS
echo -n "Enter text to speak: "
read TEXT

echo "Generating speech..."
curl -X POST http://localhost:9002/synthesize \
  -H "Content-Type: application/json" \
  -d "{\"text\": \"$TEXT\"}" \
  --output speech.wav 2>/dev/null

if [ -f speech.wav ]; then
    echo "✅ Speech generated: speech.wav"
    echo "Playing audio..."
    aplay speech.wav 2>/dev/null || echo "Install aplay to hear audio: sudo apt-get install alsa-utils"
    
    echo "Transcribing back..."
    curl -X POST http://localhost:9001/transcribe \
      -F "file=@speech.wav"
else
    echo "❌ TTS failed"
fi
