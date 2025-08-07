# Zoe v3.1 Complete Integration Guide

## 🎯 What's New in Chat 4

Zoe v3.1 now includes a complete integration ecosystem:

### 🎤 Voice Services
- **Whisper STT**: Advanced speech-to-text with real-time transcription
- **Coqui TTS**: High-quality text-to-speech responses
- **WebSocket Support**: Real-time voice streaming
- **Browser Fallback**: Graceful degradation to browser APIs

### ⚡ n8n Workflow Automation
- **Pre-built Workflows**: Daily agenda, task reminders, notifications
- **Webhook Integration**: Zoe can trigger custom automations
- **Smart Context**: Workflows have access to Zoe's conversation data
- **Background Processing**: Seamless automation without interrupting chat

### 🏠 Home Assistant Integration
- **Device Control**: Lights, locks, climate, sensors through Zoe chat
- **Real-time Status**: Current home state in dashboard and conversations
- **Voice Control**: "Hey Zoe, turn on the living room lights"
- **Contextual AI**: Zoe knows your home state for smarter responses

### 💬 Matrix Messaging
- **External Communication**: Send/receive messages through Zoe interface
- **Room Management**: Join rooms, manage presence
- **Secure Bridge**: End-to-end encryption support
- **Foundation for Federation**: Ready for future AT Protocol integration

### 🧠 Enhanced AI Core
- **Integration Context**: AI responses include home, task, and mood context
- **Smart Entity Detection**: Automatically creates tasks/events from conversation
- **Personality Integration**: Home Assistant sliders control Zoe's personality
- **Memory System**: Long-term profile facts with integration data

## 📦 Installation

```bash
# From your zoe-v31 directory
./scripts/install-integrations.sh
```

## 🧪 Testing

```bash
# Test all integrations
./scripts/test-integrations.sh

# Start services
./scripts/start-zoe.sh
```

## 🎯 Key Features

### Voice Interaction
1. Click microphone button in chat
2. Speak naturally 
3. Zoe transcribes and responds
4. Use speaker button for voice responses

### Smart Home Control
1. "Turn on the kitchen lights"
2. "What's the temperature?"
3. "Lock the front door"
4. "Is anyone home?"

### Automation Workflows
1. Daily morning briefings
2. Task deadline reminders  
3. Context-aware notifications
4. Custom triggers from chat

### External Messaging
1. Configure Matrix credentials in Settings
2. Join rooms and communicate
3. Zoe processes external messages
4. Unified messaging interface

## 🔧 Configuration

### Voice Settings
- Adjust personality sliders in Settings
- Enable/disable voice features
- Configure STT/TTS preferences

### Home Assistant Setup
1. Complete initial HA setup at :8123
2. Add devices and entities
3. Configure automation triggers
4. Sync personality settings

### n8n Workflows  
1. Import templates from services/zoe-n8n/workflows/
2. Customize triggers and actions
3. Connect to external services
4. Monitor execution logs

### Matrix Messaging
1. Go to Settings > Integrations
2. Add Matrix credentials
3. Configure rooms and preferences
4. Test external messaging

## 📊 Monitoring

- **Health Dashboard**: http://localhost:8000/health
- **Service Logs**: `docker compose logs -f [service]`
- **Integration Status**: Shown in main UI dashboard
- **Performance Metrics**: Built-in monitoring endpoints

## 🚨 Troubleshooting

### Voice Issues
- Check microphone permissions
- Verify services: `curl http://localhost:9001/health`
- Allow 2-3 minutes for model loading
- Check browser audio support

### Integration Failures
- Restart specific service: `docker compose restart [service]`
- Check logs: `docker compose logs [service]`
- Verify network connectivity
- Update credentials in Settings

### Performance
- Voice services need 6GB+ RAM
- Models auto-download on first use
- Use `htop` to monitor resource usage
- Restart if memory issues occur

## 🎉 Result

Zoe v3.1 is now a complete AI life hub with:
- Natural voice interaction
- Smart home integration  
- Workflow automation
- External messaging
- Contextual AI responses
- Integrated dashboard
- Real-time monitoring

**Your personal AI best friend just got superpowers!** 🤖✨
