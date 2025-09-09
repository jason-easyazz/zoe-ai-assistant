#!/bin/bash

echo "🎤 Improved Voice Test"
echo "========================"

# Function to test TTS and STT
test_phrase() {
    local TEXT="$1"
    echo -e "\n📝 Testing: '$TEXT'"
    
    # Generate speech
    echo "   Generating speech..."
    curl -s -X POST http://localhost:9002/synthesize \
      -H "Content-Type: application/json" \
      -d "{\"text\": \"$TEXT\"}" \
      --output test_phrase.wav
    
    if [ -f test_phrase.wav ]; then
        # Play it
        echo "   Playing audio..."
        aplay test_phrase.wav 2>/dev/null
        
        # Transcribe it
        echo "   Transcribing..."
        RESULT=$(curl -s -X POST http://localhost:9001/transcribe \
          -F "file=@test_phrase.wav" | python3 -c "import sys, json; data=json.load(sys.stdin); print(data.get('text', 'ERROR'))")
        
        echo "   ✅ Heard: '$RESULT'"
        
        # Check accuracy
        if [ "$TEXT" = "$RESULT" ]; then
            echo "   🎯 PERFECT MATCH!"
        else
            echo "   📊 Accuracy needs improvement"
        fi
    fi
}

# Test various phrases
test_phrase "Hello world"
test_phrase "Hello Jason"
test_phrase "Today is a beautiful day"
test_phrase "Zoe is my AI assistant"
test_phrase "The weather is nice today"

echo -e "\n✅ Test complete!"
