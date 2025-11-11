# Zoe Voice Agent Setup Guide

Complete guide to real-time voice conversations with Zoe using NeuTTS Air and LiveKit.

## Overview

Zoe's Voice Agent provides:
- ✅ **Ultra-realistic TTS** with voice cloning (NeuTTS Air)
- ✅ **Real-time conversations** with <200ms latency (LiveKit)
- ✅ **Natural interruptions** - talk over Zoe anytime
- ✅ **Multi-user support** - multiple simultaneous conversations
- ✅ **Voice profiles** - choose from preset voices or create your own
- ✅ **Mobile ready** - works on any browser

## Architecture

```
User Browser (WebRTC)
    ↓
LiveKit Server (WebRTC orchestration)
    ↓
Voice Agent Service (STT → LLM → TTS pipeline)
    ├─→ Whisper (Speech-to-Text)
    ├─→ Zoe Core (LLM reasoning)
    └─→ NeuTTS Air (Text-to-Speech with cloning)
```

## Quick Start

### 1. Download Sample Voice Files

```bash
cd /home/zoe/assistant/services/zoe-tts/samples
wget https://raw.githubusercontent.com/neuphonic/neutts-air/main/samples/dave.wav
wget https://raw.githubusercontent.com/neuphonic/neutts-air/main/samples/jo.wav
```

### 2. Set Environment Variables

Add to `/home/zoe/assistant/.env`:

```env
# LiveKit Configuration
LIVEKIT_API_KEY=your_api_key_here
LIVEKIT_API_SECRET=your_secret_here
LIVEKIT_URL=ws://localhost:7880

# Or use defaults for development
LIVEKIT_API_KEY=devkey
LIVEKIT_API_SECRET=secret
```

### 3. Build and Start Services

```bash
cd /home/zoe/assistant

# Build new services
docker-compose build zoe-tts livekit zoe-voice-agent

# Start all services
docker-compose up -d

# Check logs
docker-compose logs -f zoe-tts
docker-compose logs -f zoe-voice-agent
docker-compose logs -f livekit
```

### 4. Verify Services

```bash
# Check TTS service
curl http://localhost:9002/health

# Check LiveKit server
curl http://localhost:7880/

# Check voice agent
curl http://localhost:9003/health

# Check available voices
curl http://localhost:8000/api/tts/voices
```

### 5. Open Voice Client

Navigate to: `http://your-zoe-ip/voice-client.html`

- Enter your name
- Select a voice profile
- Click "Start Conversation"
- Grant microphone permissions
- Start talking!

## Voice Profiles

### System Voices

#### Default Voice
Basic NeuTTS Air voice, no cloning

#### Dave (British Male)
- Friendly British accent
- Good for casual conversations
- Sample: "My name is Dave, and um, I'm from London."

#### Jo (Female)
- Clear, professional female voice
- Good for task-oriented interactions
- Sample: "Hello, I'm Jo. It's nice to meet you."

#### Zoe (Friendly Female)
- Warm, assistant-like voice
- Recommended default for Zoe
- Sample: "Hi there! I'm Zoe, your personal AI assistant."

### Creating Custom Voice Profiles

#### Via API

```bash
curl -X POST http://localhost:8000/api/tts/clone-voice \
  -F "profile_name=My Custom Voice" \
  -F "reference_text=This is the exact text from my recording" \
  -F "reference_audio=@/path/to/audio.wav" \
  -F "user_id=your_user_id"
```

#### Via UI (Coming Soon)

Navigate to Voice Settings → Create Voice Profile

#### Recording Guidelines

For best voice cloning results:

- **Duration**: 3-15 seconds
- **Format**: WAV, mono, 16-44kHz
- **Content**: Natural, continuous speech
- **Quality**: Clean recording, minimal background noise
- **Tone**: Conversational, not robotic
- **Pauses**: Minimal pauses between words

**Good example:**
> "Hi there! My name is Sarah, and I love helping people with their daily tasks. I'm excited to be your AI assistant today."

**Bad example:**
> "Hello. This. Is. A. Test." (too robotic, too many pauses)

## Testing Voice Features

### 1. Test Basic TTS

```bash
curl -X POST http://localhost:8000/api/tts/synthesize \
  -H "Content-Type: application/json" \
  -d '{
    "text": "Hello! This is a test of the new voice system.",
    "voice": "dave",
    "speed": 1.0
  }' \
  --output test-voice.wav

# Play the audio
aplay test-voice.wav
```

### 2. Test Voice Conversation

```python
import requests

# Start conversation
response = requests.post('http://localhost:8000/api/voice/start-conversation', json={
    'user_id': 'test_user',
    'voice_profile': 'zoe'
})

token_data = response.json()
print(f"Room: {token_data['room_name']}")
print(f"Token: {token_data['token'][:50]}...")
```

### 3. List Active Conversations

```bash
curl http://localhost:8000/api/voice/rooms
```

## Performance Optimization

### Raspberry Pi 5 Settings

#### Expected Performance
- **TTS Generation**: 2-3x realtime (Q4 GGUF)
- **Round-trip Latency**: 200-500ms
- **Concurrent Conversations**: 3-5 simultaneously
- **Memory Usage**: ~2GB for TTS, ~500MB for LiveKit

#### Optimization Tips

1. **Pre-warm voice profiles** (already done on startup)
2. **Use caching** for repeated phrases
3. **Limit concurrent conversations** to 3-5
4. **Monitor CPU usage**: `docker stats`

