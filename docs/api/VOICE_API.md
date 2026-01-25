# Voice API Reference

API documentation for Zoe's voice control system.

## Overview

The Voice API enables:
- Wake word detection
- Voice activity detection (VAD)
- Audio ducking during voice interaction
- Barge-in support for interruptions
- Real-time voice events via WebSocket

**Base URL**: `/api/voice`

## WebSocket API

### Connect

**WebSocket** `/api/voice/ws`

Real-time voice control connection.

**Query Parameters:**
- `session_id`: User session ID

### Messages from Client

#### Wake Word Detected

```json
{
  "type": "wake_word",
  "keyword": "hey zoe",
  "confidence": 0.95
}
```

#### Voice Activity

```json
{
  "type": "vad",
  "active": true,
  "confidence": 0.8
}
```

#### Manual Interrupt

```json
{
  "type": "interrupt",
  "source": "button"
}
```

#### State Request

```json
{
  "type": "state_request"
}
```

#### Ping

```json
{
  "type": "ping"
}
```

### Messages from Server

#### Ducking Update

```json
{
  "type": "ducking",
  "state": "ducked",
  "level": 0.2,
  "timestamp": "2024-12-27T12:00:00Z"
}
```

#### State Update

```json
{
  "type": "state",
  "barge_in": {
    "state": "listening",
    "vad_active": true,
    "last_voice_time": 1703678400.0
  },
  "ducking": {
    "state": "ducked",
    "level": 0.2,
    "target_level": 0.2,
    "reason": "wake_word"
  },
  "timestamp": "2024-12-27T12:00:00Z"
}
```

#### Interrupt Event

```json
{
  "type": "interrupt",
  "interrupt_type": "wake_word",
  "previous_state": "playing",
  "confidence": 0.95,
  "timestamp": "2024-12-27T12:00:00Z"
}
```

#### Wake Word Acknowledgment

```json
{
  "type": "wake_word_ack",
  "success": true,
  "keyword": "hey zoe"
}
```

#### Pong

```json
{
  "type": "pong"
}
```

## HTTP Endpoints

### Get Voice Configuration

**GET** `/api/voice/config`

Get configuration for browser-side voice detection.

**Response:**
```json
{
  "wake_word": {
    "keywords": ["hey zoe", "zoe"],
    "sensitivity": 0.5,
    "engine": "porcupine_wasm",
    "wasm_path": "/js/wakeword/",
    "model_path": "/js/wakeword/models/"
  },
  "vad": {
    "threshold": 0.5,
    "min_speech_ms": 100,
    "max_silence_ms": 1500
  },
  "ducking": {
    "level": 0.2,
    "fade_ms": 200
  }
}
```

### Get Voice State

**GET** `/api/voice/state`

Get current voice control state.

**Response:**
```json
{
  "barge_in": {
    "state": "idle",
    "vad_active": false,
    "last_voice_time": 0,
    "config": {
      "allow_during_speech": true,
      "allow_during_music": true
    }
  },
  "ducking": {
    "state": "normal",
    "level": 1.0,
    "target_level": 1.0,
    "reason": null
  }
}
```

### Trigger Interrupt

**POST** `/api/voice/interrupt`

Manually trigger an interrupt.

**Query Parameters:**
- `source`: Interrupt source (`button`, `api`)

**Response:**
```json
{
  "success": true,
  "source": "api",
  "previous_state": "playing"
}
```

### Start Ducking

**POST** `/api/voice/duck`

Start audio ducking.

**Query Parameters:**
- `reason`: Reason for ducking

**Response:**
```json
{
  "state": "ducked",
  "level": 0.2,
  "target_level": 0.2,
  "reason": "api"
}
```

### Stop Ducking

**POST** `/api/voice/unduck`

Stop audio ducking.

**Query Parameters:**
- `immediate`: Skip fade (boolean)

**Response:**
```json
{
  "state": "normal",
  "level": 1.0,
  "target_level": 1.0,
  "reason": null
}
```

### Set Zoe State

**POST** `/api/voice/set-state`

Set Zoe's current activity state.

**Query Parameters:**
- `state`: One of `idle`, `listening`, `processing`, `speaking`, `playing`

**Response:**
```json
{
  "success": true,
  "state": "listening"
}
```

## Zoe States

| State | Description |
|-------|-------------|
| `idle` | Not doing anything |
| `listening` | Actively listening to user |
| `processing` | Processing user request |
| `speaking` | Speaking response |
| `playing` | Playing music |

## Interrupt Types

| Type | Description |
|------|-------------|
| `wake_word` | User said wake word |
| `voice` | User started speaking |
| `button` | User pressed button/key |
| `manual` | API-triggered interrupt |

## Ducking States

| State | Description |
|-------|-------------|
| `normal` | Full volume |
| `ducked` | Reduced volume for voice |
| `muted` | Completely muted |

## Browser Integration

### VoiceController

The browser-side `VoiceController` handles:
- WebSocket connection management
- Wake word detection (WASM-based)
- Voice activity detection
- Audio element ducking

**Usage:**
```javascript
// Initialize
await window.VoiceController.init();

// Start wake word detection
await window.VoiceController.startWakeWordDetection();

// Register callbacks
window.VoiceController.onWakeWord((keyword, confidence) => {
  console.log(`Wake word: ${keyword}`);
});

window.VoiceController.onDucking((state, level) => {
  console.log(`Ducking: ${state} at ${level}`);
});

// Manual interrupt
window.VoiceController.triggerInterrupt('button');

// Stop detection
window.VoiceController.stopWakeWordDetection();
```

### Audio Ducking

The VoiceController automatically manages volume for registered audio elements:

```javascript
// Register audio element for ducking
const audio = document.querySelector('audio');
window.VoiceController.registerAudio(audio);

// Shared audio is auto-registered
// window.ZOE_SHARED_AUDIO is handled automatically
```

## Error Handling

Voice errors follow standard Zoe error format:

```json
{
  "error": "WAKE_WORD_ERROR",
  "message": "Failed to initialize wake word detection",
  "details": {},
  "retryable": true
}
```

### Error Codes

| Code | Description |
|------|-------------|
| `WAKE_WORD_ERROR` | Wake word detection failed |
| `SPEECH_RECOGNITION_ERROR` | Speech recognition failed |
| `TTS_ERROR` | Text-to-speech failed |
| `VOICE_ERROR` | Generic voice error |

