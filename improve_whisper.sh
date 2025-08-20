#!/bin/bash

# ============================================================================
# IMPROVE WHISPER ACCURACY
# ============================================================================

echo "🔧 Improving Whisper Accuracy..."

# Fix 1: Update Whisper to force English and use base model (better accuracy)
cat > services/zoe-whisper/app.py << 'EOF'
import whisper
import tempfile
import os
import subprocess
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import JSONResponse

app = FastAPI(title="Zoe Whisper STT")

# Try to load base model for better accuracy (fallback to tiny if memory issues)
print("Loading Whisper model...")
try:
    model = whisper.load_model("base")
    model_name = "base"
    print("Base model loaded (better accuracy)")
except:
    try:
        model = whisper.load_model("tiny.en")  # English-only tiny model
        model_name = "tiny.en"
        print("Tiny English model loaded")
    except:
        model = whisper.load_model("tiny")
        model_name = "tiny"
        print("Tiny model loaded")

@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "model": f"whisper-{model_name}",
        "note": "Force English for better accuracy"
    }

@app.post("/transcribe")
async def transcribe_audio(file: UploadFile = File(...)):
    """Transcribe uploaded audio file"""
    if not model:
        raise HTTPException(status_code=503, detail="Model not loaded")
    
    temp_input = None
    temp_converted = None
    
    try:
        # Save uploaded file
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
            content = await file.read()
            tmp.write(content)
            temp_input = tmp.name
        
        # Convert to 16kHz mono for best results
        temp_converted = tempfile.mktemp(suffix=".wav")
        convert_cmd = [
            "ffmpeg", "-i", temp_input,
            "-ar", "16000",      # 16kHz sample rate
            "-ac", "1",          # Mono
            "-af", "volume=2",   # Boost volume
            "-y",                # Overwrite
            temp_converted
        ]
        
        subprocess.run(convert_cmd, capture_output=True)
        
        # Transcribe with English forced
        result = model.transcribe(
            temp_converted if os.path.exists(temp_converted) else temp_input,
            language="en",  # Force English
            fp16=False      # Don't use FP16 on Pi
        )
        
        return {
            "text": result["text"].strip(),
            "language": "en",
            "model": model_name
        }
        
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": f"Transcription failed: {str(e)}"}
        )
    finally:
        # Clean up
        for f in [temp_input, temp_converted]:
            if f and os.path.exists(f):
                try:
                    os.unlink(f)
                except:
                    pass
EOF

echo "✅ Whisper updated to force English"

# Fix 2: Install espeak on host for testing
echo "📦 Installing espeak on host system..."
sudo apt-get update && sudo apt-get install -y espeak > /dev/null 2>&1
echo "✅ espeak installed"

# Fix 3: Create better test script
cat > test_voice_improved.sh << 'EOF'
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
EOF

chmod +x test_voice_improved.sh

# Fix 4: Rebuild Whisper with improvements
echo "🔄 Rebuilding Whisper service..."
docker compose build zoe-whisper
docker compose up -d zoe-whisper

echo "⏳ Waiting for model to load (this may take a minute)..."
sleep 10

# Check if it's ready
for i in {1..30}; do
    if curl -s http://localhost:9001/health | grep -q "healthy"; then
        echo "✅ Whisper ready!"
        break
    fi
    echo -n "."
    sleep 2
done

# Test the improvements
echo -e "\n🧪 Testing improved Whisper..."

# Create a clear test audio
echo "Testing Whisper accuracy with clear speech" | \
  docker exec zoe-tts espeak -w /tmp/clear_test.wav -s 150 -stdin

# Copy out and test
docker cp zoe-tts:/tmp/clear_test.wav clear_test.wav 2>/dev/null

if [ -f clear_test.wav ]; then
    RESULT=$(curl -s -X POST http://localhost:9001/transcribe \
      -F "file=@clear_test.wav")
    echo "Transcription result: $RESULT"
fi

echo -e "\n✅ Improvements applied!"
echo ""
echo "📝 Tips for better accuracy:"
echo "1. Speak clearly and not too fast"
echo "2. Use longer phrases (more context = better accuracy)"
echo "3. Minimize background noise"
echo "4. The base model is more accurate but slower"
echo ""
echo "🧪 Run the improved test:"
echo "   ./test_voice_improved.sh"
