# Sample Voice Profiles

This directory contains reference audio samples for voice cloning with NeuTTS Air.

## Required Files

Each voice profile needs:
1. **Audio file** (`*.wav`): Clean recording of the speaker
2. **Text file** (`*.txt`): Exact transcription of the audio

## Sample Voices

### Dave (British Male)
- **File**: `dave.wav`
- **Text**: "My name is Dave, and um, I'm from London."
- **Source**: NeuTTS Air samples directory
- **Download**: From [NeuTTS Air GitHub](https://github.com/neuphonic/neutts-air/tree/main/samples)

### Jo (Female)
- **File**: `jo.wav`
- **Text**: "Hello, I'm Jo. It's nice to meet you."
- **Source**: NeuTTS Air samples directory
- **Download**: From [NeuTTS Air GitHub](https://github.com/neuphonic/neutts-air/tree/main/samples)

### Zoe (Friendly Female)
- **File**: `zoe.wav`
- **Text**: "Hi there! I'm Zoe, your personal AI assistant. I'm here to help you with anything you need."
- **Note**: Create custom recording or use similar voice from NeuTTS Air samples

## Audio Requirements

For best voice cloning results, reference audio should be:

- **Format**: WAV file (mono channel)
- **Sample rate**: 16-44 kHz
- **Duration**: 3-15 seconds
- **Quality**: Clean speech, minimal background noise
- **Content**: Natural, continuous speech (conversational tone)
- **Pauses**: Minimal pauses, good for capturing tone

## Setup Instructions

1. **Download sample audio files** from NeuTTS Air repository:
   ```bash
   cd /home/zoe/assistant/services/zoe-tts/samples
   wget https://raw.githubusercontent.com/neuphonic/neutts-air/main/samples/dave.wav
   wget https://raw.githubusercontent.com/neuphonic/neutts-air/main/samples/jo.wav
   ```

2. **Or create your own** "Zoe" voice:
   - Record a clean 5-10 second audio sample
   - Save as `zoe.wav` (mono, 24kHz recommended)
   - Ensure text matches exactly

3. **Restart the TTS service** to load the new voices:
   ```bash
   docker-compose restart zoe-tts
   ```

## Adding Custom Voices

You can add more system-wide voices by:

1. Place audio file: `samples/{name}.wav`
2. Create text file: `samples/{name}.txt`
3. Update `voice_manager.py` to include the new voice in `sample_voices` list
4. Restart service

## Testing Voices

Test a voice profile:

```bash
curl -X POST http://localhost:9002/synthesize \
  -H "Content-Type: application/json" \
  -d '{
    "text": "This is a test of the voice cloning system.",
    "voice": "dave"
  }' \
  --output test.wav
```












