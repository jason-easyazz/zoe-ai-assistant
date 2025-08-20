#!/bin/bash

echo "🎯 Voice Quality Test"
echo "===================="

test_and_compare() {
    local TEXT="$1"
    echo -e "\n📝 Testing: '$TEXT'"
    
    # Method 1: TTS Service
    echo "Method 1: TTS Service"
    curl -s -X POST http://localhost:9002/synthesize \
      -H "Content-Type: application/json" \
      -d "{\"text\": \"$TEXT\"}" \
      --output tts_service.wav
    
    RESULT1=$(curl -s -X POST http://localhost:9001/transcribe \
      -F "file=@tts_service.wav" | python3 -c "import sys, json; print(json.load(sys.stdin).get('text', 'ERROR'))")
    echo "   TTS → Whisper: '$RESULT1'"
    
    # Method 2: Direct espeak
    echo "Method 2: Direct espeak"
    espeak -w direct_espeak.wav -s 140 -a 150 "$TEXT"
    
    RESULT2=$(curl -s -X POST http://localhost:9001/transcribe \
      -F "file=@direct_espeak.wav" | python3 -c "import sys, json; print(json.load(sys.stdin).get('text', 'ERROR'))")
    echo "   espeak → Whisper: '$RESULT2'"
    
    # Compare
    if [ "$TEXT" = "${RESULT1%.*}" ] || [ "$TEXT" = "${RESULT1%.}" ]; then
        echo "   ✅ TTS Service: PERFECT!"
    fi
    if [ "$TEXT" = "${RESULT2%.*}" ] || [ "$TEXT" = "${RESULT2%.}" ]; then
        echo "   ✅ Direct espeak: PERFECT!"
    fi
}

# Test phrases
test_and_compare "Hello Jason"
test_and_compare "Welcome to Zoe"
test_and_compare "The quick brown fox"
test_and_compare "Testing one two three"

echo -e "\n✅ Quality test complete!"