### When to Upgrade to Jetson Orin NX

Consider upgrading if:
- TTS generation is >5x realtime
- You need >5 concurrent conversations
- You want to use larger/better quality models
- You need real-time Whisper Large

## Troubleshooting

### TTS Service Won't Start

**Check espeak-ng installation:**
```bash
docker-compose exec zoe-tts espeak-ng --version
```

**Check model download:**
```bash
docker-compose exec zoe-tts ls -lh /root/.cache/neutts
```

**View logs:**
```bash
docker-compose logs -f zoe-tts | grep -i error
```

### LiveKit Connection Failed

**Check LiveKit server:**
```bash
curl http://localhost:7880/
```

**Check firewall:**
```bash
sudo ufw status
sudo ufw allow 7880/tcp
sudo ufw allow 50000:50200/udp
```

**Check API keys:**
```bash
docker-compose exec livekit env | grep LIVEKIT
```

### Voice Agent Not Responding

**Check voice agent logs:**
```bash
docker-compose logs -f zoe-voice-agent
```

**Test Zoe Core connection:**
```bash
curl http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Hello", "user_id": "test"}'
```

### Audio Quality Issues

**Symptoms**: Robotic voice, choppy audio, artifacts

**Solutions:**
1. **Check sample rate**: Should be 24kHz
2. **Test with different voice**: Some voices clone better
3. **Check CPU usage**: May be throttling
4. **Reduce concurrent load**: Pause other processes

### Latency Too High

**Measure components:**
```bash
# TTS latency
time curl -X POST http://localhost:9002/synthesize \
  -H "Content-Type: application/json" \
  -d '{"text": "Test", "voice": "default"}' \
  -o /dev/null

# Should be < 2 seconds for short text
```

**Optimize:**
- Use `default` voice (fastest, no cloning)
- Reduce concurrent conversations
- Check network latency (for remote access)
- Ensure no other heavy processes running

## API Reference

### Start Voice Conversation

```http
POST /api/voice/start-conversation
Content-Type: application/json

{
  "user_id": "string",
  "voice_profile": "default|dave|jo|zoe|custom_id",
  "room_name": "optional-room-name"
}

Response:
{
  "token": "eyJhbG...",
  "room_name": "zoe-voice-user-123456",
  "livekit_url": "http://localhost:7880",
  "user_id": "string",
  "expires_at": 1234567890
}
```

### List Active Rooms

```http
GET /api/voice/rooms

Response:
{
  "rooms": [
    {
      "name": "zoe-voice-user-123",
      "sid": "RM_abc123",
      "num_participants": 2,
      "created_at": 1234567890
    }
  ],
  "total": 1
}
```

### End Conversation

```http
DELETE /api/voice/rooms/{room_name}

Response:
{
  "success": true,
  "message": "Room deleted successfully"
}
```

### Synthesize Speech

```http
POST /api/tts/synthesize
Content-Type: application/json

{
  "text": "Hello, how can I help you today?",
  "voice": "zoe",
  "speed": 1.0,
  "use_cache": true,
  "user_id": "optional"
}

Response: audio/wav file
```

### Clone Voice

```http
POST /api/tts/clone-voice
Content-Type: multipart/form-data

profile_name: "My Voice"
reference_text: "Exact transcription of audio"
reference_audio: audio.wav file
user_id: "optional"

Response:
{
  "success": true,
  "profile_id": "uuid",
  "profile_name": "My Voice",
  "message": "Voice profile created successfully"
}
```

## Security Considerations

### Production Deployment

1. **Change default API keys:**
```env
LIVEKIT_API_KEY=your-secure-random-key
LIVEKIT_API_SECRET=your-secure-random-secret
```

2. **Enable HTTPS** for LiveKit (required for browser microphone access over internet)

3. **Set up TURN server** for NAT traversal:
```yaml
# In livekit config.yaml
rtc:
  turn_servers:
    - urls: ["turn:your-turn-server.com:3478"]
      username: "turnuser"
      credential: "turnpass"
```

4. **Implement authentication** on `/api/voice/start-conversation`

5. **Rate limit** conversation creation

### Privacy

- All voice processing happens on your server
- No data sent to external services (except if using OpenAI Whisper API)
- Conversations stored in local temporal memory
- Voice profiles stored locally

## Next Steps

### Mobile App

Use LiveKit Swift/Kotlin SDKs for native apps:
- [LiveKit iOS SDK](https://github.com/livekit/client-sdk-swift)
- [LiveKit Android SDK](https://github.com/livekit/client-sdk-android)

### Always-Listening Mode

Integrate wake word detection:
```python
# Trigger LiveKit room join on wake word
if wake_word_detected("hey zoe"):
    start_voice_conversation()
```

### Custom STT

Replace OpenAI Whisper with local Whisper:
```python
# In agent.py
from livekit.plugins import whisper_local
stt = whisper_local.STT(model_path="/path/to/model")
```

## Support

- **Issues**: Report on GitHub
- **Logs**: `docker-compose logs -f zoe-voice-agent`
- **Status**: Check `http://localhost:8000/api/voice/status`

## Credits

- **NeuTTS Air**: [Neuphonic](https://github.com/neuphonic/neutts-air)
- **LiveKit**: [LiveKit](https://github.com/livekit/livekit)
- Built with ❤️ for Zoe AI Assistant












