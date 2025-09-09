#!/bin/bash

# ============================================================================
# FIX TTS AUDIO QUALITY
# ============================================================================

echo "🔧 Fixing TTS Audio Quality..."

# Update TTS to generate clearer audio
cat > services/zoe-tts/app.py << 'EOF'
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
import subprocess
import tempfile
import os
import uuid

app = FastAPI(title="Zoe TTS Service")

class TTSRequest(BaseModel):
    text: str
    voice: str = "default"
    speed: int = 140  # Slower, clearer speech

@app.get("/health")
async def health():
    return {"status": "healthy", "engine": "espeak-optimized"}

@app.post("/synthesize")
async def synthesize_speech(request: TTSRequest):
    """Generate clearer speech optimized for Whisper"""
    try:
        # Create unique filename
        filename = f"/tmp/tts_{uuid.uuid4().hex}.wav"
        
        # Use espeak with optimized settings for clarity
        # -s: speed (140 is slower and clearer)
        # -a: amplitude/volume (150 out of 200)
        # -p: pitch (50 is normal)
        # -g: word gap (10ms between words)
        result = subprocess.run([
            "espeak",
            "-w", filename,
            "-s", str(request.speed),  # Slower speed for clarity
            "-a", "150",               # Good volume
            "-p", "50",                # Normal pitch
            "-g", "10",                # Small gap between words
            request.text
        ], capture_output=True, text=True)
        
        if result.returncode != 0:
            raise HTTPException(status_code=500, detail=f"TTS failed: {result.stderr}")
        
        # Post-process audio for better quality
        processed_filename = f"/tmp/tts_processed_{uuid.uuid4().hex}.wav"
        
        # Use ffmpeg to normalize and enhance audio
        enhance_cmd = [
            "ffmpeg", "-i", filename,
            "-af", "highpass=f=200,lowpass=f=3000,volume=1.5",  # Filter and boost
            "-ar", "16000",  # 16kHz (what Whisper expects)
            "-ac", "1",      # Mono
            "-y",            # Overwrite
            processed_filename
        ]
        
        enhance_result = subprocess.run(enhance_cmd, capture_output=True, text=True)
        
        # Use processed file if enhancement worked, otherwise original
        final_file = processed_filename if enhance_result.returncode == 0 else filename
        
        # Return the audio file
        return FileResponse(
            final_file,
            media_type="audio/wav",
            filename="speech.wav",
            headers={"X-Audio-Quality": "optimized-for-stt"}
        )
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/voices")
async def list_voices():
    return {
        "voices": [
            {"id": "default", "name": "Clear Voice", "speed": 140},
            {"id": "fast", "name": "Fast Voice", "speed": 175},
            {"id": "slow", "name": "Slow Voice", "speed": 120}
        ]
    }
EOF

echo "✅ TTS service updated with better audio quality"

# Install ffmpeg in TTS container if not present
echo "📦 Ensuring ffmpeg is in TTS container..."
cat > services/zoe-tts/Dockerfile << 'EOF'
FROM python:3.9-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    espeak \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

RUN pip install fastapi uvicorn pyttsx3

COPY app.py .

EXPOSE 9002
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "9002"]
EOF

# Rebuild TTS
echo "🔄 Rebuilding TTS service..."
docker compose build zoe-tts
docker compose up -d zoe-tts
sleep 3

# Create an alternative test using better TTS
cat > test_voice_quality.sh << 'EOF'
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
EOF

chmod +x test_voice_quality.sh

echo -e "\n✅ TTS Quality improvements applied!"
echo ""
echo "🧪 Test the improvements:"
echo "   ./test_voice_quality.sh"
echo ""
echo "This will compare TTS service vs direct espeak to see the difference."
