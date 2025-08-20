#!/bin/bash

# ============================================================================
# FIX VOICE SERVICES - TTS AND WHISPER
# ============================================================================

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log() { echo -e "${GREEN}[$(date '+%H:%M:%S')]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARNING]${NC} $1"; }
success() { echo -e "${GREEN}✅${NC} $1"; }

echo -e "${BLUE}════════════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}FIXING VOICE SERVICES${NC}"
echo -e "${BLUE}════════════════════════════════════════════════════════════════${NC}"

cd /home/pi/zoe

# ============================================================================
# FIX 1: Update TTS to return actual audio files
# ============================================================================

log "🔧 Fixing TTS service to return audio files..."

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

@app.get("/health")
async def health():
    return {"status": "healthy", "engine": "espeak"}

@app.post("/synthesize")
async def synthesize_speech(request: TTSRequest):
    """Generate speech and return audio file"""
    try:
        # Create unique filename
        filename = f"/tmp/tts_{uuid.uuid4().hex}.wav"
        
        # Use espeak to generate speech
        result = subprocess.run([
            "espeak",
            "-w", filename,
            "-s", "150",  # Speed
            request.text
        ], capture_output=True, text=True)
        
        if result.returncode != 0:
            raise HTTPException(status_code=500, detail=f"TTS failed: {result.stderr}")
        
        # Return the audio file
        return FileResponse(
            filename,
            media_type="audio/wav",
            filename="speech.wav"
        )
            
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/voices")
async def list_voices():
    return {"voices": [{"id": "default", "name": "Default Voice"}]}
EOF

success "TTS service fixed"

# ============================================================================
# FIX 2: Update Whisper to handle audio better
# ============================================================================

log "🔧 Fixing Whisper service..."

cat > services/zoe-whisper/app.py << 'EOF'
import whisper
import tempfile
import os
import subprocess
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import JSONResponse

app = FastAPI(title="Zoe Whisper STT")

# Load tiny model for Raspberry Pi
print("Loading Whisper model (this may take a moment)...")
try:
    model = whisper.load_model("tiny")
    print("Model loaded successfully!")
except Exception as e:
    print(f"Failed to load model: {e}")
    model = None

@app.get("/health")
async def health():
    return {
        "status": "healthy" if model else "model not loaded",
        "model": "whisper-tiny"
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
        
        # Convert to format Whisper can handle (16kHz mono)
        temp_converted = tempfile.mktemp(suffix=".wav")
        convert_cmd = [
            "ffmpeg", "-i", temp_input,
            "-ar", "16000",  # 16kHz sample rate
            "-ac", "1",      # Mono
            "-y",            # Overwrite
            temp_converted
        ]
        
        result = subprocess.run(convert_cmd, capture_output=True, text=True)
        
        if result.returncode != 0:
            # If conversion fails, try using original
            print(f"Conversion warning: {result.stderr}")
            temp_converted = temp_input
        
        # Transcribe
        result = model.transcribe(temp_converted)
        
        return {
            "text": result["text"].strip(),
            "language": result.get("language", "en")
        }
        
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": f"Transcription failed: {str(e)}"}
        )
    finally:
        # Clean up temp files
        for f in [temp_input, temp_converted]:
            if f and os.path.exists(f):
                try:
                    os.unlink(f)
                except:
                    pass
EOF

success "Whisper service fixed"

# ============================================================================
# FIX 3: Add some test events to calendar
# ============================================================================

log "📅 Adding sample calendar events..."

cat > /tmp/add_events.py << 'EOF'
import sqlite3
from datetime import datetime, timedelta

conn = sqlite3.connect('/home/pi/zoe/data/zoe.db')
cursor = conn.cursor()

# Add some sample events
events = [
    ("Team Meeting", datetime.now().date(), "10:00"),
    ("Lunch with Alice", (datetime.now() + timedelta(days=1)).date(), "12:30"),
    ("Project Review", (datetime.now() + timedelta(days=2)).date(), "15:00")
]

for title, date, time in events:
    cursor.execute(
        "INSERT INTO events (title, start_date, start_time) VALUES (?, ?, ?)",
        (title, date.isoformat(), time)
    )

conn.commit()
conn.close()
print("✅ Added sample events")
EOF

python3 /tmp/add_events.py

# ============================================================================
# REBUILD AND RESTART VOICE SERVICES
# ============================================================================

log "🔄 Rebuilding voice services..."

# Rebuild TTS
docker compose build zoe-tts
docker compose up -d zoe-tts
sleep 3

# Rebuild Whisper
docker compose build zoe-whisper
docker compose up -d zoe-whisper
sleep 5

# ============================================================================
# TEST THE FIXES
# ============================================================================

echo -e "\n${BLUE}════════════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}TESTING FIXED SERVICES${NC}"
echo -e "${BLUE}════════════════════════════════════════════════════════════════${NC}"

# Test TTS
log "🔊 Testing TTS..."
curl -X POST http://localhost:9002/synthesize \
  -H "Content-Type: application/json" \
  -d '{"text": "Voice services are now working correctly"}' \
  --output test_tts.wav 2>/dev/null

if [ -f test_tts.wav ] && [ -s test_tts.wav ]; then
    success "TTS working! Audio saved to test_tts.wav"
    echo "File size: $(ls -lh test_tts.wav | awk '{print $5}')"
    
    # Play it if we have a speaker
    which aplay > /dev/null && {
        log "Playing audio..."
        aplay test_tts.wav 2>/dev/null || warn "Could not play audio"
    }
else
    warn "TTS test failed"
fi

# Test Whisper with the TTS output
log "🎤 Testing Whisper..."
if [ -f test_tts.wav ]; then
    RESULT=$(curl -s -X POST http://localhost:9001/transcribe \
      -F "file=@test_tts.wav")
    
    echo "Whisper result: $RESULT"
    
    if echo "$RESULT" | grep -q "text"; then
        success "Whisper working!"
    else
        warn "Whisper test incomplete"
    fi
fi

# Test calendar
log "📅 Testing calendar..."
curl -s http://localhost:8000/api/calendar/events | python3 -m json.tool

# ============================================================================
# CREATE HELPER SCRIPTS
# ============================================================================

log "📝 Creating helper scripts..."

cat > test_voice.sh << 'EOF'
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
EOF

chmod +x test_voice.sh

success "Created test_voice.sh helper script"

echo -e "\n${GREEN}════════════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}VOICE SERVICES FIXED!${NC}"
echo -e "${GREEN}════════════════════════════════════════════════════════════════${NC}"

echo -e "\n${BLUE}Quick Test Commands:${NC}"
echo "1. Test TTS:"
echo "   curl -X POST http://localhost:9002/synthesize \\"
echo "     -H 'Content-Type: application/json' \\"
echo "     -d '{\"text\": \"Hello world\"}' --output hello.wav"
echo ""
echo "2. Test Whisper:"
echo "   curl -X POST http://localhost:9001/transcribe -F 'file=@hello.wav'"
echo ""
echo "3. Interactive test:"
echo "   ./test_voice.sh"

exit 0
